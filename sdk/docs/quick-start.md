# Quick start

The shortest useful direct MCP test discovers a server's tools, calls one, and
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
its finalized trace. `TraceView` is the stable typed API for tools,
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

## Run one test per server-owned tool

When several servers expose different tools, keep each tool under its owning
`ServerCase`. `ToolMatrix` expands those definitions into ordinary pytest
items, each receiving one immutable case:

```python
from mcp_pal.matrix import ServerCase, ToolCase, ToolMatrix

matrix = ToolMatrix(servers=(ServerCase(
    name="catalog",
    server=example_server,
    tools=(ToolCase(name="shipping_quote", arguments={"weight_kg": 2, "zone": "local"}),),
),))

@matrix.parametrize()
def test_catalog_tool(case):
    result = case.run()
    assert result.direct_result is not None
```

`@matrix.parametrize()` uses pytest's normal collection and filtering. Call
`matrix.cases()` when you want the same expansion without pytest.

## Use Claude Code or OpenCode

MCP Pal supports the built-in `ClaudeCode` and `OpenCode` harness choices.
They use the same `agent_session` flow with their normal tool access. Tool
restrictions can be added later when a test needs tighter control. The complete
server and harness definitions are in the
[`built-in harness example`](examples.md#2-use-claude-code-or-opencode).
OpenCode needs its provider credential; the repository's live example shows
the explicit opt-in.

## Bring your own harness with ACP

Use this route for an ACP-compatible agent you provide. `session.send` returns
a completed `TurnResult`; after the session closes, finalized trace assertions
go through `session.result`:

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
        turn = session.send("Use shipping_quote for a local quote")

expect(session.result).to_have_tool_call("shipping_quote", turn=turn)
turn_view = session.result.trace_view.for_turn(turn)
assert turn_view.tool_calls
```

The `TurnResult` is a supported turn selector and has a `turn_id` convenience
property; it does not own a `trace_view`. See the deterministic
[`test_harness_trace_view.py`](../examples/tests/test_harness_trace_view.py)
for a complete local harness flow. Direct MCP testing and harness-driven agent
testing are separate workflows: only the latter has an agent process and
turn-scoped responses.

## Run the examples from a checkout

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
