# MCP Pal

MCP Pal is a Python SDK and local test-results viewer for MCP servers and agent
harnesses. The standalone `mcp-pal` CLI runs SDK-backed pytest tests, stores
their results through the SDK, and can serve the bundled FastAPI/UI viewer.
SQLite stores immutable run snapshots, normalized events, and raw harness
events.

## Setup

The repository is a uv workspace whose SDK project lives under `sdk/`. The SDK
supports Python 3.10+. The repository is private, so authenticate GitHub CLI
once before downloading a release (the token needs read access to repository
contents):

```sh
gh auth login
gh auth status
```

Download and run the installer from the exact release you want:

```sh
gh release download v0.2.0a2 --repo rishhavv/mcp-pal \
  --pattern install.sh --output install.sh
sh install.sh
rm install.sh
```

```powershell
gh release download v0.2.0a2 --repo rishhavv/mcp-pal `
  --pattern install.ps1 --output install.ps1
.\install.ps1
Remove-Item install.ps1
```

The installer prefers and is tested first with `uv`; its fallback creates a
dedicated virtual environment. Neither path modifies global Python modules or
the project's virtual environment. It uses the same authenticated `gh` session
to download each exact wheel and checksum. The `gh release download` command
only downloads files from an existing release; it does not create or modify a
release.

The project being tested still needs a matching SDK environment with pytest and
SQLite storage. Until PyPI publishing is added, download the exact SDK wheel
with `gh`, then install it into that project's environment:

```sh
VERSION=0.2.0a2
SDK_WHEEL="mcp_pal-${VERSION}-py3-none-any.whl"
mkdir -p .mcp-pal-download
gh release download "v${VERSION}" -R rishhavv/mcp-pal -p "$SDK_WHEEL" \
  -O ".mcp-pal-download/$SDK_WHEEL"
uv venv .venv
. .venv/bin/activate
uv pip install "mcp-pal[pytest,storage] @ ./.mcp-pal-download/$SDK_WHEEL"
```

On Windows, use the equivalent authenticated download and local wheel path:

```powershell
$Version = '0.2.0a2'
$SdkWheel = "mcp_pal-$Version-py3-none-any.whl"
New-Item -ItemType Directory -Force .mcp-pal-download | Out-Null
gh release download "v$Version" -R rishhavv/mcp-pal -p $SdkWheel -O ".mcp-pal-download/$SdkWheel"
if ($LASTEXITCODE -ne 0) { throw 'gh release download failed' }
uv venv .venv
. .venv\Scripts\Activate.ps1
uv pip install "mcp-pal[pytest,storage] @ ./.mcp-pal-download/$SdkWheel"
```

This wheel command is a pre-PyPI workflow; the release also includes matching
CLI and app wheels, which the installer handles for the standalone command.

For repository development, copy `.env.example` to `.env`, then configure either Claude
Code (`ANTHROPIC_API_KEY`, `CLAUDE_MODEL_IDS`) or OpenCode (`OPENCODE_API_KEY`,
provider-specific credentials such as `OPENROUTER_API_KEY`, and
`OPENCODE_MODEL_IDS`). OpenCode also accepts credentials saved by `opencode auth
login`; `OPENCODE_API_KEY` is the simplest reproducible setup. Model IDs use
OpenCode's `provider/model` form.

```bash
just setup
# equivalent raw command: uv sync --all-packages --all-extras --all-groups
# run the standalone CLI against the current project
uv run --project cli mcp-pal doctor
uv run --project cli mcp-pal test --ui -- -q
```

The CLI prints a localhost history link such as
`http://127.0.0.1:8000/history` and direct links such as
`http://127.0.0.1:8000/playground/run/<runId>`.

The legacy development UI remains available for work on the old Streamlit
surface. It requires the app's optional `legacy-ui` extra:

```bash
uv run --project app --extra legacy-ui streamlit run app/src/mcp_pal_app/ui/app.py
```

It is not the standalone CLI product. The FastAPI API can still be run directly
for backend development with `uv run --project app uvicorn
mcp_pal_app.main:app --reload`.

Run `just --list` to list recipes. Use `just setup` to install dependencies for
all three workspace projects, `just test` (or the project-specific pytest
commands) for the full suite, and `just check` for
compile/import checks. Focused suites are available as `just test-unit` and
`just test-integration`.

The API is documented at `http://localhost:8000/docs`. Set `MCP_PAL_API_URL` if the UI should use another backend URL.

To reset the disposable development database, use the exact confirmation
ordering `just CONFIRM=reset dev-db-reset`.

## Custom ACP harnesses

Custom harnesses use an immutable `mcp-pal.harness.v1` manifest. Store only
environment references (`"TOKEN": "${TEAM_TOKEN}"`), never credentials or
resolved paths. The executable is intentionally unsandboxed and requires an
explicit trust acknowledgment when creating a profile. The JSON Schema is at
[`sdk/src/mcp_pal/schemas/mcp-pal.harness.v1.schema.json`](sdk/src/mcp_pal/schemas/mcp-pal.harness.v1.schema.json).

