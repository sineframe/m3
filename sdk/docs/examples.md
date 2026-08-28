# Verified examples

Every scenario below is an ordinary pytest test. The subprocess examples run
against the real local stdio MCP server in
[`example_mcp_server.py`](../examples/servers/example_mcp_server.py); the mock
example uses the SDK's deterministic in-process server. The tests are the
canonical, copyable examples and each section includes both a key snippet and
a link to its complete file.

Run the complete catalog from the repository root:

```bash
uv run --project sdk --extra pytest pytest -q sdk/examples/tests
```

## Lead example: live OpenCode tool usage

[`test_live_opencode.py`](../examples/tests/test_live_opencode.py) is the
opt-in end-to-end example for an actual agent. It launches OpenCode, connects
it to the real example MCP subprocess, asks it to use `shipping_quote`, and
inspects the captured canonical wire call directly.

```bash
set -a; source .env; set +a
MCP_PAL_RUN_LIVE_OPENCODE=1 uv run --project sdk --all-extras \
  pytest -q sdk/examples/tests/test_live_opencode.py
```

```python
requests = [
    event for event in trace.events
    if event.kind.value == "tool.call_requested"
    and event.server_binding == "example-mcp"
    and event.turn_id == turn.snapshot.turn_id
    and event.payload.get("tool") == "shipping_quote"
]
assert len(requests) == 1
arguments = requests[0].payload["arguments"]
assert arguments["zone"] == "local"
assert arguments["weight_kg"] == 2
```

This test is skipped unless `MCP_PAL_RUN_LIVE_OPENCODE=1`, OpenCode, and
`OPENCODE_API_KEY` are available. Provider behavior is nondeterministic and
can cost money; keep the narrow tool policy and semantic assertions, and do
not add brittle exact prose assertions. The deterministic catalog below is
the default CI path.

## Start here

### Discover and call a tool

[`test_quick_start.py`](../examples/tests/test_quick_start.py) shows the minimal
synchronous flow: open the kit and direct client, discover every paginated
tool, call `shipping_quote`, and assert structured output.

```python
with MCPTestKit(env={}) as kit:
    with kit.direct(example_server) as client:
        assert "shipping_quote" in {tool.name for tool in client.list_all_tools()}
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

assert result.is_error is False
assert result.structured_content == {"amount": 9.0, "currency": "USD"}
```

### Chain tool calls using earlier output

[`test_chained_workflow.py`](../examples/tests/test_chained_workflow.py) is the
canonical sequential workflow. It passes the normalized customer identifier
into order creation, passes the resulting order identifier into retrieval, and
verifies the state accumulated by the same MCP server process.

```python
normalized = client.call_tool("normalize_customer", {"name": "Ada Lovelace"})
customer_id = normalized.structured_content["customer_id"]
created = client.call_tool("create_order", {"customer_id": customer_id, "item": "engine", "quantity": 2})
order_id = created.structured_content["order_id"]
retrieved = client.call_tool("get_order", {"order_id": order_id})
assert retrieved.structured_content["customer_id"] == customer_id
```

### Use the async API

[`test_async_usage.py`](../examples/tests/test_async_usage.py) performs a tool
call through `AsyncMCPTestKit` and an async direct client, with no sync wrapper.

```python
async with AsyncMCPTestKit(env={}) as kit:
    async with kit.direct(example_server) as client:
        result = await client.call_tool("shipping_quote", {"weight_kg": 3, "zone": "regional"})
assert result.structured_content["currency"] == "USD"
```

## Check readiness and capabilities

[`test_capabilities_and_probes.py`](../examples/tests/test_capabilities_and_probes.py)
shows the baseline kit capabilities and an explicit optional-dependency probe.
Use probes when a test suite needs a clear readiness report before exercising
an environment-dependent integration.

```python
with MCPTestKit(env={}) as kit:
    report = kit.capabilities()
    assert report.readiness.ready
    transport = kit.capabilities([ProbeRequest(
        kind=ProbeKind.TRANSPORT,
        name="mcp-package",
        module="mcp",
        transport="stdio",
    )])
assert transport.result_for("mcp-package").status is CapabilityStatus.READY
```

