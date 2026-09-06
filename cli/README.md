# MCP Pal CLI

`mcp-pal` is a standalone test runner for projects that use the MCP Pal SDK.
The command is distributed with the production web UI, so users do not need a
checkout of this repository, Node.js, Vite, Streamlit, or `uv` in the project
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
gh release download vX.Y.Z --repo rishhavv/mcp-pal --pattern install.sh --output install.sh
sh install.sh
rm install.sh
```
On Windows:
```powershell
gh release download vX.Y.Z --repo rishhavv/mcp-pal --pattern install.ps1 --output install.ps1
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

After installing the CLI, project setup is one explicit command:

```sh
cd my-project
mcp-pal setup
mcp-pal doctor
mcp-pal test --ui
```

`mcp-pal setup` installs only `mcp-pal[pytest,storage]` into the project
environment. It selects `--python`, then an active `VIRTUAL_ENV` or
`CONDA_PREFIX`, then `.venv`, creating `.venv` when needed. It never installs
the CLI or bundled app there, never edits dependency manifests or lockfiles,
and verifies the exact SDK version and release checksum. If you recreate or
sync the environment, run setup again until PyPI publishing is available.

<details>
<summary>Advanced recovery: install the SDK wheel manually</summary>

This is only needed when setup is unavailable. Keep the exact release version
and remove the temporary download after installation:

```sh
VERSION=X.Y.Z
SDK_WHEEL="mcp_pal-${VERSION}-py3-none-any.whl"
mkdir -p .mcp-pal-download
gh release download "v${VERSION}" -R rishhavv/mcp-pal -p "$SDK_WHEEL" \
  -O ".mcp-pal-download/$SDK_WHEEL"
uv venv .venv
. .venv/bin/activate
uv pip install "mcp-pal[pytest,storage] @ ./.mcp-pal-download/$SDK_WHEEL"
rm -rf .mcp-pal-download
```

On Windows:

```powershell
$Version = 'X.Y.Z'
$SdkWheel = "mcp_pal-$Version-py3-none-any.whl"
New-Item -ItemType Directory -Force .mcp-pal-download | Out-Null
gh release download "v$Version" -R rishhavv/mcp-pal -p $SdkWheel -O ".mcp-pal-download/$SdkWheel"
if ($LASTEXITCODE -ne 0) { throw 'gh release download failed' }
uv venv .venv
. .venv\Scripts\Activate.ps1
uv pip install "mcp-pal[pytest,storage] @ ./.mcp-pal-download/$SdkWheel"
Remove-Item -Recurse -Force .mcp-pal-download
```
</details>

## Commands

There are three public commands:

```text
mcp-pal setup [options]
mcp-pal doctor
mcp-pal test [options] -- [pytest arguments]
```

There is no `mcp-pal ui` command. The UI is a mode of `mcp-pal test` because it
shows the runs produced by that test command.

### `doctor`

`doctor` reports the standalone CLI and project environment independently. A
missing project environment is a normal `not ready` result with `mcp-pal setup`
as remediation; invalid interpreters and invalid configuration are operational
errors. It also checks explicitly requested capabilities:

```sh
mcp-pal doctor
mcp-pal doctor --require storage:sqlite
mcp-pal doctor --python .venv/bin/python --project-root . --json
```

### `test`

The CLI runs pytest using the project Python. It discovers that Python in this
order:

1. `--python PATH`, when supplied;
2. the `VIRTUAL_ENV` Python;
3. the `CONDA_PREFIX` Python;
4. `<project-root>/.venv/bin/python` (or `Scripts/python.exe` on Windows);
5. `python3`, then `python` on `PATH`.

The selected environment must contain `pytest`, the SDK pytest plugin, SQLite
storage, and `mcp-pal` at exactly the same version as the CLI's SDK. Follow the
authenticated SDK-wheel download shown in [Install](#install), then install
the project dependency in its own environment (pre-PyPI):

```sh
uv venv .venv
. .venv/bin/activate
uv pip install "mcp-pal[pytest,storage] @ ./.mcp-pal-download/$SDK_WHEEL"
```

The default results database is `.mcp-pal/executions.sqlite` below the project
root. Override it when needed:

```sh
mcp-pal test --results-db /tmp/my-runs.sqlite -- -q tests
mcp-pal test --python .venv/bin/python -- -q --maxfail=1
```

Everything after `--` is passed to pytest unchanged. The CLI adds its storage
plugin and `--mcp-pal-results-db` option itself. In other words, SQLite
execution persistence is automatic whenever tests run through `mcp-pal test`;
tests run directly with pytest use in-memory SDK storage unless they pass an
explicit `SQLiteExecutionStore` or install the plugin and flag themselves.

The database records SDK executions, specifications, recorded events and
traces, sessions/turns, saved artifacts/evidence, and evaluations attached
to those executions. Direct SDK evaluations are saved only with
`store=SQLiteExecutionStore(path)`; `mcp-pal test` selects the equivalent
store through `--mcp-pal-results-db`. In-memory SDK storage is temporary.
Pytest item outcomes and ordinary assertion results are not saved. Aggregate
matrix/trial trends are calculated from saved evaluations with the SDK store or
the API v2 aggregate route; summary rows are not duplicated in SQLite. A
completed execution is a saved run record, not by itself a saved test-pass
result.

### `--ui`

Add `--ui` to keep a local viewer open after pytest finishes:

```sh
mcp-pal test --ui
```

This starts one FastAPI server and one loopback port. The production UI is
bundled inside the CLI wheel; Node.js, npm, and Vite are not run at runtime.
The command prints links like these:

```text
MCP-Pal UI: http://127.0.0.1:8000/history
Run: http://127.0.0.1:8000/playground/run/<runId>
```

The `<runId>` in the direct link is the same execution ID stored by the SDK
and returned by `/api/v2/executions`. The history and direct-run pages come
from that same origin. The CLI stays open so the browser can load results;
press Ctrl+C to stop it. A normal pytest failure still opens the UI and keeps
that pytest exit code. Collection/configuration errors, interruption, server
startup errors, and invalid configuration return an operational failure.

When stdout is an interactive terminal, the SDK plugin shows a compact test
progress bar. It is disabled for non-TTY output and for verbose pytest modes,
where pytest's normal output remains available.

The server binds only to `127.0.0.1`. It is intended for local use; do not
expose it through a public interface or reverse proxy without adding your own
authentication and network controls.

## Troubleshooting

- `project Python ... does not match`: install the same `mcp-pal` version as
  the CLI, including the `[pytest,storage]` extras, in the project environment.
- `pytest` or SQLite is missing: activate the selected project environment and
  download the matching SDK wheel with `gh release download` as shown above,
  then install `mcp-pal[pytest,storage] @ ./.mcp-pal-download/$SDK_WHEEL`.
- `port is already in use`: select another port with `--port 8123`.
- No UI bundle is available: reinstall the CLI release; development checkouts
  do not contain generated UI files.
- The UI shows no runs: confirm the test uses `MCPTestKit` and that the CLI
  results database is the same file passed to the API. Runs are written by
  the SDK's default storage plugin; the CLI never writes test results itself.
