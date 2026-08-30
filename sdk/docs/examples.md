# Examples

These are ordinary pytest tests using public MCP Pal APIs. The example server
is a small stateful stdio MCP program with deterministic tools, resources, and
prompts. It runs as a real subprocess, so these tests exercise the protocol
boundary too: [`example_mcp_server.py`](../examples/servers/example_mcp_server.py).

Run the local examples with:

```bash
uv run --project sdk --extra pytest pytest -q sdk/examples/tests
```

## 1. Discover a direct server tool before calling it

Use `kit.direct` when you want to test an MCP server without an agent. The
shared [`example_server` fixture](../examples/tests/conftest.py) points to the
server above. [`test_quick_start.py`](../examples/tests/test_quick_start.py)
lists all pages, checks the tool description and input shape, and then calls it:

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

For pagination and the other direct operations, see
[`test_direct_client_surface.py`](../examples/tests/test_direct_client_surface.py).

## 2. Use Claude Code or OpenCode

Choose a built-in harness when the test should send a prompt to an installed
Claude Code or OpenCode process. Both use the same `agent_session` API and may
call tools available to the harness. Start with the shared server definition and
choose one harness:

```python
import os
from mcp_pal import MCPTestKit, expect
from mcp_pal.types import (
    AgentExecutionSpec, ClaudeCode, OpenCode, SecretReference, ServerBinding, StdioServer,
)

def env_secret(name: str) -> SecretReference:
    return SecretReference(source="environment", name=name)

def test_agent_uses_shipping_quote(example_server: StdioServer) -> None:
    # Pick the installed built-in harness for this test:
    harness = ClaudeCode(
        model=os.environ["MCP_PAL_CLAUDE_MODEL"],
        credential_references={"ANTHROPIC_API_KEY": env_secret("ANTHROPIC_API_KEY")},
    )
    # Or swap in OpenCode (and set MCP_PAL_OPENCODE_MODEL):
    # harness = OpenCode(
    #     model=os.environ["MCP_PAL_OPENCODE_MODEL"],
    #     credential_references={"OPENCODE_API_KEY": env_secret("OPENCODE_API_KEY")},
    # )
    spec = AgentExecutionSpec(
        harness=harness,
        servers=(ServerBinding(server=example_server, alias="example-mcp"),),
    )
    with MCPTestKit(env={}) as kit:
        with kit.agent_session(spec) as session:
            turn = session.send("Use shipping_quote for a local quote")

    expect(session.result).to_have_tool_call(
        "shipping_quote", turn=turn, server="example-mcp", status="success"
    )
```

`ClaudeCode(...)` and `OpenCode(...)` are the built-in choices. The test sends
one prompt to the selected installed process; that process may choose and call
an available MCP tool. The genuine provider example is
[`test_live_opencode.py`](../examples/tests/test_live_opencode.py); it needs
OpenCode and `OPENCODE_API_KEY` and may incur provider charges. Claude Code
uses the same session flow when its CLI is installed and configured.
Tool restrictions are optional; add a policy later when a test needs tighter
control over available tools. Credentials are resolved at launch from the
referenced environment variables and are not stored by the SDK; child process
login state is not inherited from the parent environment.

## 3. Bring your own harness with ACP

Use ACP when you have an ACP-compatible agent of your own. The local fixture
below is just a credential-free example process, not a required way to build an
agent. It calls the real example server and reports the result through ACP.
The complete fixture is
[`deterministic_acp_agent.py`](../examples/servers/deterministic_acp_agent.py),
and the complete test is
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).

```python
import sys
from pathlib import Path
from mcp_pal import MCPTestKit, expect
from mcp_pal.types import (
    ACPAgent, AgentExecutionSpec, RestrictiveToolPolicy, ServerBinding, StdioServer,
)

examples = Path("sdk/examples")
server = StdioServer(
    name="example-mcp", command=sys.executable,
    args=(str(examples / "servers" / "example_mcp_server.py"),),
    cwd=str(examples),
)
policy = RestrictiveToolPolicy(
    allowed_tools=("example-mcp:shipping_quote",)
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
    tool_policy=policy,
)

with MCPTestKit(env={}) as kit:
    with kit.agent_session(spec) as session:
        turn = session.send("Use shipping_quote for a local quote")

expect(session.result).to_have_tool_call(
    "shipping_quote", turn=turn, server="example-mcp", status="success"
)
```

## 4. Match arguments, results, status, counts, and choices

Use exact or partial arguments, result projections, status, count bounds,
predicates, latency limits, and negative checks:

```python
with kit.agent_session(spec) as session:
    turn = session.send("local")

# The session has closed, so its result is ready for final assertions.
expect(session.result).to_have_tool_call(
    "shipping_quote", turn=turn, arguments={"zone": "local"},
    arguments_partial=True,
    argument_predicate=lambda args: args["weight_kg"] > 0,
    predicate=lambda call: call["status"] == "success",
    min_count=1, max_count=1, max_latency_ms=30_000,
)
expect(session.result).to_not_have_tool_call("always_fails", turn=turn)
```

