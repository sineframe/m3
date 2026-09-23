# Examples

Agent tests use `@pytest.mark.m3`; select models with CLI `--harness` and
repeat independent executions with `--trials N`. Use `kit.agents(...)` in
scripts and notebooks, and `ToolMatrix` for deterministic direct calls.
For a trusted native Codex test server, pass
`permission_policy=PermissionPolicy(mode="allow")` to `agent.run(...)` or
`agent.session(...)` so Codex's MCP tool approval can be answered. The default
permission policy denies it.

These are ordinary pytest tests using public M3 APIs. In your project,
run tests with `m3 test -- tests` and add `--ui` before `--` to inspect
recorded executions in the bundled local viewer. Direct pytest remains
supported. The local examples use deterministic tools, resources, and prompts.
The Streamable HTTP example uses an external DeepWiki endpoint and is
documented separately below. The local server is
[`example_mcp_server.py`](../examples/servers/example_mcp_server.py).

From this repository checkout, run the deterministic local examples directly
with:

```bash
uv run --project sdk --extra pytest pytest -q sdk/examples/tests
```

## 1. Test a deployed MCP endpoint with Streamable HTTP

Use `HTTPServer` for a deployed MCP endpoint. Start with the direct
contract path—initialize, discover tools, call a tool, and assert the typed
result—then use the finalized trace after client closure. The
[Streamable HTTP guide](http.md) explains the route. Its external
DeepWiki example,
[`test_streamable_http.py`](../examples/nondeterministic/test_streamable_http.py),
also demonstrates an OpenCode session; invoke that exact file when needed. It is outside the deterministic examples command.

## 2. Discover a direct local server tool before calling it

Write this test to catch regressions in a server's advertised tool contract and
result without involving an agent. Use `kit.direct` to connect to the server. The
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

## 3. Run one test across native harnesses

Define the server fixture your test needs, mark the test once, and select
harnesses and models when you run it. The executable local fixture is shown in
[`conftest.py`](../examples/tests/conftest.py):

```python
import pytest
from m3 import expect

@pytest.mark.m3
def test_agent_uses_shipping_quote(agent, example_server):
    result = agent.run(
        "Use shipping_quote for a 2 kg parcel in the local zone.",
        server=example_server,
    )
    expect(result).to_have_tool_call(
        "shipping_quote", server=example_server.name, status="success"
    )
```

```bash
m3 test --env-file .env \
  --harness opencode=opencode/big-pickle \
  --harness codex=gpt-5.6-sol --trials 2 -- tests/test_shipping.py
```

This creates four independent executions. The `.env` file supplies
`OPENCODE_API_KEY` and `OPENAI_API_KEY` when those providers use API keys;
native login remains available where supported. For Claude Code use
`ANTHROPIC_API_KEY`. OpenCode and Pi use the key for the provider prefix in the
model name. A custom provider can map a source variable with
`--credential-env VENDOR_API_KEY=MY_VENDOR_KEY`; scope it to one harness with
`opencode:VENDOR_API_KEY=MY_VENDOR_KEY`. MCP server credentials are separate.

To keep defaults in code, put
`@pytest.mark.m3(agents=[{"harness": "opencode", "models": ["opencode/big-pickle"]}])`
on the test. CLI selections replace those defaults.

## 4. Bring your own harness with ACP

An ACP-compatible agent uses the same list format. Its manifest specifies the
process and any environment-variable references:

```python
import sys
from pathlib import Path
from m3 import MCPTestKit, StdioServer, expect

examples = Path("sdk/examples").resolve()
example_server = StdioServer(
    name="example-mcp",
    command=sys.executable,
    args=[str(examples / "servers" / "example_mcp_server.py")],
    cwd=str(examples),
)
choices = [{
    "harness": "acp",
    "models": ["deterministic-example"],
    "manifest": {
        "schema_version": "m3.harness.v1",
        "protocol": "acp", "protocol_version": 1,
        "command": sys.executable,
        "args": [str(examples / "servers" / "deterministic_acp_agent.py")],
        "env": {},
    },
}]
with MCPTestKit() as kit:
    agent = kit.agents(choices)[0]
    result = agent.run("Use shipping_quote for a local quote", server=example_server)
    expect(result).to_have_tool_call("shipping_quote", status="success")
```

This local fixture needs no provider key. Set the environment variables named
by an external ACP manifest before running it. In a notebook, iterate over
`kit.agents(choices)` exactly as in ordinary Python.

## 5. Match arguments, results, status, counts, and choices

Use these assertions when the tool name alone is not enough: verify how it was
called, what it returned, how often it ran, and which tools it avoided:

```python
with MCPTestKit() as kit:
    agent = kit.agents(choices)[0]
    with agent.session(server=example_server) as session:
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

## 6. Test multiple turns by `TurnResult`

Use turn-scoped assertions to prove which prompt caused each tool call in a
multi-turn session. Each `session.send(prompt)` returns one completed turn.
After closing the session, pass either `TurnResult`, `TurnState`, `TurnId`,
or a string ID to the matcher and `TraceView.for_turn`:

```python
with MCPTestKit() as kit:
    agent = kit.agents(choices)[0]
    with agent.session(server=example_server) as session:
        first = session.send("local")
        second = session.send("regional")

    expect(session.result).to_have_tool_call("shipping_quote", turn=first)
    expect(session.result).to_have_tool_call("shipping_quote", turn=second)
    assert session.result.trace_view.for_turn(first).tool_calls
```

For a run that calls `shipping_quote` and `get_order`, use `to_have_tool_calls`
to check the complete list, including repeated calls. It checks the exact
sequence by default; set
`ordered=False` to accept any order while still requiring the same number of
each tool:

```python
expect(result).to_have_tool_calls(["shipping_quote", "get_order"])
expect(result).to_have_tool_calls(
    ["get_order", "shipping_quote"], ordered=False
)
```

The list uses wire-observed calls by default. Pass `server=`, `turn=`, or
`evidence="reported"` to select calls before comparing the full list.

See the two-turn implementation in
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).

## Elicitation examples

The maintained modern-protocol examples keep the server fixture and the M3
test code visibly separate: the server is
[`modern_mrtr_server.py`](../examples/servers/modern_mrtr_server.py), while
the six runnable test modules are:

- [`test_modern_mrtr_sdk.py`](../examples/tests/test_modern_mrtr_sdk.py) —
  direct SDK input-required handling and the explicit
  `allow_input_required=True` escape hatch.
- [`test_modern_mrtr_direct.py`](../examples/tests/test_modern_mrtr_direct.py)
  — a direct `call_tool(..., elicitation=plan)` retry with keyed responses.
- [`test_modern_mrtr_pi_qualified.py`](../examples/tests/test_modern_mrtr_pi_qualified.py)
  — a qualified automatic Pi elicitation operation.
- [`test_modern_mrtr_pi_unqualified.py`](../examples/tests/test_modern_mrtr_pi_unqualified.py)
  — the same automatic operation with an unqualified plan and prompt.
- [`test_modern_mrtr_pi_session.py`](../examples/tests/test_modern_mrtr_pi_session.py)
  — two turns, proving the plan belongs to the second `session.send` only.
- [`test_modern_mrtr_pi_composed.py`](../examples/tests/test_modern_mrtr_pi_composed.py)
  — one tool call with either/or, optional, or same-round address forms,
  followed by a URL round.

Run the deterministic direct/server example with plain pytest:

```bash
uv run --project sdk --extra pytest pytest -q \
  sdk/examples/tests/test_modern_mrtr_sdk.py \
  sdk/examples/tests/test_modern_mrtr_direct.py
```

The Pi files use the repository's deterministic fixture selection and are
collected by plain pytest; run them with the Pi 0.85.1 gate available. These
examples are the maintained reference for imports, fixture wiring, complete
assertions, and separation between server code and test code. They use only
public `agent` selection and session APIs.

Build an intuition for composing tests in the [Elicitation guide](elicitation.md).
The complete API inventory, signatures, response binding, action boundaries,
manual escape hatch, managed-input status, and trace assertions are in the
[MRTR API reference](elicitation-api.md). Keep server fixture code separate
from M3 test code as shown by the maintained files above.

## 7. Chain tool outputs

Write a chained test when later tools depend on earlier output or shared server
state. Keep one direct client open for the whole workflow:

```python
normalized = client.call_tool("normalize_customer", {"name": "Ada Lovelace"})
customer_id = normalized.structured_content["customer_id"]
created = client.call_tool("create_order", {"customer_id": customer_id, "item": "engine", "quantity": 2})
order = client.call_tool("get_order", {"order_id": created.structured_content["order_id"]})
assert order.structured_content["customer_id"] == customer_id
```

Complete test: [`test_chained_workflow.py`](../examples/tests/test_chained_workflow.py).

## 8. Test resources, prompts, errors, and schemas

Use direct tests to verify non-tool MCP surfaces and distinguish expected tool
errors from invalid contracts. The client also handles resources and prompts:

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

## 9. Use async APIs

Use the async API when the code under test is already async; it verifies the
same MCP behavior without a synchronous wrapper:

```python
async with AsyncMCPTestKit(env={}) as kit:
    async with kit.direct(example_server) as client:
        result = await client.call_tool("shipping_quote", {"weight_kg": 3, "zone": "regional"})
