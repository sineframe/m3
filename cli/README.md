# M3 CLI

Use `m3 test --env-file .env` to pass selected variables to pytest.
`--credential-env` maps agent harness credentials, or judge credentials with
the `judge:` scope. The judge reads `M3_JUDGE_API_KEY` by default; use
`LLMJudge(api_key_env=...)` to select another target variable.
`--judge-max-requests N` caps attempts including retries. Custom endpoints
require explicit `api_key_env` and
`response_mode`; loopback `auth="none"` reads no key and sends no
`Authorization` header. Judges use Chat Completions, so the endpoint and model
must support the selected response mode.

`m3` is a standalone test runner for projects that use the M3 SDK.
The command is distributed with the production web UI, so users do not need a
checkout of this repository, Node.js, Vite, or `uv` in the project
being tested.

## Install

The repository is private, so authenticate GitHub CLI once before downloading a
release (the token needs read access to repository contents):

```sh
gh auth login && gh auth status
```

Replace `X.Y.Z` with the exact release you want, then download and run its
installer:

```sh
gh release download vX.Y.Z --repo sineframe/m3 --pattern install.sh --output install.sh
sh install.sh
rm install.sh
```
On Windows:
```powershell
gh release download vX.Y.Z --repo sineframe/m3 --pattern install.ps1 --output install.ps1
.\install.ps1
Remove-Item install.ps1
```

The installer prefers `uv tool install`, and otherwise creates a dedicated
virtual environment. The machine-level CLI and bundled UI stay isolated from
global Python and from every project environment. Releases are currently
pre-releases, so select an explicit release tag.
The authenticated `gh` session downloads exact release assets; `gh release
download` only downloads files from an existing release and does not create or modify a release.

## Set up a project

After installing the CLI, initialize the project and answer its two short
questions:

```sh
cd my-project
m3 init
m3 setup
m3 doctor
# Replace the starter TODO with a real assertion and remove its skip.
m3 test --suite mcp-behavior -- tests/test_m3_starter.py
```

`init` defaults the project name to the repository name and the suite name to
`mcp-behavior`. It creates `m3.toml` (the stable project identity) and a
single skipped starter test at `tests/test_m3_starter.py`. Running `init`
also creates `.env.example` with blank `OPENCODE_API_KEY`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, and `M3_JUDGE_API_KEY` entries. Copy it to `.env` if you
do not already have one; otherwise add only the keys your tests need. Add
`.env` to `.gitignore` if needed and pass `--env-file .env` to `m3 test`. Running
`init` again preserves existing files and adds `.env.example` if it is
missing. The first skipped run confirms collection but exits 1 under `m3 test`
because no test executed. Replace the starter before using the run as a CI gate.

`m3 setup` installs `m3[pytest,storage,judge]` into the project
environment. It selects `--python`, then an active `VIRTUAL_ENV` or
`CONDA_PREFIX`, then `.venv`, creating `.venv` when needed. It never installs
the CLI or bundled app there, never edits dependency manifests or lockfiles,
and verifies the exact SDK version and release checksum. Judge support is
included. If you recreate or sync the environment, run setup again until PyPI
publishing is available. Rerunning setup also adds judge support to an older
project environment.

<details>
<summary>Advanced recovery: install the SDK wheel manually</summary>

This is only needed when setup is unavailable. Keep the exact release version
and remove the temporary download after installation:

```sh
VERSION=X.Y.Z
SDK_WHEEL="m3-${VERSION}-py3-none-any.whl"
mkdir -p .m3-download
gh release download "v${VERSION}" -R sineframe/m3 -p "$SDK_WHEEL" \
  -O ".m3-download/$SDK_WHEEL"
uv venv .venv
. .venv/bin/activate
uv pip install "m3[pytest,storage,judge] @ ./.m3-download/$SDK_WHEEL"
rm ".m3-download/$SDK_WHEEL"
rmdir .m3-download 2>/dev/null || true
```

On Windows:

```powershell
$Version = 'X.Y.Z'
$SdkWheel = "m3-$Version-py3-none-any.whl"
New-Item -ItemType Directory -Force .m3-download | Out-Null
gh release download "v$Version" -R sineframe/m3 -p $SdkWheel -O ".m3-download/$SdkWheel"
if ($LASTEXITCODE -ne 0) { throw 'gh release download failed' }
uv venv .venv
. .venv\Scripts\Activate.ps1
uv pip install "m3[pytest,storage,judge] @ ./.m3-download/$SdkWheel"
Remove-Item -Recurse -Force .m3-download
```
</details>

