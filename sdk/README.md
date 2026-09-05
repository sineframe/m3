# MCP Pal

MCP Pal is a Python SDK for direct MCP-server tests and agent-driven testing.

The package is currently `0.2.0a3`. The direct-testing SDK, multi-turn agent
runtime, harness adapters, policies, workspaces, canonical tracing, and the
SQLAlchemy-backed persistent store are implemented. Phase 13 still has open
cross-process and shared store-contract acceptance tests, tracked in
`plans/python-testing-sdk-v0.2.md`; the existing application remains available
until the planned API and UI migration.

Install the supported optional capabilities with:

```bash
uv add "mcp-pal[pytest]"
pip install "mcp-pal[pytest]"
```

The repository contains the current application README and executable
fixtures. The SDK source distribution includes the SDK tests, examples, docs,
and legal files needed for source validation.