## Errors and contracts

[`test_errors_and_contracts.py`](../examples/tests/test_errors_and_contracts.py)
contains three test functions and five pytest cases:

- `test_assert_an_expected_tool_error` distinguishes an MCP tool-error result
  from an exception and checks its content.
- `test_shipping_contract` parametrizes local, regional, and international
  inputs to exercise the same tool contract with several meaningful cases.
- `test_reject_arguments_that_do_not_match_the_tool_schema` opts into JSON
  Schema validation and asserts `ModelValidationError` for invalid input.

The parametrized contract produces three pytest cases, so these functions
account for five passing cases in the suite.

```python
with kit.direct(example_server, validate_schemas=True) as client:
    with pytest.raises(ModelValidationError):
        client.call_tool("shipping_quote", {"weight_kg": -1, "zone": "local"})
```

## Assert tool usage

[`test_tool_usage_assertions.py`](../examples/tests/test_tool_usage_assertions.py)
is a synthetic matcher-contract reference. It covers exact and partial
arguments, regular expressions, numeric tolerance, unordered arrays, argument
and call predicates, exact results, status, counts, latency bounds, server
identity, and positive/negative assertions. It is not evidence that the
matcher currently accepts a captured direct or agent-session trace.

For real direct-client tool usage, inspect the untouched canonical events as
shown in [`test_assertions_snapshots_evaluations.py`](../examples/tests/test_assertions_snapshots_evaluations.py).

```python
synthetic_trace = _matcher_trace()
expect(synthetic_trace).to_have_tool_call(
    "shipping_quote",
    arguments={"zone": r"reg.*"},
    arguments_partial=True,
    arguments_regex=True,
    min_count=1,
    max_count=1,
)
expect(synthetic_trace).to_have_tool_call("shipping_quote", count=2)
expect(synthetic_trace).to_not_have_tool_call("create_order")
```

`to_have_reported_tool_call`, `evidence="wire"`, `evidence="reported"`, and
`evidence="any"` are also covered by the synthetic fixture in the same file.
The rich `to_have_tool_call` API exists, but its current evidence-shape
compatibility is narrower than its signature suggests (see the table below).

| Surface | Status | Reference |
| --- | --- | --- |
| Live harness prompting and multi-turn capture | Supported | [`test_live_opencode.py`](../examples/tests/test_live_opencode.py) |
| Raw canonical direct/agent trace inspection | Supported | [`test_assertions_snapshots_evaluations.py`](../examples/tests/test_assertions_snapshots_evaluations.py), [`test_live_opencode.py`](../examples/tests/test_live_opencode.py) |
| Rich `to_have_tool_call` API contract | Supported on synthetic wire-shaped fixtures | [`test_tool_usage_assertions.py`](../examples/tests/test_tool_usage_assertions.py) |
| `to_have_tool_call` directly on normalized direct traces | Current gap: `NORMALIZED` provenance is filtered out | Use raw event assertions until SDK normalization is fixed |
| `to_have_tool_call` directly on agent-session wire traces | Current gap: agent payloads use root fields instead of `params` | Use raw event assertions until SDK normalization is fixed |

## Resources and prompts

[`test_resources_and_prompts.py`](../examples/tests/test_resources_and_prompts.py)
contains two protocol examples:

- `test_read_a_resource` discovers resources and reads text from the returned
  URI.
- `test_render_a_prompt_with_arguments` discovers prompts, supplies an
  argument, and asserts the rendered prompt message.

The complete direct-client operation matrix, including pagination, resource
templates, completion, subscriptions, ping, logging, progress, notifications,
and roots changed, is in
[`test_direct_client_surface.py`](../examples/tests/test_direct_client_surface.py).

```python
resources = client.list_all_resources()
guide = client.read_resource(resources[0].uri)
prompt = client.get_prompt("review_order", {"order_id": "order-042"})
completion = client.complete(
    types.PromptReference(name="review_order"),
    {"name": "order_id", "value": "order-"},
)
```

