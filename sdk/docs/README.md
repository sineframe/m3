# M3 Python SDK

Use these pages to start writing tests for MCP servers and agent harnesses.
Tests are ordinary executable pytest tests; run them through the M3 CLI
for persisted results and an optional local UI, or invoke pytest directly.
Persisted pytest runs require a suite name on each selected test; direct
Python/notebook use and pytest without persisted results do not. See the
[quick start](quick-start.md#run-the-test) for markers and selection behavior.
Native harness choices include Claude Code, OpenCode, Codex App Server, and Pi
RPC; ACP remains the bring-your-own protocol path.
The project SDK and standalone CLI are separate installations; adding the SDK
to a project does not install the `m3` command or its bundled UI.

- [Streamable HTTP](http.md) — test a deployed MCP endpoint
  directly, through an agent session, or with a harness matrix.
- [Quick start](quick-start.md) — install the SDK, write the first real MCP
  test, and run it with the CLI or pytest.
- [Concepts](concepts.md) — understand server bindings, lifecycle, results,
  schemas, chained workflows, traces, and isolation.
- [Elicitation](elicitation.md) — write composable MRTR tests using real
  either/or, optional, same-round, and later-round examples.
- [Elicitation API reference](elicitation-api.md) — exact helpers, action
  boundaries, manual input, managed rounds, and trace fields.
- [Pi-to-Codex MRTR parity inventory](../tests/mrtr-harness-parity.md) —
  scenario-by-scenario test mapping, Codex evidence, and pending gaps.
- [Examples](examples.md) — choose the example matching a deployed endpoint,
  local stdio server, harness, ACP agent, or matrix workflow.
- [Evaluations](evaluations.md) — define evaluators, score repeated agent
  trials across harnesses, save results, and query pass-rate trends.

The copyable typed observability starting point is
[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py); it is
also linked from the [quick start](quick-start.md) and [concepts](concepts.md).

For harness-driven tool assertions, start with
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).

The examples run against the deterministic stdio server in
[`example_mcp_server.py`](../examples/servers/example_mcp_server.py). They use
only public M3 SDK APIs, so the same tests work with either runner.
