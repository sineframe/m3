# Examples

The examples are ordinary pytest tests and use public MCP Pal APIs. The
example server is a local, stateful stdio MCP fixture exposing deterministic
tools, resources, and prompts across a real subprocess and protocol boundary.
The complete server is
[`example_mcp_server.py`](../examples/servers/example_mcp_server.py).

```bash
uv run --project sdk --extra pytest pytest -q sdk/examples/tests
```

## 1. Discover a direct server tool before calling it

Use `kit.direct` when testing an MCP server without an agent. The executable
[`test_quick_start.py`](../examples/tests/test_quick_start.py) uses the shared
[`example_server` fixture](../examples/tests/conftest.py), lists every page,
and checks the selected tool's description and input contract before calling:

```python
with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
    shipping_quote = next(
        tool for tool in client.list_all_tools() if tool.name == "shipping_quote"
    )
    assert shipping_quote.name == "shipping_quote"
    assert shipping_quote.description == "Calculate a deterministic shipping quote"
    assert list(shipping_quote.input_schema["required"]) == ["weight_kg", "zone"]
    assert set(shipping_quote.input_schema["properties"]) == {"weight_kg", "zone"}
    result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})
assert result.structured_content == {"amount": 9.0, "currency": "USD"}
```

For pagination and the broader direct protocol surface, see
[`test_direct_client_surface.py`](../examples/tests/test_direct_client_surface.py).

## 2. Prompt a harness and assert its MCP tool

Use this for agent workflows where a harness chooses and calls the server.
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py)
uses a deterministic local ACP process (not an LLM) and the real
[`example_mcp_server.py`](../examples/servers/example_mcp_server.py).

Define the harness, server binding, alias, and restrictive policy before
opening the session. This example uses the deterministic local ACP process
[`deterministic_acp_agent.py`](../examples/servers/deterministic_acp_agent.py),
which invokes the real example server and needs no credentials or network:

```python
import sys
from pathlib import Path
from mcp_pal import MCPTestKit, expect
from mcp_pal.types import ACPAgent, AgentExecutionSpec, RestrictiveToolPolicy, ServerBinding, StdioServer

examples = Path("sdk/examples")
server = StdioServer(
    name="example-mcp", command=sys.executable,
    args=(str(examples / "servers" / "example_mcp_server.py"),),
    cwd=str(examples),
)
spec = AgentExecutionSpec(
    harness=ACPAgent(
        model="deterministic-fixture",
        manifest={
            "command": sys.executable,
            "args": (str(examples / "servers" / "deterministic_acp_agent.py"),),
            "protocol": "acp", "protocol_version": 1,
        },
    ),
    servers=(ServerBinding(server=server, alias="example-mcp"),),
    tool_policy=RestrictiveToolPolicy(
        allowed_tools=("example-mcp:shipping_quote",)
    ),
)

with MCPTestKit(env={}) as kit:
    with kit.agent_session(spec) as session:
        turn = session.send("Get a local shipping quote")

expect(session.result).to_have_tool_call(
    "shipping_quote", turn=turn, server="example-mcp",
    arguments={"weight_kg": 2}, arguments_partial=True,
    result={"structured_content": {"currency": "USD"}},
    result_partial=True, status="success",
)
```

`ACPAgent` selects the local harness executable, `ServerBinding` gives the MCP
server its stable alias, and `RestrictiveToolPolicy` limits the tools the
harness may use. `session.send(prompt)` sends one user turn to that configured
harness, waits for its terminal `TurnResult`, and lets the harness select and
call allowed MCP tools. For a genuine provider, the opt-in
[`test_live_opencode.py`](../examples/tests/test_live_opencode.py) runs
OpenCode against the same server. It needs OpenCode and `OPENCODE_API_KEY`
and can incur provider charges.

## 3. Match arguments, results, status, counts, and choices

Use exact or partial arguments, result projections, status, `count`,
`min_count`, `max_count`, argument/call predicates, latency bounds, and
negative assertions:

```python
with kit.agent_session(spec) as session:
    turn = session.send("local")

# The session context has closed, so this is a finalized result.
expect(session.result).to_have_tool_call(
    "shipping_quote", arguments={"zone": "local"},
    arguments_partial=True, argument_predicate=lambda args: args["weight_kg"] > 0,
    predicate=lambda call: call["status"] == "success",
    min_count=1, max_count=1, max_latency_ms=30_000,
)
expect(session.result).to_not_have_tool_call("always_fails", turn=turn)
```

The complete harness assertions are in
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).
Text, content, structured-result, and broader matcher examples are in
[`test_assertions_snapshots_evaluations.py`](../examples/tests/test_assertions_snapshots_evaluations.py).
When comparing evidence sources explicitly, use `evidence="reported"` or
`evidence="any"`; the default matcher source is wire evidence.

## 4. Test multiple turns with `TurnResult` scoping

`session.send` returns a completed `TurnResult`. After the session closes,
pass that object directly to matchers or `TraceView.for_turn`:

