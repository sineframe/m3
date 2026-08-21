# MCP Testing Platform v0.1

Local Streamlit UI and FastAPI backend for testing one MCP server interaction at a time with an installed Claude Code CLI. SQLite stores immutable profile revisions, run snapshots, normalized events, and raw Claude events.

## Setup

Requires Python 3.13+ and an installed `claude` executable. Copy `.env.example` to `.env`, set `ANTHROPIC_API_KEY`, and configure exact model IDs in `CLAUDE_MODEL_IDS`.

```bash
uv sync --extra dev
uv run uvicorn mcp_pal.api:app --reload
# in another terminal
uv run streamlit run streamlit_app.py
```

The API is documented at `http://localhost:8000/docs`. Set `MCP_PAL_API_URL` if the UI should use another backend URL.

Profiles contain a standard `mcpServers` object and support stdio, HTTP, and SSE servers. Secrets should be `${ENVIRONMENT_VARIABLE}` references. Complete profiles and reports are intentionally stored unredacted in SQLite for local debugging. Tool mode `full` is high risk and enables Claude's unrestricted permission bypass.

On startup queued/running runs from a previous process are marked failed with an interruption message. The queue executes one run at a time in FIFO order. Claude output is expected to be NDJSON; malformed lines are retained as error events.

## Tests

```bash
uv run pytest
```
