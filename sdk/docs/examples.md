# Examples

These are ordinary pytest tests using public MCP Pal APIs. In your project,
run tests with `mcp-pal test -- tests` and add `--ui` before `--` to inspect
recorded executions in the bundled local viewer. Direct pytest remains
supported. The local examples use a small stateful stdio MCP program with
deterministic tools, resources, and prompts. The Streamable HTTP example uses
an external DeepWiki endpoint and is documented separately below. The local
server runs as a real subprocess, so those tests exercise the protocol boundary:
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
also demonstrates an OpenCode session and `HarnessMatrix.each_tool`; invoke
that exact file when needed. It is outside the deterministic examples command.

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

## 3. Use a native harness with the local stdio server

Write this test to assert that an agent chooses and successfully calls the
intended MCP tool, rather than only checking its final prose. Choose a built-in
harness to send the prompt to an installed native process. Claude Code and
OpenCode are shown below; Codex and Pi use the same `AgentSpec` shape with
their persistent app-server/RPC adapters:

Codex uses its App Server JSON-RPC process. Pi uses native RPC and a private
MCP bridge extension because Pi does not expose MCP directly. Neither adapter
is routed through ACP. Codex supports stdio and Streamable HTTP MCP; Pi's
bridge supports stdio, SSE, and Streamable HTTP.

