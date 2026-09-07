# MCP Pal Python SDK

Use these pages to start writing tests for MCP servers and agent harnesses.
Tests are ordinary executable pytest tests; run them through the MCP Pal CLI
for persisted results and an optional local UI, or invoke pytest directly.
The project SDK and standalone CLI are separate installations; adding the SDK
to a project does not install the `mcp-pal` command or its bundled UI.

- [Streamable HTTP](http.md) — test a deployed MCP endpoint
  directly, through an agent session, or with a harness matrix.
- [Quick start](quick-start.md) — install the SDK, write the first real MCP
  test, and run it with the CLI or pytest.
- [Concepts](concepts.md) — understand server bindings, lifecycle, results,
  schemas, chained workflows, traces, and isolation.
- [Examples](examples.md) — choose the example matching a deployed endpoint,
  local stdio server, harness, ACP agent, or matrix workflow.
- [Evaluations](evaluations.md) — define evaluators, save results, and query
  pass-rate trends across trials.

The copyable typed observability starting point is
[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py); it is
also linked from the [quick start](quick-start.md) and [concepts](concepts.md).

For harness-driven tool assertions, start with
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).

The examples run against the deterministic stdio server in
[`example_mcp_server.py`](../examples/servers/example_mcp_server.py). They use
only public MCP Pal SDK APIs, so the same tests work with either runner.