```bash
uv run --project sdk mcp-pal-harness validate harness.json --check-local
# equivalent raw command: uv run --project sdk python -m mcp_pal.harness.cli validate harness.json --check-local
# equivalent just recipe: just harness-validate harness.json
uv run --project sdk mcp-pal-harness probe harness.json --kind protocol
# equivalent just recipe: just harness-probe harness.json
# full probes are opt-in and consume one harness/model turn against local echo:
uv run --project sdk mcp-pal-harness probe harness.json --kind full --transport stdio --mode-id default --session-config '{}'
# equivalent just recipe (use the mode/config values advertised by protocol probe):
# just harness-full-probe harness.json stdio default '{}'
```

The reference bridge demonstrates wrapping a deterministic structured,
non-ACP CLI without an API key or network access:

```bash
uv run --project sdk mcp-pal-reference-bridge \
  --target uv \
  --target-args-json '["run", "python", "-m", "mcp_pal.fixtures.structured_cli"]'
# equivalent raw command: uv run --project sdk python -m mcp_pal.bridge.reference ...
# equivalent just recipe: just reference-bridge
```

Its stdin/stdout is ACP NDJSON, and it launches the selected stdio MCP server
passed by `session/new`; it is a reference fixture, not a generic vendor CLI
adapter. For a real ACP agent, create a manifest pointing at the vendor's
official local bridge and run probes explicitly before submitting runs. Real
agent smoke tests are opt-in, local-only, and require the vendor executable
and its own authentication; no API keys are needed by the repository tests.

Profiles contain one harness-neutral `mcpServers` object and support stdio, HTTP, and SSE servers. The backend translates the selected server to each harness's native config. Secrets should be `${ENVIRONMENT_VARIABLE}` references. Claude/MCP trace payloads and downloaded reports redact detected credentials; profile revisions remain local configuration snapshots. Tool mode `full` is high risk and enables unrestricted automatic tool approval. Completed Claude reports show the configured transport (`stdio`, `http`, or `sse`) and a Braintrust-style waterfall.

The profile form starts with the public Excalidraw HTTP MCP server:

```json
{
  "mcpServers": {
    "excalidraw": {
      "type": "http",
      "url": "https://mcp.excalidraw.com/mcp"
    }
  }
}
```

Reports also expose a redacted, versioned harness-neutral `trace.mcp_calls` collection (`mcp.v1`) with selected-server tool, status, timing, arguments, result, harness, and transport. Claude and OpenCode include correlated, redacted wire request/response and server latency when their stdio, HTTP, or SSE capture is available; custom ACP runs use backend-normalized `acp.v1` traces with separate ACP and MCP evidence. Unmatched calls truthfully retain unavailable wire fields.

If the UI says “Backend connected · runner setup required”, the API is reachable
and profiles/history remain usable; run submission is disabled until the health
checks pass for the selected harness. Add the relevant key to `.env` and restart
the API (and check the harness executable, CLI flags, database path, and permissions as needed).

On startup queued/running runs from a previous process are marked failed with an interruption message. The queue executes one run at a time in FIFO order. Harness output is expected to be NDJSON; malformed lines are retained as error events. Claude and OpenCode have different native schemas, but harness adapters convert both into the same API event types (`assistant_text`, `thinking`, `tool_call`, `tool_result`, step/system/error events). The UI renders that canonical schema and never parses harness-native messages.

## Tests

```bash
just test
# equivalent raw commands:
# PYTHONDONTWRITEBYTECODE=1 uv run --project sdk --extra pytest pytest -q sdk/tests
# PYTHONDONTWRITEBYTECODE=1 uv run --project app --extra legacy-ui --group test pytest -q app/tests
# PYTHONDONTWRITEBYTECODE=1 uv run --project cli pytest -q cli/tests
# focused equivalents:
# just test-unit        -> runs both sdk/tests/unit and app/tests/unit
# just test-integration -> runs both sdk/tests/integration and app/tests/integration
# just check            -> compiles all three projects and imports the SDK, app, and CLI
# just package-check    -> uv run --project sdk --all-extras python scripts/check_packaging.py
# CI also runs the isolated CLI wheel/tool + project-venv gate on Ubuntu.
```

The manual live merge gate (`just live-ui-gate`) runs one real OpenCode API
request and a browser check against the UI. It is opt-in, may incur provider
cost, and has not passed as part of the ordinary local or CI test suite unless
you run it with the required credentials.

Because `rishhavv/mcppal-ui` is a private sibling repository, repository
maintainers must configure the `MCPPAL_UI_TOKEN` Actions secret with read-only
Contents access to that repository. Both the standalone CI gate and CLI release
workflow use it to check out the exact commit pinned in `cli/UI_REF`; the token
is not included in release artifacts.

## CLI releases

Pushing a version tag such as `v0.2.0a2` triggers the CLI release workflow. The
tag must exactly match the versions in the SDK, app, and CLI projects. After the
pinned UI build and isolated release gates pass, the workflow stores these
artifacts on the tag's GitHub Release:

- the matching SDK, app, and standalone CLI wheels;
- `SHA256SUMS` for those three wheels;
- the rendered `install.sh` and `install.ps1` bootstrap scripts.

A manual workflow dispatch builds and verifies the same release payload but
does not publish it. PyPI publication is intentionally deferred.
