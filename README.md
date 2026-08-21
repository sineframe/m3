# MCP Testing Platform v0.1

Local Streamlit UI and FastAPI backend for testing one MCP server interaction at a time with Claude Code or OpenCode. SQLite stores immutable profile revisions, run snapshots, backend-normalized events, and raw harness events.

## Setup

Requires Python 3.13+ and at least one installed harness. Copy `.env.example` to `.env`, then configure either Claude Code (`ANTHROPIC_API_KEY`, `CLAUDE_MODEL_IDS`) or OpenCode (`OPENCODE_API_KEY`, provider-specific credentials such as `OPENROUTER_API_KEY`, and `OPENCODE_MODEL_IDS`). OpenCode also accepts credentials saved by `opencode auth login`; `OPENCODE_API_KEY` is the simplest reproducible setup. Model IDs use OpenCode's `provider/model` form.

```bash
just setup
# equivalent raw command: uv sync --extra dev
just api
# equivalent raw command: uv run uvicorn mcp_pal.main:app --reload
# in another terminal
just ui
# equivalent raw command: uv run streamlit run src/mcp_pal/ui/app.py
```

Run `just --list` to list recipes. Use `just setup` to install dependencies,
`just test` (or `uv run pytest -q`) for the full suite, and `just check` for
compile/import checks. Focused suites are available as `just test-unit` and
`just test-integration`.

The API is documented at `http://localhost:8000/docs`. Set `MCP_PAL_API_URL` if the UI should use another backend URL.

Profiles contain one harness-neutral `mcpServers` object and support stdio, HTTP, and SSE servers. The backend translates the selected server to each harness's native config. Secrets should be `${ENVIRONMENT_VARIABLE}` references. Complete profiles and reports are intentionally stored unredacted in SQLite for local debugging. Tool mode `full` is high risk and enables unrestricted automatic tool approval.

If the UI says “Backend connected · runner setup required”, the API is reachable
and profiles/history remain usable; run submission is disabled until the health
checks pass for the selected harness. Add the relevant key to `.env` and restart
the API (and check the harness executable, CLI flags, database path, and permissions as needed).

On startup queued/running runs from a previous process are marked failed with an interruption message. The queue executes one run at a time in FIFO order. Harness output is expected to be NDJSON; malformed lines are retained as error events. Claude and OpenCode have different native schemas, but harness adapters convert both into the same API event types (`assistant_text`, `thinking`, `tool_call`, `tool_result`, step/system/error events). The UI renders that canonical schema and never parses harness-native messages.

## Tests

```bash
just test
# equivalent raw command: uv run pytest -q
# focused equivalents:
# just test-unit        -> uv run pytest -q tests/unit
# just test-integration -> uv run pytest -q tests/integration
# just check            -> uv run python -m compileall -q src
```
