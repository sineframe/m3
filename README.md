# MCP Testing Platform v0.1

Local Streamlit UI and FastAPI backend for testing one MCP server interaction at a time with an installed Claude Code CLI. SQLite stores immutable profile revisions, run snapshots, normalized events, and raw Claude events.

## Setup

Requires Python 3.13+ and an installed `claude` executable. Copy `.env.example` to `.env`, set `ANTHROPIC_API_KEY`, and configure exact model IDs in `CLAUDE_MODEL_IDS`.

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

Profiles contain a standard `mcpServers` object and support stdio, HTTP, and SSE servers. Secrets should be `${ENVIRONMENT_VARIABLE}` references. Complete profiles and reports are intentionally stored unredacted in SQLite for local debugging. Tool mode `full` is high risk and enables Claude's unrestricted permission bypass.

If the UI says “Backend connected · runner setup required”, the API is reachable
and profiles/history remain usable; run submission is disabled until the health
checks pass. Add a missing `ANTHROPIC_API_KEY` to `.env` and restart the API (and
check the Claude executable, CLI flags, database path, and permissions as needed).

On startup queued/running runs from a previous process are marked failed with an interruption message. The queue executes one run at a time in FIFO order. Claude output is expected to be NDJSON; malformed lines are retained as error events.

## Tests

```bash
just test
# equivalent raw command: uv run pytest -q
# focused equivalents:
# just test-unit        -> uv run pytest -q tests/unit
# just test-integration -> uv run pytest -q tests/integration
# just check            -> uv run python -m compileall -q src
```
