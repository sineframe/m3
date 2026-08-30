# MCP Pal Python SDK

Use these pages to start writing pytest tests for MCP servers and agent
harnesses. The examples are ordinary executable pytest tests linked from the
guide.

- [Quick start](quick-start.md) — install the SDK and run the first real MCP test.
- [Concepts](concepts.md) — understand server bindings, lifecycle, results,
  schemas, chained workflows, traces, and isolation.
- [Examples](examples.md) — choose direct-server testing, a built-in Claude
  Code/OpenCode harness, a bring-your-own ACP agent, or an opt-in live provider.

The copyable typed observability starting point is
[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py); it is
also linked from the [quick start](quick-start.md) and [concepts](concepts.md).

For harness-driven tool assertions, start with
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).

The examples run against the deterministic stdio server in
[`example_mcp_server.py`](../examples/servers/example_mcp_server.py). They use
only public MCP Pal SDK APIs and ordinary pytest tests.
