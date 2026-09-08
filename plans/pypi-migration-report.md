# MCP Pal PyPI migration report

Status: proposed follow-up work  
Prepared: 2026-09-05  
Scope: publish the SDK and standalone CLI on PyPI, make the CLI own its FastAPI
viewer runtime, and preserve the CLI's two-environment architecture. The
internal `mcp-pal-app` distribution is deliberately excluded from PyPI.

## Executive conclusion

PyPI would remove nearly all of the installation friction that currently comes
from private GitHub Release assets.

The user-facing flow can become:

```sh
# Install the command in uv's isolated tool environment.
uv tool install "mcp-pal-cli==<version>"

# In the project being tested, install the matching SDK/plugin.
uv add --dev "mcp-pal[pytest,storage]==<version>"

mcp-pal test --ui -- tests/
```

There would be no `gh auth login`, `gh release download`, installer bootstrap,
manual wheel filename, `.mcp-pal-download` directory, or manually supplied
`--with` wheels. Package resolution would install `mcp-pal` automatically from
the CLI package's dependency metadata; the FastAPI viewer runtime would be part
of `mcp-pal-cli` itself. `uv tool install` would still isolate the CLI from both
global Python modules and the tested project, which is the behavior uv
documents for tools.

This does **not** collapse the required two environments. The CLI and viewer
runtime belong in uv's tool environment; the SDK pytest plugin must still be
installed in the project environment because that environment owns the tests
and their dependencies.