```python
import os
from mcp_pal import MCPTestKit, expect
from mcp_pal.types import (
    AgentSpec, ClaudeCode, Codex, OpenCode, Pi, SecretReference, ServerBinding, StdioServer,
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
    # Or use the native Codex app-server or Pi RPC adapters:
    # harness = Codex(
    #     model=os.environ["MCP_PAL_CODEX_MODEL"],
    #     credential_references={"OPENAI_API_KEY": env_secret("OPENAI_API_KEY")},
    # )
    # harness = Pi(
    #     model=os.environ["MCP_PAL_PI_MODEL"], provider="openai",
    #     credential_references={"OPENAI_API_KEY": env_secret("OPENAI_API_KEY")},
    # )
    spec = AgentSpec(
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

`ClaudeCode(...)`, `OpenCode(...)`, `Codex(...)`, and `Pi(...)` are the built-in native choices. The test sends
one prompt to the selected installed process; that process may choose and call
an available MCP tool. The nondeterministic OpenCode-over-HTTP example is
[`test_streamable_http.py`](../examples/nondeterministic/test_streamable_http.py);
it needs OpenCode and `OPENCODE_API_KEY` and may incur provider charges. Claude
Code uses the same session flow when its CLI is installed and configured.
Tool restrictions are optional; add a policy later when a test needs tighter
control over available tools. Credentials are resolved at launch from the
referenced environment variables and are not stored by the SDK; child process
login state is not inherited from the parent environment.

## 4. Bring your own harness with ACP

Use this test to make the same tool-selection assertion for an ACP-compatible
agent you provide. The local fixture below is just a credential-free example
process, not a required way to build an agent. It calls the real example server
and reports the result through ACP.
The complete fixture is
[`deterministic_acp_agent.py`](../examples/servers/deterministic_acp_agent.py),
and the complete test is
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py).

```python
import sys
from pathlib import Path
from mcp_pal import MCPTestKit, expect
from mcp_pal.types import (
    ACPAgent, AgentSpec, RestrictiveToolPolicy, ServerBinding, StdioServer,
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
spec = AgentSpec(
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

## 5. Match arguments, results, status, counts, and choices

Use these assertions when the tool name alone is not enough: verify how it was
called, what it returned, how often it ran, and which tools it avoided:

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

## 6. Test multiple turns by `TurnResult`

Use turn-scoped assertions to prove which prompt caused each tool call in a
multi-turn session. Each `session.send(prompt)` returns one completed turn.
After closing the session, pass either `TurnResult`, `TurnState`, `TurnId`,
or a string ID to the matcher and `TraceView.for_turn`:

```python
with kit.agent_session(spec) as session:
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
    result = kit.run(spec)
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
mcp-pal test --results-db .mcp-pal/executions.sqlite -- tests
```

For direct pytest, opt into that default-store behavior explicitly when it is
more convenient than passing `store=` in test code:

```bash
pytest -p mcp_pal.pytest_plugin \
  --mcp-pal-results-db .mcp-pal/executions.sqlite tests
```

This saved history contains executions, traces, sessions/turns, stored
artifacts/evidence, and explicitly attached `kit.evaluate()` records. When the
MCP Pal pytest plugin is active, it also contains pytest item outcomes and MCP
Pal matcher evaluations. It does not turn arbitrary Python assertions into
evaluations or persist matrix/trial aggregate trends.

## 15. Run a matrix across servers and harnesses

Use a matrix to apply one testing question consistently across several servers,
tools, or harnesses without hand-writing each pytest case. `HarnessMatrix`
tries the same prompt against several MCP servers and harnesses. In
`each_server` mode, every case contains one selected server, so a harness cannot
accidentally use a different server. The matrix below expands to four cases—two
servers × two built-in harnesses—with stable IDs such as `catalog/claude` and
`warehouse/opencode`:

```python
import os
from mcp_pal import expect
from mcp_pal.matrix import HarnessCase, HarnessMatrix, ServerCase, ToolCase
from mcp_pal.types import ClaudeCode, Codex, OpenCode, Pi, SecretReference

def env_secret(name: str) -> SecretReference:
    return SecretReference(source="environment", name=name)

catalog = ServerCase(
    name="catalog",
    server=example_server.model_copy(update={"name": "catalog"}),
    tools=(
        ToolCase(name="normalize_customer"),
        ToolCase(name="batch_total"),
        ToolCase(name="shipping_quote"),
    ),
)
warehouse = ServerCase(
    name="warehouse",
    server=example_server.model_copy(update={"name": "warehouse"}),
    tools=(ToolCase(name="shipping_quote"), ToolCase(name="get_order")),
)
harnesses = (
    HarnessCase(name="claude", harness=ClaudeCode(
        model=os.environ["MCP_PAL_CLAUDE_MODEL"],
        credential_references={"ANTHROPIC_API_KEY": env_secret("ANTHROPIC_API_KEY")},
    )),
    HarnessCase(name="opencode", harness=OpenCode(
        model=os.environ["MCP_PAL_OPENCODE_MODEL"],
        credential_references={"OPENCODE_API_KEY": env_secret("OPENCODE_API_KEY")},
    )),
)
matrix = HarnessMatrix.each_server(
    servers=(catalog, warehouse), harnesses=harnesses,
)

@matrix.parametrize()
def test_server_case(case):
    tool = case.server.tools[0].name
    prompt = {
        "catalog": "Use normalize_customer with name Ada Lovelace.",
        "warehouse": "Use shipping_quote with weight_kg 2 and zone regional.",
    }[case.server.name]
    result = case.run(prompt)
    expect(result).to_have_tool_call(tool, server=case.server.name, status="success")
```

Provider choices can be nondeterministic, and these cases need the matching
CLI and credentials. The complete executable, credential-free matrix
examples are in [`test_matrix_usage.py`](../examples/tests/test_matrix_usage.py).

### Score repeated harness trials

When the goal is a pass rate rather than a single tool-call assertion, invoke
an explicit evaluator for every matrix trial and aggregate its persisted
decisions. The live math example combines ten logical cases,
`HarnessMatrix.each_server`, `trials=2`, unrestricted access to one safe MCP
server, turn-scoped evaluation, and per-harness score reporting:

- [Evaluation walkthrough](evaluations.md#evaluate-a-harness-matrix-over-repeated-trials)
- [Complete typed pytest example](../examples/nondeterministic/test_math_harness_matrix.py)
- [Deterministic math MCP server](../examples/servers/math_mcp_server.py)

The full example uses OpenCode and includes commented alternatives for Claude
Code and a restrictive tool allowlist. Follow the evaluation walkthrough to
run it.

### Deterministic tools across server-owned tools

Use `ToolMatrix` when calls and arguments are known. Define every tool under
the `ServerCase` that owns it; servers often expose different tool sets. Each
parametrized case runs the normal SDK boundary and supports typed result and
trace assertions:

```python
from mcp_pal.matrix import ToolMatrix, ServerCase, ToolCase
from mcp_pal.types import CallToolResult

matrix = ToolMatrix(servers=(
    ServerCase(
        name="catalog", server=example_server.model_copy(update={"name": "catalog"}),
        tools=(
            ToolCase(name="normalize_customer", arguments={"name": "Ada Lovelace"}),
            ToolCase(name="batch_total", arguments={"values": [1, 2, 3]}),
        ),
    ),
    ServerCase(
        name="warehouse", server=example_server.model_copy(update={"name": "warehouse"}),
        tools=(ToolCase(
            name="shipping_quote",
            arguments={"weight_kg": 2, "zone": "regional"},
        ),),
    ),
))

@matrix.parametrize()
def test_owned_tool(case):
    result = case.run()
    assert isinstance(result.direct_result, CallToolResult)
    expected = {
        "catalog/normalize_customer": {"customer_id": "ada-lovelace"},
        "catalog/batch_total": {"total": 6.0},
        "warehouse/shipping_quote": {"amount": 12.0, "currency": "USD"},
    }
    assert result.direct_result.structured_content == expected[case.id]
    assert result.trace_view.tool_calls
```

### Bring your own harness

ACP is the **Bring your own harness** path: provide an `ACPAgent` manifest for
the executable you control. The deterministic local process accepts structured
JSON instructions so its server/tool choices are reproducible; a compatible
ACP harness can use the same matrix API. For the local executable used below:

```python
import sys
from pathlib import Path
from mcp_pal.types import ACPAgent

examples = Path("sdk/examples")
acp_harness = HarnessCase(
    name="acp",
    harness=ACPAgent(
        model="deterministic-example",
        manifest={
            "schema_version": "mcp-pal.harness.v1",
            "protocol": "acp",
            "protocol_version": 1,
            "command": sys.executable,
            "args": [str(examples / "servers" / "deterministic_acp_agent.py")],
            "env": {},
        },
    ),
)
```

### Modes, trials, and a continuing chain

`each_tool` creates one case for each server-owned tool. `all_servers` keeps
all declared servers available, which is useful when one turn feeds a value
to another server. This example uses the explicit ACP harness above; Claude's
multi-server restriction means it cannot be substituted here:

```python
import json

case = HarnessMatrix.all_servers(
    servers=(catalog, warehouse), harnesses=(acp_harness,)
).cases()[0]
with case.session() as session:
    first = session.send(json.dumps({
        "server": "catalog", "tool": "shipping_quote",
        "arguments": {"weight_kg": 2, "zone": "local"},
    }))
    amount = json.loads(first.response.text)["amount"]
    second = session.send(json.dumps({
        "server": "warehouse", "tool": "shipping_quote",
        "arguments": {"weight_kg": amount, "zone": "regional"},
    }))
```

The runnable version uses these server-owned tools and checks their returned
structured values in [`test_matrix_usage.py`](../examples/tests/test_matrix_usage.py).
Use `.cases()` when pytest is not the caller; `.parametrize("item")` gives the
pytest argument a custom name while preserving the case IDs. `trials=2` repeats
every matrix cell as independent `/trial-1` and `/trial-2` cases. The
executable example also covers sync and async helpers, SQLite reopen, typed
failure inspection, and this two-turn chain. Normal execution specifications,
events, turns, traces, and explicitly attached evaluations persist through
SQLite; plugin-enabled pytest verdicts are saved in internal run records and
matcher checks as execution evaluations. Matrix summary rows are not saved;
use `store.aggregate_evaluations(...)` to calculate
pass rates from the saved evaluations.

### Aggregate trials

Each execution is one trial. Matrix repetitions share a stable case ID while
their trial IDs remain different. Query by run for run trends, or by time and
evaluator for a calendar chart:

```python
from mcp_pal import EvaluationQuery

trend = store.aggregate_evaluations(EvaluationQuery(
    group_by=("run_id", "evaluator"),
    filters={"evaluator": "mcp_pal.output.has_text.v1"},
))
for group in trend.groups:
    print(group.key, group.values.pass_rate, group.values.trial_count)
```

Use `case_id` to compare matrix cells and `trial_id` to open the execution
report behind one chart point. Deterministic and user-supplied LLM evaluators
must be queried separately; judge labels come from saved evaluator provenance.
API v2 does not run either evaluator.

### External provider matrices

External-provider coverage is available in
[`test_live_matrix_api.py`](../tests/e2e/test_live_matrix_api.py). These tests
require `MCP_PAL_RUN_LIVE_OPENCODE=1` or
`MCP_PAL_RUN_LIVE_CLAUDE=1` with the matching harness credential and may incur
provider usage. Native Codex and Pi coverage is in
[`test_live_codex_pi.py`](../tests/e2e/test_live_codex_pi.py); it requires
`MCP_PAL_RUN_LIVE_CODEX=1` or `MCP_PAL_RUN_LIVE_PI=1`, the corresponding model
variable (`MCP_PAL_LIVE_CODEX_MODEL` or `MCP_PAL_LIVE_PI_MODEL`), and an
explicit credential route. Codex uses `OPENAI_API_KEY`; Pi accepts the generic
`MCP_PAL_LIVE_PI_PROVIDER`/`MCP_PAL_LIVE_PI_CREDENTIAL_ENV` route or its
`OPENAI_API_KEY`/`PI_CODING_AGENT_DIR` routes. Set `MCP_PAL_CODEX_EXECUTABLE` or
`MCP_PAL_PI_EXECUTABLE` when needed.  All live tests are skipped unless their
flag and prerequisites are present, and may incur provider usage. Claude's
multi-server limitation is respected by keeping its case to one server.
