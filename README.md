# MCP Testing Platform v0.1

Local Streamlit UI and FastAPI backend for testing one MCP server interaction at a time with Claude Code, OpenCode, or a custom ACP harness. SQLite stores immutable profile revisions, run snapshots, backend-normalized events, and raw harness events.

## Setup

The repository is a uv workspace whose published `mcp-pal` project lives under
`sdk/`. The SDK supports Python 3.10+; the current application is exercised on
Python 3.13+ and requires at least one
installed harness. Copy `.env.example` to `.env`, then configure either Claude
Code (`ANTHROPIC_API_KEY`, `CLAUDE_MODEL_IDS`) or OpenCode (`OPENCODE_API_KEY`,
provider-specific credentials such as `OPENROUTER_API_KEY`, and
`OPENCODE_MODEL_IDS`). OpenCode also accepts credentials saved by `opencode auth
login`; `OPENCODE_API_KEY` is the simplest reproducible setup. Model IDs use
OpenCode's `provider/model` form.

```bash
just setup
# equivalent raw command: uv sync --all-packages --all-extras --all-groups
just api
# equivalent raw command: uv run --project app uvicorn mcp_pal_app.main:app --reload
# in another terminal
just ui
# equivalent raw command: uv run --project app streamlit run app/src/mcp_pal_app/ui/app.py
```

Run `just --list` to list recipes. Use `just setup` to install dependencies for
both workspace projects, `just test` (or the two project-specific pytest
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
# PYTHONDONTWRITEBYTECODE=1 uv run --project app --group test pytest -q app/tests
# focused equivalents:
# just test-unit        -> runs both sdk/tests/unit and app/tests/unit
# just test-integration -> runs both sdk/tests/integration and app/tests/integration
# just check            -> compiles sdk/src and app/src, then imports mcp_pal_app
# just package-check    -> uv run --project sdk --all-extras python scripts/check_packaging.py
```