## Tracing and lifecycle

[`test_tracing_and_lifecycle.py`](../examples/tests/test_tracing_and_lifecycle.py)
contains three operational scenarios:

- `test_inspect_a_finalized_trace` asserts canonical request, response, tool,
  terminal, and sequence evidence after client closure.
- `test_closing_a_client_stops_its_server_process` verifies closed-client
  behavior and confirms the owned stdio subprocess exits.
- `test_each_connection_has_isolated_server_state` proves state is shared by
calls on one connection but absent from a new subprocess connection.

The matcher and result projection matrix is in
[`test_assertions_snapshots_evaluations.py`](../examples/tests/test_assertions_snapshots_evaluations.py):
text/content/structured matchers, grouped checks, traces/events/duration,
execution lifecycle/outcome/errors, artifacts, workspace diffs, snapshots,
and synchronous/asynchronous evaluators.

```python
expect(quote).to_have_text_containing('"currency": "USD"')
expect(execution).to_be_completed()
expect(trace).to_have_event("execution.finished", count=1)
```

## SDK-provided test doubles

When a test needs to own the server contract instead of launching the server
under test,
[`test_mock_server_expectations.py`](../examples/tests/test_mock_server_expectations.py)
shows `MockMCPServer.tool`, ordered `expect_tool_call`, `verify`, recording,
and JSON round-tripping. This is still a real MCP protocol session, but it is
an in-process SDK fixture rather than a subprocess.

```python
from mcp_pal import MCPTestKit
from mcp_pal.testing import MockMCPServer

server = MockMCPServer(name="contract-example")

@server.tool
def greet(arguments):
    return {"message": f"Hello, {arguments['name']}!"}

server.expect_tool_call("greet", {"name": "Ada"})
with MCPTestKit(env={}) as kit:
    with kit.direct(server.in_process()) as client:
        client.call_tool("greet", {"name": "Ada"})
server.verify()
```

## Supported surface and deliberate boundaries

The examples cover every supported synchronous and asynchronous direct MCP
operation exposed by `DirectClient`/`AsyncDirectClient`, and every public
matcher method on result and trace projections. The rich tool-call matcher is
covered as a synthetic contract fixture because direct/agent trace compatibility
is currently incomplete. `MCPTestKit`/`AsyncMCPTestKit` configuration,
capabilities, evaluators, direct execution, and lifecycle are also covered.

One current integration gap is visible in the live example: agent-session wire
events store tool name and arguments at the event-payload root, while the
generic tool-call matcher reads the direct-client `params` projection. The
example therefore inspects untouched canonical events directly. This should be
fixed in the SDK before treating agent-session matcher examples as zero-adapter
API.

The following are intentionally not presented as working examples because the
current SDK does not support them as ordinary direct-test operations:

- post-construction callback registration (`register_callbacks`) raises
  `UnsupportedFeature`; callbacks must be supplied when creating the client;
  the boundary is executable in
  [`test_direct_client_surface.py`](../examples/tests/test_direct_client_surface.py);
- generic agent/harness sessions require a configured provider adapter and a
  real provider executable, so they are not deterministic local MCP examples;
- official MCP conformance is a separate future wrapper around the official
  conformance suite, not reimplemented in this examples catalog;
- the SDK's fault-injection, replay, workspace, HTTP/SSE, and policy APIs are
  public support components but are not direct MCP author quick-start flows;
  they should receive dedicated guides when their end-to-end fixtures are
  intentionally documented.

## Shared example setup

[`conftest.py`](../examples/tests/conftest.py) contains the explicit
`StdioServer` pytest fixture used throughout the catalog. It is normal project
test code, not an MCP Pal pytest plugin fixture. Replace its command, arguments,
and working directory with the server under test.

The fixture launches
[`example_mcp_server.py`](../examples/servers/example_mcp_server.py), a compact
official MCP low-level server with paginated tools, structured results, JSON
Schemas, subprocess-local session state, a deliberate tool error, a resource,
and a prompt. It is test infrastructure for the examples rather than an SDK
mock.