```python
with kit.agent_session(spec) as session:
    first = session.send("local")
    second = session.send("regional")

# The session context has closed before accessing its finalized result.
expect(session.result).to_have_tool_call("shipping_quote", turn=first)
expect(session.result).to_have_tool_call("shipping_quote", turn=second)
assert session.result.trace_view.for_turn(first).tool_calls
```

See the two-turn implementation in
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).

## 5. Chain tool outputs

Keep one direct connection open when a later call consumes an earlier result:

```python
normalized = client.call_tool("normalize_customer", {"name": "Ada Lovelace"})
customer_id = normalized.structured_content["customer_id"]
created = client.call_tool("create_order", {"customer_id": customer_id, "item": "engine", "quantity": 2})
order = client.call_tool("get_order", {"order_id": created.structured_content["order_id"]})
assert order.structured_content["customer_id"] == customer_id
```

Complete test: [`test_chained_workflow.py`](../examples/tests/test_chained_workflow.py).

## 6. Test resources, prompts, errors, and contracts

Resources and prompts use the direct client:

```python
guide = client.read_resource("memory://testing-guide")
prompt = client.get_prompt("review_order", {"order_id": "order-042"})
```

See [`test_resources_and_prompts.py`](../examples/tests/test_resources_and_prompts.py)
for complete calls. For tool errors and schema contracts:

```python
with kit.direct(example_server, validate_schemas=True) as client:
    with pytest.raises(ModelValidationError):
        client.call_tool("shipping_quote", {"weight_kg": -1, "zone": "local"})
```

The error, validation, and parametrized contract cases are in
[`test_errors_and_contracts.py`](../examples/tests/test_errors_and_contracts.py).

## 7. Use async APIs

The async kit has the same typed result and direct-client shape:

```python
async with AsyncMCPTestKit(env={}) as kit:
    async with kit.direct(example_server) as client:
        result = await client.call_tool("shipping_quote", {"weight_kg": 3, "zone": "regional"})
assert result.structured_content["currency"] == "USD"
```

Complete async direct and evaluation examples are in
[`test_async_usage.py`](../examples/tests/test_async_usage.py) and
[`test_assertions_snapshots_evaluations.py`](../examples/tests/test_assertions_snapshots_evaluations.py).

## 8. Inspect and debug typed traces

After the direct client or agent session closes, inspect the finalized view:

```python
with kit.agent_session(spec) as session:
    turn = session.send("local")

# Finalized only: this is after the session context above.
view = session.result.trace_view
assert view.messages or view.tool_calls
assert view.for_server("example-mcp").tool_calls
assert view.for_turn(turn).tool_calls
assert view.summary.timing.duration_ms >= 0
```

[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py) covers
messages, reasoning, runtime metadata, filters, indexes, timing, availability
states, and bounded raw-evidence reads. Raw capture is optional but must be
configured and remains bounded. Lifecycle, protocol, process cleanup, and
cross-connection isolation are in
[`test_tracing_and_lifecycle.py`](../examples/tests/test_tracing_and_lifecycle.py).

## 9. Capabilities and probes

Ask the kit what is ready before using environment-dependent features:

```python
with MCPTestKit(env={}) as kit:
    report = kit.capabilities()
    assert report.readiness.ready
```

Transport and dependency probes are shown in
[`test_capabilities_and_probes.py`](../examples/tests/test_capabilities_and_probes.py).

## 10. Snapshots, evaluations, and grouped assertions

Assertions can be grouped and evaluations can be registered against a typed
result:

```python
with check() as checks:
    checks.expect(quote).to_have_text_containing("USD")
    checks.expect(quote).to_have_structured_content({"amount": 7.0, "currency": "USD"})
kit.register_evaluator("is-usd", lambda context: context.subject["structured_content"]["currency"] == "USD")
```

See [`test_assertions_snapshots_evaluations.py`](../examples/tests/test_assertions_snapshots_evaluations.py)
for snapshots, lifecycle/error matchers, artifacts, workspace assertions,
sync/async evaluators, and grouped checks.

## 11. Use a mock server

When the test owns the server contract, use the SDK test double:

```python
server = MockMCPServer(name="contract-example")

@server.tool
def shipping_quote(arguments: dict[str, object]) -> dict[str, str]:
    return {"currency": "USD"}
```

The complete setup, expectations, verification, and recording examples are in
[`test_mock_server_expectations.py`](../examples/tests/test_mock_server_expectations.py).

## 12. Persist and reopen optionally

In-memory storage is the default. Configure SQLite when durable history is the
behavior under test; close and reopen it, then retrieve the typed view and use
the public raw-evidence API for entries that expose an evidence reference.
The executable close/reopen example is
[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py).

```python
store = SQLiteExecutionStore("traces.sqlite")
with MCPTestKit(store=store, env={}) as kit:
    result = kit.run(spec)
execution_id = result.snapshot.execution_id
store.close()
reopened = SQLiteExecutionStore("traces.sqlite")
view = reopened.get_trace_view(execution_id)
reopened.close()
```
