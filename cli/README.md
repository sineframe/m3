# MCP Pal CLI

`mcp-pal` is a standalone test runner for projects that use the MCP Pal SDK.
The command is distributed with the production web UI, so users do not need a
checkout of this repository, Node.js, Vite, Streamlit, or `uv` in the project
being tested.

## Install

The repository is private, so authenticate GitHub CLI once before downloading a
release (the token needs read access to repository contents):

```sh
gh auth login
gh auth status
```

Download and run the installer from the exact release you want:

```sh
gh release download v0.2.0a3 --repo rishhavv/mcp-pal \
  --pattern install.sh --output install.sh
sh install.sh
rm install.sh
```

```powershell
gh release download v0.2.0a3 --repo rishhavv/mcp-pal `
  --pattern install.ps1 --output install.ps1
.\install.ps1
Remove-Item install.ps1
```

The installer prefers `uv tool install`, and falls back to a dedicated virtual
environment when `uv` is unavailable. Both paths keep the CLI and its app out
of the global Python installation and out of the project virtual environment.
It uses the authenticated `gh` session for the exact CLI, SDK, app, and
checksum assets. The `gh release download` command only downloads files from an
existing release; it does not create or modify a release.

Until PyPI publishing is added, install the matching SDK wheel into the
project's own virtual environment using an authenticated exact-asset download:

```sh
VERSION=0.2.0a3
SDK_WHEEL="mcp_pal-${VERSION}-py3-none-any.whl"
mkdir -p .mcp-pal-download
gh release download "v${VERSION}" -R rishhavv/mcp-pal -p "$SDK_WHEEL" \
  -O ".mcp-pal-download/$SDK_WHEEL"
uv venv .venv
. .venv/bin/activate
uv pip install "mcp-pal[pytest,storage] @ ./.mcp-pal-download/$SDK_WHEEL"
```

On Windows:

```powershell
$Version = '0.2.0a3'
$SdkWheel = "mcp_pal-$Version-py3-none-any.whl"
New-Item -ItemType Directory -Force .mcp-pal-download | Out-Null
gh release download "v$Version" -R rishhavv/mcp-pal -p $SdkWheel -O ".mcp-pal-download/$SdkWheel"
if ($LASTEXITCODE -ne 0) { throw 'gh release download failed' }
uv venv .venv
. .venv\Scripts\Activate.ps1
uv pip install "mcp-pal[pytest,storage] @ ./.mcp-pal-download/$SdkWheel"
```

## Commands

There are exactly two public commands:

```text
mcp-pal doctor
mcp-pal test [options] -- [pytest arguments]
```

There is no `mcp-pal ui` command. The UI is a mode of `mcp-pal test` because it
shows the runs produced by that test command.

### `doctor`

`doctor` checks the project Python and explicitly requested capabilities:

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
plugin and results database options itself.

### `--ui`

Add `--ui` to keep a local viewer open after pytest finishes:

```sh
mcp-pal test --ui -- -q
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