The go/no-go issue is visibility: PyPI does not support private packages. A
production PyPI publication makes both distributions and the compiled UI
inside the CLI distribution publicly downloadable. Keeping the GitHub source
repositories private does not change that. If MCP Pal must remain private, use
a private package index instead of pypi.org. See [PyPI's private-package
answer](https://pypi.org/help/#how-can-i-publish-my-private-packages-to-pypi).

## What PyPI simplifies

| Concern | Private GitHub Releases now | PyPI afterwards |
| --- | --- | --- |
| CLI installation | Authenticate `gh`, download installer, installer downloads three wheels | `uv tool install mcp-pal-cli==<version>` |
| SDK installation | Construct a wheel filename, download it, install its local path | `uv add --dev "mcp-pal[pytest,storage]==<version>"` |
| Dependency wiring | Installer explicitly supplies SDK and app wheels | CLI owns the viewer runtime; PyPI resolves the SDK dependency |
| Checksums | Custom `SHA256SUMS` download and verification | Index metadata supplies hashes; installers verify downloaded distributions |
| OS bootstrap scripts | Maintain and test shell and PowerShell implementations | Not required for the normal uv flow |
| Consumer authentication | Every developer needs private GitHub repository access | None for public PyPI packages |
| Upgrades | Download another exact installer/release | `uv tool upgrade mcp-pal-cli` or reinstall an exact version |
| Ephemeral use | Custom local release assets | `uvx --from mcp-pal-cli mcp-pal ...` |
| Publisher credentials | GitHub token or release permissions | GitHub OIDC Trusted Publishing; no long-lived PyPI API token |

PyPI does **not** simplify these parts:

- building the production UI and embedding it in the CLI distribution;
- checking out the private `mcppal-ui` repository during release builds;
- maintaining `MCPPAL_UI_TOKEN` while that sibling repository is private;
- running pytest with the tested project's Python;
- storing results in SQLite and serving `/api/v2` plus the bundled UI;
- keeping SDK and CLI versions compatible and publishing both projects;
- completing deterministic and live pre-release gates.

Official references:

- [uv tool environments are isolated from projects](https://docs.astral.sh/uv/guides/tools/)
- [uv package build and publish guidance](https://docs.astral.sh/uv/guides/package/)
- [Python packaging flow](https://packaging.python.org/en/latest/flow/)

## Current repository findings

MCP Pal currently builds three distributions, all at `0.2.0a2`:

| Directory | Current distribution | Purpose | PyPI target |
| --- | --- | --- | --- |
| `sdk/` | `mcp-pal` | SDK, pytest plugin, storage implementation | Public `mcp-pal` project |
| `app/` | `mcp-pal-app` | Existing FastAPI/API runtime used by the viewer | Not published; viewer runtime moves behind the CLI boundary |
| `cli/` | `mcp-pal-cli` | `mcp-pal` command and bundled UI | Public `mcp-pal-cli` project with the viewer runtime included |

As checked through PyPI's JSON endpoints on 2026-09-05, `mcp-pal` and
`mcp-pal-cli` both return HTTP 404. They appear unregistered, but this is not a
reservation or guarantee:
PyPI can reject names that are prohibited or too similar to another project,
and a pending Trusted Publisher does not reserve a name until the first upload.
See [PyPI name rules](https://pypi.org/help/#project-name) and [pending publisher
behavior](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

The build already uses `uv build --no-sources`, which is correct for testing
published metadata without workspace overrides. The current release builder,
however, produces wheels only. The Python Packaging User Guide recommends
publishing both an sdist and a wheel for each package.

Publication readiness differs by package:

- `sdk/pyproject.toml` already has a README, license, authors, keywords,
  classifiers, and project URLs.
- `cli/pyproject.toml` has a README and license but lacks authors, keywords,
  classifiers, and project URLs.
- `cli/pyproject.toml` currently depends on `mcp-pal-app==0.2.0a2`, and
  `cli/src/mcp_pal_cli/web.py` imports `create_app` and `Settings` from that
  package. That is why the current GitHub installer needs an app wheel. It is an
  implementation dependency, not evidence that `mcp-pal-app` should become a
  public product.
- The CLI currently uses the full application factory, not a small standalone
  viewer adapter. Removing the public app distribution therefore requires a
  real source-boundary refactor; merely deleting the dependency would break the
  viewer.

## Recommended publication shape

Publish exactly two public projects:

```text
mcp-pal-cli                 # primary user-facing product
└── mcp-pal[storage]==X     # public SDK dependency

mcp-pal                     # SDK/plugin installed in tested projects

mcp-pal-app                 # internal repository application; not on PyPI
```

`mcp-pal-cli` is the point of the migration and must be treated as the primary
release deliverable. Its wheel must contain the command, FastAPI viewer runtime,
and compiled UI. `mcp-pal` remains separate because pytest imports the SDK and
plugin from the tested project's environment.

Do not copy `mcp_pal_app` into the CLI only during a release build. That would
make ordinary source builds differ from release builds and would leave two
owners for the same import package. Extract the viewer-specific runtime into a
neutral internal import namespace such as `mcp_pal_viewer` that is physically
owned and shipped by the CLI distribution. Both `mcp_pal_cli` and the internal
repository app can consume that one implementation. The broader development
application, legacy UI, profile editing, and run-creation machinery must not be
pulled into the CLI unless the bundled viewer actually needs them.

Consequences:

1. Create PyPI projects only for `mcp-pal` and `mcp-pal-cli`.
2. Both must trust the same GitHub workflow identity.
3. Both versions must be advanced and released together during the alpha
   series.
4. The CLI is implemented, documented, and accepted first as the product. In
   the final upload job, upload the SDK immediately before the CLI so the CLI's
   exact dependency already exists when users install it. This operational
   ordering does not make the SDK the primary product.
5. A two-project release is not transactional. If publication stops halfway,
   retry the exact unchanged artifacts; do not rebuild the same version.

## One-time external setup

These steps happen outside the repository and cannot be completed by a code PR.

### 1. Confirm public distribution is acceptable

Explicitly approve public access to:

- SDK source and wheel contents;
- FastAPI viewer runtime included in the standalone CLI;
- standalone CLI source and wheel contents;
- the compiled production UI embedded in the CLI wheel and sdist;
- package metadata and dependency graph.

If any of these must remain private, stop and choose a private index. Standard
PyPI cannot provide authenticated/private package visibility.

### 2. Secure the PyPI maintainer account

Create or select the PyPI owner account, enable the account's required security
controls, and establish a recovery/offboarding process. Prefer a PyPI
organization if ownership should outlive one person's account.

### 3. Configure one pending Trusted Publisher per public package

For `mcp-pal` and `mcp-pal-cli`, configure a pending GitHub Actions publisher
with:

- owner: `mcppal`;
- repository: `mcp-pal`;
- workflow filename: the final publishing workflow filename, recommended
  `.github/workflows/release-pypi.yml`;
- environment: `pypi`.

Pending publishers allow the first trusted workflow run to create the project,
so no manual token upload is needed. They do not reserve names. Configure them
shortly before the first release and publish promptly.

Trusted Publishing exchanges GitHub's OIDC identity for a short-lived,
project-scoped PyPI token. No `PYPI_TOKEN` GitHub secret should be created. PyPI
documents that the GitHub publish job requires `id-token: write`; use that
permission only on the publish job. See [creating a project through
OIDC](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [using a Trusted Publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

### 4. Create the GitHub `pypi` environment

Create an environment named exactly `pypi`, matching the PyPI publisher
configuration. Restrict it to release tags where the repository plan supports
that policy. Add a required reviewer if the repository's GitHub plan supports
required reviewers for private repositories; GitHub documents plan-dependent
limitations for private repositories.

The environment is still useful without secrets because it scopes and records
the deployment identity. See [GitHub deployment environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).

## Required repository changes

### Packet 1: make the CLI own its viewer runtime

Files:

- `sdk/pyproject.toml`
- `app/pyproject.toml`
- `cli/pyproject.toml`
- `cli/src/mcp_pal_cli/web.py`
- the viewer API/factory modules currently under `app/src/mcp_pal_app/`
- affected app and CLI tests

Changes:

1. Inventory the UI's `/api/v2` calls and identify the smallest server surface
   needed for history and direct-run viewing.
2. Move or extract that viewer-specific FastAPI factory, v2 routes/projections,
   minimal settings, and lifecycle ownership into a neutral internal package
   such as `cli/src/mcp_pal_viewer/`. Include that package in the
   `mcp-pal-cli` distribution and preserve the existing `/api/v2` wire contract
   exactly.
3. Do not duplicate the runtime at build time. There must be one authoritative
   source implementation used by source tests and release builds.
4. Keep broader application-only behavior under `app/`. Make the internal app
   consume `mcp_pal_viewer` where it needs the same v2 surface, so there is one
   implementation and no dependency from viewer code back into `mcp_pal_app`.
   The app may depend on the CLI workspace project for development; it is still
   excluded from the publish artifact set.
5. Remove `mcp-pal-app` from `cli`'s published dependencies. Add the runtime
   dependencies actually used by the CLI, including FastAPI/uvicorn and any
   required settings or SQLAlchemy packages, with tested bounds.
6. Keep an exact same-version `mcp-pal[storage]` dependency during alpha.
7. Add complete `authors`, `keywords`, `classifiers`, and `[project.urls]` to
   the CLI metadata.
8. Ensure both public distributions contain the Apache license and declare it
   in metadata.
9. Retain `[tool.uv.sources]` workspace entries for repository development, but
   continue verifying builds with `--no-sources` so published metadata resolves
   only from an index. uv explicitly recommends `--no-sources` for this check.
10. Validate README rendering and metadata for both public distributions before
    upload.

Acceptance invariants:

- installing only the CLI wheel plus its SDK dependency provides
  `mcp-pal test --ui`;
- `importlib.util.find_spec("mcp_pal_app")` is false in the clean CLI tool
  environment;
- `/api/v2`, SQLite reads, UI routing, loopback-only hosting, same-origin
  protection, and secret redaction behave exactly as before;
- the app test suite remains green even though the app is no longer a release
  dependency of the CLI.

The standard `pyproject.toml` fields become the metadata PyPI and installers
consume; see the [pyproject metadata specification](https://packaging.python.org/en/latest/specifications/pyproject-toml/).

### Packet 2: produce the exact PyPI distribution set

Files:

- `scripts/build_cli_release.py` (prefer renaming to a neutral
  `scripts/build_release.py` in this follow-up)
- `cli/tests/test_release_build.py`
- `scripts/smoke_cli_uv_install.py`
- `scripts/check_cli_standalone.py`

Changes:

1. Continue building the UI from the commit pinned by `cli/UI_REF`.
2. Continue staging the UI into a temporary copy of `cli/`; never commit build
   output.
3. Build both wheel and sdist for the SDK and CLI with `uv build --no-sources`.
   Continue testing the internal app from source, but do not add its artifacts
   to the publish directory.
4. Expect exactly four Python distribution files:
   two `py3-none-any.whl` files and two `.tar.gz` sdists.
5. Inspect metadata from both formats and require identical name, version,
   Python requirement, and dependency relationships.
6. Require the CLI wheel and CLI sdist to contain the bundled production UI.
7. Build a wheel from each of the two sdists in isolation and compare its
   important metadata and packaged UI contract with the directly built wheel.
8. Reject source maps and development-only UI files as the current gate does.
9. Keep the current exact-version and forbidden-mandatory-dependency checks.
10. Keep a clean output directory invariant so stale artifacts can never be
    uploaded accidentally.

Publishing wheel plus sdist follows the [PyPA package format
recommendation](https://packaging.python.org/en/latest/discussions/package-formats/).

### Packet 3: separate build authority from publish authority

Files:

- new `.github/workflows/release-pypi.yml`, or a carefully reworked
  `.github/workflows/release-cli.yml`
- `.github/workflows/ci.yml`
- workflow contract tests under `cli/tests/`

Recommended workflow design:

```text
version tag
    |
    v
build-and-test (contents: read; private UI token only)
    |
    +-- build pinned UI
    +-- build four immutable distributions
    +-- run deterministic isolated gates
    +-- upload one GitHub Actions artifact
    |
    v
publish-pypi (environment: pypi; id-token: write only)
    |
    +-- download the already-tested artifact
    +-- publish SDK, then CLI
    |
    v
verify-index (contents: read; no publish authority)
    |
    +-- wait for exact versions on PyPI
    +-- install from PyPI into disposable environments
    +-- run smoke and standalone gates
    |
    v
github-release (contents: write)
```

Security and correctness requirements:

1. Only a pushed version tag may reach `publish-pypi`. A manual dispatch may
   build and verify but must not publish unless a separate explicit policy is
   approved.
2. Validate that the normalized tag version exactly matches both public project
   versions before building. The internal app version is not part of the PyPI
   publication contract.
3. Give `id-token: write` only to the publish job. Do not give that job the
   private UI checkout token or broad repository write access.
4. Make publishing depend on every deterministic gate.
5. Transfer the already-tested distributions between jobs; never rebuild in
   the privileged publish job.
6. Use PyPA's Trusted Publishing action or uv's supported Trusted Publishing
   flow. For the smallest authentication surface and automatic PyPI publish
   attestations, prefer `pypa/gh-action-pypi-publish` and pin the action to a
   reviewed immutable commit SHA, consistent with this repository's action
   policy.
7. Keep workflow concurrency non-cancelling for releases.
8. Publish the SDK immediately before the CLI, using the same artifact set.
   The CLI remains the primary product; dependency-first upload ordering only
   prevents a temporarily broken install. Document partial-publish recovery.
9. Generate a GitHub Release only after PyPI verification passes. GitHub can
   retain release notes and optionally attach the same Python distributions,
   but it is no longer the installation source.
10. Do not upload the UI source checkout. Only the UI files embedded in the
    Python distributions are release artifacts.

PyPI recommends a narrowly scoped publishing workflow because changing or
triggering a trusted workflow is security-equivalent to controlling a publish
credential. See the [Trusted Publisher security
model](https://docs.pypi.org/trusted-publishers/security-model/). The official
PyPA publishing action automatically produces supported publish attestations;
see [PyPI attestation guidance](https://docs.pypi.org/attestations/producing-attestations/).

### Packet 4: remove GitHub-only installation machinery

Files to remove after the first PyPI installation gate succeeds:

- `scripts/install.sh.in`
- `scripts/install.ps1.in`
- `scripts/render_cli_installers.py`
- installer-specific tests in `cli/tests/test_installers.py`
- the Windows job that only parses the PowerShell installer

Files to simplify:

- `.github/workflows/release-cli.yml`
- `scripts/smoke_cli_uv_install.py`
- release workflow tests
- release build tests

Behavior to remove:

- rendered OS-specific bootstrap assets;
- custom authenticated wheel downloads;
- `SHA256SUMS` as an installation requirement;
- `MCP_PAL_RELEASE_BASE_URL` and private-release download branches;
- fallback logic that manually creates a dedicated CLI virtual environment.

Do not remove the uv isolation test. Replace its local-wheel command with a
pre-publish local-artifact gate and add a post-publish exact-index gate.

If supporting users without uv is a product requirement, recommend `pipx
install mcp-pal-cli==<version>` as the isolated alternative. Do not restore a
global `pip install` recommendation or maintain a second custom environment
manager inside MCP Pal.

### Packet 5: rewrite installation documentation

Files:

- `README.md`
- `cli/README.md`
- `sdk/README.md` where relevant

Replace the private GitHub release instructions with:

```sh
# Install the CLI without modifying this project's environment.
uv tool install "mcp-pal-cli==<version>"

# Add the SDK, pytest plugin, and SQLite storage to the tested project.
uv add --dev "mcp-pal[pytest,storage]==<version>"

# Verify environment discovery, then run tests and open the viewer.
mcp-pal doctor
mcp-pal test --ui -- tests/
```

Also document:

- `uv tool upgrade mcp-pal-cli`;
- `uv tool uninstall mcp-pal-cli`;
- `uvx --from mcp-pal-cli mcp-pal ...` for ephemeral use;
- the reason the CLI and SDK intentionally occupy different environments;
- how non-uv projects install `mcp-pal[pytest,storage]` into their own venv;
- that exact CLI and SDK versions must match during the alpha series;
- that pre-releases may require an explicit version until a stable release is
  available;
- the package names versus command name (`mcp-pal-cli` provides `mcp-pal`).

Delete all normal-user references to:

- `gh auth login`;
- `gh release download`;
- installer shell files;
- wheel filenames;
- `.mcp-pal-download`;
- `MCP_PAL_RELEASE_BASE_URL`;
- manually supplying SDK/app wheels to `uv tool install`.

## Test and release gates

### Required before publication

The pre-publication gate must run against the exact files later uploaded:

1. Existing SDK, examples, app, and CLI suites on Python 3.10 and 3.11.
2. UI tests, typecheck, lint, and production build from the pinned UI commit.
3. Metadata validation for all four public distributions.
4. Build-from-sdist verification for the SDK and CLI.
5. CLI install into disposable `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` using only
   the local release directory.
6. SDK install into a separate disposable dummy project environment.
7. A real SDK-authored pytest test with forced SQLite storage.
8. `/api/v2` report verification and bundled history/direct-run UI checks.
9. Secret-leak checks and the manual live OpenCode plus browser gate.
10. A source scan proving no local path, workspace source, private GitHub URL,
    installer-only setting, or source checkout is required at runtime.

### Required immediately after publication

Run from a fresh temporary directory and give uv temporary cache/tool paths so
the gate cannot pass from a developer's existing installation:

1. Poll PyPI until both exact versions appear, with a bounded timeout.
2. Run `uv tool install "mcp-pal-cli==<version>"` using PyPI only.
3. Assert the installed CLI and SDK versions both equal the tag version, and
   assert that no separately installed `mcp-pal-app` distribution or
   `mcp_pal_app` import package is present.
4. Assert neither public installation has local-directory or direct-URL
   provenance.
5. Create a separate dummy project and install
   `mcp-pal[pytest,storage]==<version>` from PyPI only.
6. Run the existing standalone dummy test, SQLite/API checks, and bundled UI
   browser checks.
7. Run a small Windows install/command smoke test as well as Linux. The wheels
   are pure Python, but command shims and path handling remain platform-specific.

This post-publish check cannot protect against publishing a bad immutable
version; that is why the local-artifact gate remains the real merge/release
gate. The post-publish check proves index availability and resolver behavior.

TestPyPI is optional rather than the primary correctness gate. Its dependency
population differs from PyPI, and testing packages with ordinary dependencies
often requires a second production index. The PyPA guide documents that setup,
but the exact local-artifact gate avoids ambiguity over which copy of the two
public MCP Pal packages was installed. See [Using
TestPyPI](https://packaging.python.org/en/latest/guides/using-testpypi/).

## Versioning and failure recovery

1. Keep one shared public version across SDK and CLI while exact cross-package
   pins are used. The internal app is not part of the PyPI version contract.
2. Make the version/tag consistency check mandatory and run it before any
   upload.
3. Never rebuild different bytes for a partially published version.
4. If an upload partially succeeds, retry the original artifact set. uv's
   publishing documentation states that identical existing PyPI files can be
   ignored safely during a retry.
5. If published behavior is bad, yank the affected release and publish a new
   version. Do not treat deletion and replacement as a rollback strategy.
6. For the first migration release, choose a version that has never been
   published. `0.2.0a2` is currently unused on PyPI and GitHub, but use the next
   version if `0.2.0a2` is released through the current GitHub-only path first.
7. Keep exact alpha pins. Revisit compatible ranges only after the packages
   have an explicit compatibility and deprecation policy.

## Risks and decisions

| Risk | Severity | Treatment |
| --- | --- | --- |
| Accidentally publishing private code/UI publicly | Critical | Explicit visibility approval before creating publishers |
| Package names claimed before first publication | High | Configure pending publishers shortly before a fully gated first release and publish promptly |
| Workflow compromise grants publish authority | High | Dedicated workflow/environment, immutable action pins, job-level `id-token: write`, protected release flow |
| Partial two-package publication | High | Build once, publish SDK immediately before CLI, retain exact artifacts, documented identical retry |
| Internal app boundary leaks into public packaging again | High | Gate CLI metadata against `mcp-pal-app` and gate the clean environment against `mcp_pal_app` |
| CLI resolves an incompatible SDK | High | Same public version and exact CLI-to-SDK pin during alpha |
| sdist omits bundled UI or differs from wheel | High | Inspect both, rebuild wheel from sdist, run UI gate against published shape |
| Developer environment makes smoke test pass falsely | Medium | Temporary uv cache/tool directories and dummy project; PyPI-only source assertions |
| Poor PyPI project pages or unclear package roles | Medium | Complete SDK/CLI metadata and clearly name CLI as the primary product |
| Pre-release not selected automatically | Medium | Document exact alpha version and test exact install command |
| UI private checkout still needs a PAT | Medium | Keep current read-only token or later replace it with a GitHub App |

## Recommended implementation order

1. Decide whether public distribution is acceptable.
2. Move the viewer runtime behind the CLI package boundary and remove the
   `mcp-pal-app` distribution dependency.
3. Prepare SDK/CLI metadata and extend the release builder to produce and
   validate four public distributions.
4. Add the split build/publish/verify workflow and contract tests.
5. Rewrite the docs to use index package names.
6. Run all deterministic and live pre-publication gates.
7. Create the two pending PyPI Trusted Publishers and GitHub `pypi`
   environment.
8. Push the approved new version tag.
9. Monitor SDK then CLI publication and run the clean PyPI install gate.
10. Only after that gate succeeds, remove the GitHub installer machinery in a
    cleanup PR. Keeping it for the first PyPI release gives one recovery path if
    PyPI setup fails; it should not remain as the documented primary path.

## Definition of done

The migration is complete only when all of these are true:

- only the `mcp-pal` and `mcp-pal-cli` names are created for this release, and
  both are owned by the intended PyPI account/organization;
- both public projects trust only the intended GitHub workflow/environment;
- a version tag builds one immutable, fully tested artifact set;
- the SDK and CLI wheel plus sdist are published with matching versions;
- the CLI distribution has no dependency on `mcp-pal-app`, contains the viewer
  runtime and compiled UI itself, and works without `mcp_pal_app` installed;
- `uv tool install mcp-pal-cli==<version>` succeeds from PyPI in clean Linux and
  Windows environments;
- a separate dummy project installs `mcp-pal[pytest,storage]==<version>` from
  PyPI and passes the SQLite/API/UI standalone gate;
- normal user documentation contains no GitHub authentication or wheel-download
  ceremony;
- no global Python environment is modified;
- the current pytest storage, progress, `/api/v2`, bundled UI, and `runId`
  contracts remain unchanged;
- the old installer path is either removed or clearly labeled as a temporary
  fallback, not the primary installation method.

## Primary sources

- [PyPI: private packages are unsupported](https://pypi.org/help/#how-can-i-publish-my-private-packages-to-pypi)
- [PyPI: create projects with pending Trusted Publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
- [PyPI: publish with GitHub OIDC](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
- [PyPI: Trusted Publisher security model](https://docs.pypi.org/trusted-publishers/security-model/)
- [PyPI: producing publish attestations](https://docs.pypi.org/attestations/producing-attestations/)
- [GitHub: deployment environments and protection rules](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [uv: building and publishing packages](https://docs.astral.sh/uv/guides/package/)
- [uv: installing isolated tools](https://docs.astral.sh/uv/guides/tools/)
- [PyPA: package formats](https://packaging.python.org/en/latest/discussions/package-formats/)
- [PyPA: `pyproject.toml` metadata](https://packaging.python.org/en/latest/specifications/pyproject-toml/)
- [PyPA: TestPyPI](https://packaging.python.org/en/latest/guides/using-testpypi/)