The complete harness assertions are in
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).
For an evidence-source comparison, use `evidence="reported"` or
`evidence="any"`; wire evidence is the default.

## 5. Test multiple turns by `TurnResult`

Each `session.send(prompt)` returns one completed turn. After closing the
session, pass either `TurnResult`, `TurnSnapshot`, `TurnId`, or a string ID to
the matcher and `TraceView.for_turn`:

```python
with kit.agent_session(spec) as session:
    first = session.send("local")
    second = session.send("regional")

expect(session.result).to_have_tool_call("shipping_quote", turn=first)
expect(session.result).to_have_tool_call("shipping_quote", turn=second)
assert session.result.trace_view.for_turn(first).tool_calls
```

See the two-turn implementation in
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).

## 6. Chain tool outputs

Keep one direct client open when one tool feeds the next:

```python
normalized = client.call_tool("normalize_customer", {"name": "Ada Lovelace"})
customer_id = normalized.structured_content["customer_id"]
created = client.call_tool("create_order", {"customer_id": customer_id, "item": "engine", "quantity": 2})
order = client.call_tool("get_order", {"order_id": created.structured_content["order_id"]})
assert order.structured_content["customer_id"] == customer_id
```

Complete test: [`test_chained_workflow.py`](../examples/tests/test_chained_workflow.py).

## 7. Test resources, prompts, errors, and schemas

The direct client also handles resources and prompts:

```python
guide = client.read_resource("memory://testing-guide")
prompt = client.get_prompt("review_order", {"order_id": "order-042"})
```

See [`test_resources_and_prompts.py`](../examples/tests/test_resources_and_prompts.py).
For schema validation and expected tool errors, see
[`test_errors_and_contracts.py`](../examples/tests/test_errors_and_contracts.py):

```python
with kit.direct(example_server, validate_schemas=True) as client:
    with pytest.raises(ModelValidationError):
        client.call_tool("shipping_quote", {"weight_kg": -1, "zone": "local"})
```

## 8. Use async APIs

The async kit follows the same shape:

```python
async with AsyncMCPTestKit(env={}) as kit:
    async with kit.direct(example_server) as client:
        result = await client.call_tool("shipping_quote", {"weight_kg": 3, "zone": "regional"})
assert result.structured_content["currency"] == "USD"
```

See [`test_async_usage.py`](../examples/tests/test_async_usage.py) and the
async evaluation in
[`test_assertions_snapshots_evaluations.py`](../examples/tests/test_assertions_snapshots_evaluations.py).

## 9. Inspect and debug traces

After a client or session closes, inspect its typed view:

```python
with kit.agent_session(spec) as session:
    turn = session.send("local")

view = session.result.trace_view
assert view.messages or view.tool_calls
assert view.for_server("example-mcp").tool_calls
assert view.for_turn(turn).tool_calls
assert view.summary.timing.duration_ms >= 0
```

[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py) covers
messages, reasoning, runtime information, filters, indexes, timing, explicit
availability states, and bounded raw-evidence reads. Raw capture is optional,
must be configured, and is bounded. Process cleanup and connection isolation
are in [`test_tracing_and_lifecycle.py`](../examples/tests/test_tracing_and_lifecycle.py).

## 10. Check capabilities and probes

Check readiness before using an environment-dependent feature:

```python
with MCPTestKit(env={}) as kit:
    report = kit.capabilities()
    assert report.readiness.ready
```

See [`test_capabilities_and_probes.py`](../examples/tests/test_capabilities_and_probes.py).

## 11. Use snapshots, evaluations, and grouped assertions

Group related checks and register an evaluator against a typed result:

```python
with check() as checks:
    checks.expect(quote).to_have_text_containing("USD")
    checks.expect(quote).to_have_structured_content({"amount": 7.0, "currency": "USD"})
kit.register_evaluator("is-usd", lambda context: context.subject["structured_content"]["currency"] == "USD")
```

See [`test_assertions_snapshots_evaluations.py`](../examples/tests/test_assertions_snapshots_evaluations.py)
for snapshots, lifecycle/error checks, artifacts, workspace checks, and
sync/async evaluations.

## 12. Use a mock server

When the test owns the server behavior, use the in-process helper:

```python
server = MockMCPServer(name="contract-example")

@server.tool
def shipping_quote(arguments: dict[str, object]) -> dict[str, str]:
    return {"currency": "USD"}
```

Complete setup, expectations, verification, and recording are in
[`test_mock_server_expectations.py`](../examples/tests/test_mock_server_expectations.py).

## 13. Persist and reopen when needed

Memory storage is the default. Use SQLite when you want history to survive
process boundaries:

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

The close/reopen and public raw-evidence examples are in
[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py).