assert result.structured_content["currency"] == "USD"
```

See [`test_async_usage.py`](../examples/tests/test_async_usage.py) and the
async evaluation in
[`test_assertions_snapshots_evaluations.py`](../examples/tests/test_assertions_snapshots_evaluations.py).

## 10. Inspect and debug traces

Inspect a trace when you need evidence of what ran—not just whether the final
assertion passed. After a client or session closes, use its typed view:

```python
with MCPTestKit() as kit:
    agent = kit.agents(choices)[0]
    with agent.session(server=example_server) as session:
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

## 11. Check capabilities and probes

Probe readiness before an environment-dependent test so a missing runtime or
transport is diagnosed directly:

```python
with MCPTestKit(env={}) as kit:
    report = kit.capabilities()
    assert report.readiness.ready
```

See [`test_capabilities_and_probes.py`](../examples/tests/test_capabilities_and_probes.py).

## 12. Use snapshots, evaluations, and grouped assertions

Use these helpers for stable result snapshots, reusable quality checks, or
several failures reported together:

```python
with check() as checks:
    checks.expect(quote).to_have_text_containing("USD")
    checks.expect(quote).to_have_structured_content({"amount": 7.0, "currency": "USD"})
kit.register_evaluator("is-usd", lambda context: context.subject["structured_content"]["currency"] == "USD")
```

See [`test_assertions_snapshots_evaluations.py`](../examples/tests/test_assertions_snapshots_evaluations.py)
for snapshots, lifecycle/error checks, artifacts, workspace checks, and
sync/async evaluations.

## 13. Use a mock server

Use a mock server to test client behavior against expected MCP calls without
starting an external server process:

```python
server = MockMCPServer(name="contract-example")

@server.tool
def shipping_quote(arguments: dict[str, object]) -> dict[str, str]:
    return {"currency": "USD"}
```

Complete setup, expectations, verification, and recording are in
[`test_mock_server_expectations.py`](../examples/tests/test_mock_server_expectations.py).

## 14. Persist and reopen when needed

Use SQLite only when traces must be reopened from saved storage; otherwise
direct SDK and pytest use keeps executions in memory by default:

```python
store = SQLiteExecutionStore("traces.sqlite")
with MCPTestKit(store=store, env={}) as kit:
    agent = kit.agents(choices)[0]
    result = agent.run("Get a local shipping quote", server=example_server)
execution_id = result.snapshot.execution_id
store.close()
reopened = SQLiteExecutionStore("traces.sqlite")
view = reopened.get_trace_view(execution_id)
reopened.close()
```

The close/reopen and public raw-evidence examples are in
[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py).

The standalone CLI enables the same storage automatically for every
unconfigured kit used during its pytest process:

```bash
m3 test --results-db .m3/executions.sqlite -- tests
```

For direct pytest, opt into that default-store behavior explicitly when it is
more convenient than passing `store=` in test code:

```bash
pytest -p m3.pytest_plugin \
  --results-db .m3/executions.sqlite tests
```

This saved history contains executions, traces, sessions/turns, stored
artifacts/evidence, and explicitly attached `kit.evaluate()` records. When the
M3 pytest plugin is active, it also contains pytest item outcomes and MCP
Pal matcher evaluations. It does not turn arbitrary Python assertions into
evaluations or persist matrix/trial aggregate trends.

## 15. Combine servers, tools, harnesses, and trials

Use normal pytest parameterization for server and prompt variations. The
`agent` fixture adds one item for each selected harness/model/trial:

```python
import pytest
from m3 import expect

@pytest.mark.m3
@pytest.mark.parametrize("prompt,tool", [
    ("Get a local shipping quote for 2 kg", "shipping_quote"),
    ("Normalize Ada Lovelace", "normalize_customer"),
])
def test_tool_choice(agent, example_server, prompt, tool):
    result = agent.run(prompt, server=example_server)
    expect(result).to_have_tool_call(tool, status="success")
```

For two prompts, two `--harness` selections, and `--trials 2`, pytest collects
eight agent items. Each prompt keeps one logical case ID across harnesses and
trials. An explicit `case_id=...` on `agent.run` controls that identity in a
normal Python loop. Trials are measured repetitions, not retries.

### Compose ToolMatrix with agent selection

A ToolMatrix case describes a server and its owned tool. It can run a known
call directly with `case.run()`, or supply a server and prompt to a marked
agent test. The tool's prompt must be populated for the second form:

```python
import sys
from pathlib import Path
import pytest
from m3 import StdioServer, expect
from m3.matrix import ServerCase, ToolCase, ToolMatrix

_examples = Path("sdk/examples").resolve()
example_server = StdioServer(
    name="example-mcp", command=sys.executable,
    args=[str(_examples / "servers" / "example_mcp_server.py")],
    cwd=str(_examples),
)
matrix = ToolMatrix(servers=(ServerCase(
    name="catalog", server=example_server,
    tools=(
        ToolCase(name="shipping_quote", arguments={"weight_kg": 2, "zone": "local"}, prompt="Get a local shipping quote"),
        ToolCase(
            name="normalize_customer",
            arguments={"name": "Ada Lovelace"},
            prompt="Normalize Ada Lovelace",
        ),
    ),
),))

@matrix.parametrize()
def test_known_tool(case):
    result = case.run().direct_result
    assert result is not None
    assert not result.is_error
    if case.tool.name == "shipping_quote":
        assert result.structured_content == {"amount": 9.0, "currency": "USD"}
    else:
        assert result.structured_content == {"customer_id": "ada-lovelace"}

@pytest.mark.m3
@matrix.parametrize()
def test_agent_chooses_tool(case, agent):
    result = agent.run(case.tool.prompt, server=case.server)
    expect(result).to_have_tool_call(
        case.tool.name, server=case.server.name, status="success"
    )
```

The matrix's named tool does not restrict what the agent sees. Keep safe
alternatives available when testing the agent's choice.

### Use multiple servers or a continuing session

Pass `servers=[catalog_server, warehouse_server]` to `agent.run` when one
request needs both servers. For several turns, use
`with agent.session(servers=[...]) as session:` and call `session.send(...)`
for each turn. Inspect finalized evidence in `session.result.trace_view` after
the context exits. Claude Code's native server-scoped mode supports one bound
server; choose a harness that supports multiple servers for this example.

### Run without pytest

```python
from m3 import MCPTestKit

agents = [
    {"harness": "opencode", "models": ["opencode/big-pickle", "openai/gpt-5.6-sol"]},
    {"harness": "codex", "models": ["gpt-5.6-sol"]},
]
with MCPTestKit() as kit:
    for agent in kit.agents(agents, trials=2):
        result = agent.run("Find the shipping tool", server=example_server,
                           case_id="shipping-tool")
        print(agent.harness, agent.model, agent.trial,
              [call.tool.value for call in result.trace_view.tool_calls])
```

`run` waits for an `ExecutionResult`. For background execution, `submit`
returns an `ExecutionHandle`:

```python
handle = agent.submit("Find the shipping tool", server=example_server)
print(handle.snapshot().state)
result = handle.result(timeout=30)
# If the work is no longer needed while it is running, call handle.cancel().
```

Both methods accept `tools=None` by default, which leaves advertised MCP
tools available. `tools=[]` denies MCP tools.

### Complete examples and provider modes

The focused examples are executable references for each mode:

- [`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py) covers full tool evidence and trace assertions.
- [`test_matrix_usage.py`](../examples/tests/test_matrix_usage.py) covers ToolMatrix cases and selected agents.
- [`test_math_harness_matrix.py`](../examples/nondeterministic/test_math_harness_matrix.py) runs ten evaluated math cases with repeated trials.
- [`math_mcp_server.py`](../examples/servers/math_mcp_server.py) is the deterministic server used by that evaluation.
- [`test_streamable_http.py`](../examples/nondeterministic/test_streamable_http.py) covers the direct HTTP client and an opt-in native HTTP session.
- [`test_live_matrix_api.py`](../tests/e2e/test_live_matrix_api.py) covers opt-in native selections against a local stdio server.
- [`test_live_opencode.py`](../examples/tests/test_live_opencode.py) and [`test_live_codex_pi.py`](../tests/e2e/test_live_codex_pi.py) are opt-in provider examples.

The matrix example explains the difference between a logical case, a selected
harness/model configuration, and a trial. A multi-server session is one
continuing conversation with all supplied servers; an aggregate groups the
saved evaluation decisions by the requested metadata. These examples keep the
selection dictionaries and server definitions visible so they can be copied
into a normal Python program.

### Bring your own harness

For a harness you operate, provide an ACP selection dictionary with its
manifest. The complete deterministic ACP example is
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py);
it uses the local deterministic ACP process
[`deterministic_acp_agent.py`](../examples/servers/deterministic_acp_agent.py).
The same dictionary works in a normal Python program:

```python
import sys
from pathlib import Path
from m3 import MCPTestKit, StdioServer, expect

root = Path("sdk/examples").resolve()
server = StdioServer(
    name="example-mcp", command=sys.executable,
    args=(str(root / "servers" / "example_mcp_server.py"),), cwd=str(root),
)
choices = [{
    "harness": "acp",
    "models": ["deterministic-example"],
    "manifest": {
        "command": sys.executable,
        "args": [str(root / "servers" / "deterministic_acp_agent.py")],
        "protocol": "acp", "protocol_version": 1,
    },
}]
with MCPTestKit(env={}) as kit:
    result = kit.agents(choices)[0].run(
        "Use shipping_quote for a local quote", server=server,
    )
    expect(result).to_have_tool_call("shipping_quote", server=server.name, status="success")
```
