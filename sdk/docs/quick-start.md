# Quick start

The shortest useful MCP Pal test discovers a server's tools, calls one, and
asserts its typed result. The complete, executable version is
[`test_quick_start.py`](../examples/tests/test_quick_start.py).

## Install

Add MCP Pal and its pytest dependencies to a uv-managed project:

```bash
uv add "mcp-pal[pytest]"
```

The equivalent pip command is:

```bash
python -m pip install "mcp-pal[pytest]"
```

The SDK requires Python 3.10 or newer.

## Define the server under test

MCP Pal receives a server definition rather than starting a hidden fixture.
For a local stdio server, construct a public `StdioServer` with its command,
arguments, and working directory. The examples do this in the ordinary pytest
fixture [`example_server`](../examples/tests/conftest.py), which points to the
real subprocess server
[`example_mcp_server.py`](../examples/servers/example_mcp_server.py).

These two shared files are part of the example setup; copy or replace them with
the command for your own MCP server. No `mcp_test` pytest fixture or scenario
file is required.

## Write the test

Follow [`test_discover_and_call_a_tool`](../examples/tests/test_quick_start.py):

1. Enter `MCPTestKit` to own the test runtime and its cleanup.
2. Open `kit.direct(example_server)` to initialize one MCP connection.
3. Use `list_all_tools()` to discover all tool pages.
4. Call the selected tool with `call_tool(name, arguments)`.
5. Assert `is_error` and `structured_content` on the typed result.

For assertions about the whole execution, close the client first and project
its finalized trace. `TraceView` is the stable typed surface for tools,
messages, timing, runtime metadata, and terminal outcome:

```python
from mcp_pal.types import ExecutionOutcome

with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
    result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})
    # result is the typed operation result while the client is open.

trace = client.final_trace
assert trace is not None
view = trace.view()
assert view.outcome is ExecutionOutcome.COMPLETED
call = view.tool_calls[0]
assert call.tool.value == "shipping_quote"
assert call.arguments.value == {"weight_kg": 2, "zone": "local"}
assert call.wire.state.value == "observed"
```

The executable version is
[`test_typed_trace_view.py`](../examples/tests/test_typed_trace_view.py).
Trace projection is finalized-only; use the operation result for assertions
that must happen before client shutdown.

Both objects are context managers. Exiting the direct client closes its MCP
connection and subprocess; exiting the kit provides the outer cleanup boundary.

## Run the verified examples from a checkout

From the repository root, run exactly:

```bash
uv run --project sdk --extra pytest pytest -q sdk/examples/tests
```

This command installs the SDK project's pytest extra and runs every documented
example against a real local MCP subprocess. To run only the quick start:

```bash
uv run --project sdk --extra pytest pytest -q sdk/examples/tests/test_quick_start.py
```

Continue with the [concepts](concepts.md) or choose a scenario from the
[examples catalog](examples.md).
