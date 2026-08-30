# MCP Pal Python SDK

Use these pages to start writing pytest tests for MCP servers. The executable
tests in [`sdk/examples/tests`](../examples/tests/) are the source of truth for
every documented example; the documentation links to them instead of carrying
copies that can drift.

- [Quick start](quick-start.md) — install the SDK and run the first real MCP test.
- [Concepts](concepts.md) — understand server bindings, lifecycle, results,
  schemas, chained workflows, traces, and isolation.
- [Examples](examples.md) — browse every verified sync/async and finalized
  typed-trace scenario.

The copyable typed observability starting point is
[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py); it is
also linked from the [quick start](quick-start.md) and [concepts](concepts.md).

The examples run against the deterministic stdio server in
[`example_mcp_server.py`](../examples/servers/example_mcp_server.py). They use
only public MCP Pal SDK APIs and ordinary pytest tests.