## Commands

There are four public commands:

```text
m3 init
m3 setup [options]
m3 doctor
m3 test [options] -- [pytest arguments]
```

There is no `m3 ui` command. The UI is a mode of `m3 test` because it
shows the runs produced by that test command.

### `init`

Run `m3 init` from the repository you want to test. It asks for the
project name, then the suite name, showing a default for each. It creates the
identity file, skipped pytest starter, and `.env.example` template. If the
identity and starter already exist, it reports the existing project and adds
the template only when missing. If one of those two required files is
missing or the identity is invalid, it reports the partial state for repair.

### `doctor`

`doctor` reports the standalone CLI and project environment independently. A
missing project environment is a normal `not ready` result with `m3 setup`
as remediation; invalid interpreters and invalid configuration are operational
errors. It also checks explicitly requested capabilities:

```sh
m3 doctor
m3 doctor --require storage:sqlite
m3 doctor --python .venv/bin/python --project-root . --json
```

### `test`

Agent selection flags:

| Flag | Meaning |
| --- | --- |
| `--harness KIND[@VERSION]=MODEL[,MODEL...]` | repeatable harness, optional version, and model selection |
| `--runtime=system\|managed` | use the installed harness (default) or the managed cache |
| `--harness-cache-dir PATH` | override the per-user harness cache for this run |
| `--trials N` | independent executions per combination |
| `--execution-timeout SECONDS` | full deadline for each selected execution; each case has its own deadline |
| `--env-file PATH` | explicitly load provider variables for the pytest child |
| `--credential-env [KIND:]TARGET=SOURCE` | map an agent credential; use `judge:TARGET=SOURCE` for the judge |
| `--suite NAME` or `--suite=NAME` | select tests whose inherited `m3` marker has this suite name |

Suite selection happens during pytest collection, before agent expansion. Put
the same existing marker in each file belonging to one suite:

```python
import pytest
pytestmark = pytest.mark.m3(suite_name="catalog")
```

Combine `--suite` with `--harness`, `--trials`, paths, `-k`, and `-m`; every
selector must match. An unknown suite exits 5 and a blank value exits 2. The
selected run ID is printed and can be passed to `--baseline RUN_ID`.

For example, two selections and two trials produce four agent executions:

```sh
m3 test --env-file .env --harness opencode=opencode/big-pickle \
  --harness codex=gpt-5.6-sol --trials 2 -- tests/test_shipping.py
```

Mark a test with `@pytest.mark.m3` and request the `agent` fixture. The
CLI supplies its harnesses and models. A marker may instead set defaults with
`@pytest.mark.m3(agents=[...], trials=2)`. CLI `--harness` replaces those
defaults; CLI `--trials` replaces the marker's trial count. Ordinary tests
without an `agent` fixture still run once. For an agent test, the item count is
ordinary pytest cases × selected harness/model choices × trials.

### Managed harness runtimes

The default `--runtime=system` launches the harness already installed on the
machine. A version selector requires `--runtime=managed`. With managed mode,
an unversioned harness requests `latest`; M3 resolves it once for the run and
shares that pinned version across pytest workers. Explicit versions are
reused until their verified cache entry is removed.

```sh
m3 test --runtime=managed \
  --harness opencode@1.18.30=opencode/big-pickle \
  --harness opencode@1.18.31=opencode/big-pickle \
  -- tests/test_shipping.py
```

Harness setup starts when the selected test runs. The test waits for the
requested release to be resolved, downloaded, checked, and installed, or for
an existing cache entry to be checked. The CLI prints resolution, download,
verification, and ready states; cache hits are shown as loaded from cache.
Setup failures fail the affected test. Each execution records the selected
model, requested harness selector, resolved harness version, target, and
download digest. Matrix reports distinguish different resolved versions.

The default cache root is `~/Library/Caches/m3/harnesses` on macOS,
`${XDG_CACHE_HOME:-~/.cache}/m3/harnesses` on Linux, and
`%LOCALAPPDATA%/m3/harnesses` on Windows (falling back to
`~/AppData/Local/m3/harnesses`). Entries are grouped by harness,
version, target, and digest. Set `M3_HARNESS_CACHE_DIR`, pass
`--harness-cache-dir PATH`, or use the SDK's `harness_cache_dir` argument to
override the root. The SDK constructor takes precedence for SDK calls; the
CLI flag takes precedence over the environment variable for CLI runs. Keep
the cache outside the tested repository and system executable directories.
M3 runs the cached executable by absolute path without a global install or
PATH change.

