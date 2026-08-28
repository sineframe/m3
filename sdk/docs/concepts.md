# Concepts

## Server definition, kit, and client

A server definition such as `StdioServer` describes how to reach an MCP
server. It does not connect when it is constructed. The shared
[`example_server` fixture](../examples/tests/conftest.py) shows a stdio binding
to a Python subprocess.

`MCPTestKit` is the outer runtime and cleanup boundary. `kit.direct(server)`
creates a direct client for one MCP connection. Entering the client starts the
transport and performs MCP initialization; leaving it closes the connection
and its owned subprocess. Keep calls that depend on server session state inside
the same client context. The lifecycle behavior is executable in
[`test_tracing_and_lifecycle.py`](../examples/tests/test_tracing_and_lifecycle.py).

## Sync and async APIs

Use `MCPTestKit` in synchronous tests and `AsyncMCPTestKit` in async tests. The
direct operations have matching shapes, but calls on the async client are
awaited. Compare the synchronous
[`test_quick_start.py`](../examples/tests/test_quick_start.py) with
[`test_async_usage.py`](../examples/tests/test_async_usage.py).

Choose the API that matches the surrounding application or test. There is no
need to create an event loop inside a synchronous test or move an async test
through a synchronous wrapper.

## Direct protocol testing

The examples exercise MCP operations directly: initialize, discover tools,
call tools, list and read resources, and list and render prompts. This gives
deterministic protocol-level assertions without involving an LLM or agent
harness. Resource and prompt coverage lives in
[`test_resources_and_prompts.py`](../examples/tests/test_resources_and_prompts.py).

For an agent-driven workflow, see the opt-in live OpenCode example
[`test_live_opencode.py`](../examples/tests/test_live_opencode.py). It uses a
restrictive policy, sends one instruction, and verifies the actual captured
wire tool call rather than trusting the model's prose. Live tests need an
explicit environment opt-in and credentials, so they are separate from the
deterministic suite and should have generous operation timeouts.

The example also documents a current matcher integration gap: agent-session
wire events expose tool fields at the event-payload root, whereas the generic
tool matcher reads the direct-client `params` projection. The example therefore
inspects untouched canonical events directly; that workaround should disappear
once the SDK normalizes both trace sources.

## Tool errors and exceptions

An MCP server can successfully answer `tools/call` while reporting that the
tool itself failed. That is a `ToolCallResult` with `is_error=True`, not a
Python exception. Assert its returned content as shown in
[`test_assert_an_expected_tool_error`](../examples/tests/test_errors_and_contracts.py).

Transport failures, protocol failures, timeouts, and local validation failures
are exceptions. Keeping these two paths distinct lets a test say whether the
server was unreachable or the requested operation produced an expected domain
error.

## Structured output and schema validation

`ToolCallResult.structured_content` exposes the MCP tool's structured result
without parsing its text representation. Tools may also advertise input and
output JSON Schemas. Pass `validate_schemas=True` to `kit.direct(...)` when a
test should enforce those contracts locally. Valid data-driven cases and an
invalid-input assertion are executable in
[`test_errors_and_contracts.py`](../examples/tests/test_errors_and_contracts.py).

Schema validation is opt-in. A validation mismatch raises
`ModelValidationError`; it is not represented as `is_error=True` because the
SDK rejected a contract mismatch rather than receiving a tool-error result.

## Chained, stateful workflows

A chained test keeps one direct client open and feeds structured output from
one tool into the arguments of the next. Because all calls share one MCP
connection and server process, the workflow may also verify state written by
an earlier call.

The canonical
[`test_chained_workflow.py`](../examples/tests/test_chained_workflow.py)
normalizes a customer, passes that identifier into order creation, then passes
the returned order identifier into retrieval and asserts the stored record.
This is a multi-step protocol workflow, not a conversational agent session:
the test explicitly controls every call and transition.

## Live and finalized traces

The direct client records canonical evidence around MCP requests, responses,
and tool calls. While the client is open, `client.trace` is a live immutable
snapshot. After the client closes, `client.final_trace` includes terminal
cleanup and `execution.finished` evidence. Inspect the finalized form when
asserting completeness or terminal outcome, as demonstrated by
[`test_inspect_a_finalized_trace`](../examples/tests/test_tracing_and_lifecycle.py).

## Determinism and isolation

The example server is local, contains no network or model dependency, and
returns deterministic values. Each stdio client starts a fresh subprocess, so
state is retained across chained calls on one connection but does not leak to
the next connection. The isolation and process-cleanup assertions are in
[`test_tracing_and_lifecycle.py`](../examples/tests/test_tracing_and_lifecycle.py).

Use the same pattern in a project: control fixtures, avoid shared external
state when testing protocol behavior, and make persistence an explicit part of
a test only when persistence is the behavior under test.