Inspect or remove cached releases with `m3 runtime cache list` and
`m3 runtime cache prune`. Pruning waits for active leases for up to 10 seconds
and returns an operational error if a release remains in use.

Known provider variable names are:

| Route | Variable name |
| --- | --- |
| OpenCode or Pi with `opencode/` models | `OPENCODE_API_KEY` |
| Codex, OpenCode, or Pi with `openai/` models | `OPENAI_API_KEY` |
| Claude Code, OpenCode, or Pi with `anthropic/` models | `ANTHROPIC_API_KEY` |
| Pi with `openai-codex/` models | `PI_CODING_AGENT_DIR` |
| `m3.judges.LLMJudge` | `M3_JUDGE_API_KEY` by default, or explicit `api_key_env` |

For example, put `OPENAI_API_KEY` for an agent using OpenAI and
`M3_JUDGE_API_KEY` for the judge in the same `.env` file, then run
`m3 test --env-file .env`. The judge does not fall back to the agent key.
If the judge key is already named `MY_JUDGE_KEY`, use
`--credential-env judge:M3_JUDGE_API_KEY=MY_JUDGE_KEY`. The explicit mapping
overrides `M3_JUDGE_API_KEY` for that test run. A missing source stops the run
before tests execute.

Custom providers use names only, for example
`--credential-env VENDOR_API_KEY=MY_VENDOR_KEY`; use
`--credential-env opencode:VENDOR_API_KEY=MY_VENDOR_KEY` to scope a mapping.
Unscoped mappings apply to harnesses, not judges. Only names appear in flags
and test code. `.env` is read only when
`--env-file` is supplied, and ambient variables take precedence. `doctor
--env-file` checks configuration and does not provide credentials to a later
test command.

The CLI runs pytest using the project Python. It discovers that Python in this
order:

1. `--python PATH`, when supplied;
2. the `VIRTUAL_ENV` Python;
3. the `CONDA_PREFIX` Python;
4. `<project-root>/.venv/bin/python` (or `Scripts/python.exe` on Windows);
5. `python3`, then `python` on `PATH`.

The selected environment must contain `pytest`, the SDK pytest plugin, SQLite
storage, judge support, and `m3` at exactly the same version as the CLI's SDK.
Run setup in the project to install these together:

```sh
m3 setup
```

The default results database is `.m3/executions.sqlite` below the project
root. Override it when needed:

```sh
m3 test --results-db /tmp/my-runs.sqlite -- -q tests
m3 test --python .venv/bin/python -- -q --maxfail=1
m3 test --baseline run-123 -- -q tests
```

`--baseline RUN_ID` is the only feedback comparison option. It validates the
explicit run ID in the selected results database before pytest starts. A run
through the plugin writes `.m3/reports/<run-id>/feedback.json`; the bundle
contains the saved test manifest and references to detailed executions and
catalogs. Existing pytest output, including test prints and logs, remains
diagnostic output and is not interpreted as a score.

Use `--execution-timeout 30` to bound startup, the harness turn, and cleanup for
each selected execution. A timeout writes a partial trace with the observed
stage and operation into the execution and trace JSON files under the feedback
bundle. `handle.result(timeout=...)` in SDK code remains a wait-only timeout.
Provider credentials still come from the child test environment: use
`--env-file .env` or ambient variables, and use `--credential-env TARGET=SOURCE`
when the provider variable has a project-specific name. Values are never put in
the timeout summary or feedback paths.

Everything after `--` is passed to pytest unchanged. The CLI adds its storage
plugin and `--results-db` option itself. In other words, SQLite
execution persistence is automatic whenever tests run through `m3 test`;
tests run directly with pytest use in-memory SDK storage unless they pass an
explicit `SQLiteExecutionStore` or install the plugin and flag themselves.

Profiles saved in the UI are stored in that same results database. A later
CLI-managed test can reference one directly; no additional CLI option is
needed. Use the profile ID shown by the UI and explicitly select the server
inside its MCP document:

```python
from m3 import MCPTestKit
from m3.types import RevisionSelection, ServerBinding, ServerProfileRef

saved_server = ServerBinding(
    profile=ServerProfileRef(
        profile_id="profile-from-ui",
        server_name="orders",
        revision=RevisionSelection(mode="latest"),
    )
)

def test_saved_server_profile():
    with MCPTestKit() as kit, kit.direct(saved_server) as client:
        assert client.list_all_tools()
```

At execution start, `latest` resolves to one immutable revision and its profile
and revision IDs are retained with the execution. Use a pinned
`RevisionSelection` when the test must name an exact revision. The same shared
store resolves `HarnessProfileRef` in agent specifications. This automatic
store selection applies to `m3 test` (including `--ui`); plain `pytest`
must be given the same `SQLiteExecutionStore` explicitly.

The database records SDK executions, specifications, recorded events and
traces, sessions/turns, saved artifacts/evidence, evaluations attached to
those executions, and internal pytest run records. Direct SDK evaluations are saved only with
`store=SQLiteExecutionStore(path)`; `m3 test` selects the equivalent
store through `--results-db`. In-memory SDK storage is temporary.
When the M3 pytest plugin is active, pytest item outcomes, phase
diagnostics, and execution associations are saved in internal run records.
M3 matcher checks are saved as evaluation records on their associated
executions. Ordinary `print()` and logging output remain diagnostic text; they
are never parsed into a score. Aggregate matrix/trial trends are calculated from
saved evaluations with the SDK store or the API v2 aggregate route. A
completed execution is still not by itself a saved test-pass result.

Each run also writes an agent-readable JSON bundle to
`.m3/reports/<run-id>/feedback.json`. Pass `--baseline RUN_ID` to add a
read-only comparison. The JSON is deterministic for the saved run, and normal
pytest results remain visible alongside the M3 run ID and feedback path.

### Control-plane upload status

`m3 test` only writes local results. It never uploads a report, even when
`M3_CONTROL_PLANE_URL` or `M3_CONTROL_PLANE_TOKEN` is present. There is no
`m3 upload` command yet. The CLI package contains a report uploader module
for a future explicit command; it is not registered with command dispatch.
That module prepares the same public v2 responses as the local app, sends the
feedback summary and complete current-run execution reports, then publishes
the run. The transport contract is documented in the control-plane repository.

### `--ui`

Add `--ui` to keep a local viewer open after pytest finishes:

```sh
m3 test --ui
```

This starts one FastAPI server and one loopback port. The production UI is
bundled inside the CLI wheel; Node.js, npm, and Vite are not run at runtime.
The command prints a home link and report links like this:

```text
UI: http://127.0.0.1:8000/#m3_token=<token>
Run: http://127.0.0.1:8000/reports/runs/<runId>#m3_token=<token>
```

The `<runId>` in the direct link is the pytest run ID stored in the test-run
manifest and returned by `/api/v2/feedback/<runId>`. The CLI stays open so the
browser can load results; press Ctrl+C to stop it. A normal pytest failure
still opens the UI and keeps that pytest exit code. Collection/configuration
errors, interruption, server startup errors, and invalid configuration return
an operational failure.

Open a link printed by the current CLI process to authorize the browser. The
browser removes the token fragment from its address bar and keeps the token in
the current tab's session storage for API requests. A new CLI launch uses a new
token, so old links stop working. The home link is printed even when the test
run saves no results. Treat the printed links as credentials while the server
is running.

When stdout is an interactive terminal, the SDK plugin shows a compact test
progress bar. It is disabled for non-TTY output and for verbose pytest modes,
where pytest's normal output remains available.

The server binds only to `127.0.0.1` and requires the launch token for API
requests. It is intended for local use; do not expose it through a public
interface or reverse proxy without suitable network controls.

## Troubleshooting

- `project Python ... does not match`: install the same `m3` version as
  the CLI, including the `[pytest,storage,judge]` extras, by running `m3 setup`
  in the project.
- `pytest`, SQLite, or judge support is missing: run `m3 setup` in the project
  to install the matching SDK and extras.
- `port is already in use`: select another port with `--port 8123`.
- No UI bundle is available: reinstall the CLI release; development checkouts
  do not contain generated UI files.
- The UI shows no runs: confirm the test uses `MCPTestKit` and that the CLI
  results database is the same file passed to the API. Runs are written by
  the SDK's default storage plugin; the CLI never writes test results itself.
