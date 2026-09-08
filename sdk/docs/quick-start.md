# Quick start

For a deployed MCP URL, use `HTTPServer` and assert direct discovery
and a tool call. The complete external example is
[`examples/nondeterministic/test_streamable_http.py`](../examples/nondeterministic/test_streamable_http.py);
it is nondeterministic and is run by invoking that exact file. For a local
command, use the deterministic stdio example
[`examples/tests/test_quick_start.py`](../examples/tests/test_quick_start.py).
For the HTTP route, see the [Streamable HTTP guide](http.md).

```python
from collections.abc import Mapping

from mcp_pal import MCPTestKit
from mcp_pal.types import HTTPServer

server = HTTPServer(name="deepwiki", url="https://mcp.deepwiki.com/mcp")
with MCPTestKit(env={}) as kit, kit.direct(server) as client:
    assert client.initialization is not None
    tools = client.list_all_tools()
    assert {"ask_question", "read_wiki_contents", "read_wiki_structure"} <= {
        tool.name for tool in tools
    }
    result = client.call_tool(
        "read_wiki_structure", {"repoName": "modelcontextprotocol/python-sdk"}
    )
    assert result.is_error is False
    assert any(
        isinstance(block, Mapping)
        and isinstance(block.get("text"), str)
        and block["text"].strip()
        for block in result.content
    )
```

## Install the project SDK

Add MCP Pal and its pytest dependencies to a uv-managed project:

```bash
uv add "mcp-pal[pytest]"
```

The equivalent pip command is:

```bash
python -m pip install "mcp-pal[pytest]"
```

The SDK requires Python 3.10 or newer.

These commands install the `mcp-pal` Python SDK and pytest support inside the
project environment. They do **not** install the standalone `mcp-pal` command
or the bundled UI.

## Install the standalone CLI

Install the CLI separately when you want CLI-managed test runs, persistent run
history, or the local UI. The CLI is a machine-level tool isolated from the
project environment; its release installer includes the production UI. Follow
the [CLI installation guide](../../cli/README.md#install), which covers GitHub
authentication and macOS, Linux, and Windows installation.

After installing the CLI, prepare the project environment and verify that its
SDK version matches the CLI:

```bash
cd my-project
mcp-pal setup
mcp-pal doctor
```

`mcp-pal setup` installs the matching `mcp-pal[pytest,storage]` SDK into the
selected project environment. It does not install the CLI there and does not
edit dependency manifests or lockfiles. This setup step is separate from both
the machine-level CLI installation and declaring the SDK as a project
dependency.

## Define the server under test

The remainder of this walkthrough uses the deterministic local fixture.
MCP Pal receives a server definition rather than starting a hidden fixture.
For a deployed HTTP endpoint, use `HTTPServer` as shown in the
[Streamable HTTP guide](http.md). For a local subprocess, construct
a `StdioServer` with its command,
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
connection and any subprocess it owns; a deployed HTTP service keeps running.
Exiting the kit provides the outer cleanup boundary and finalizes trace data.

## Run the test

The separately installed MCP Pal CLI runs the test with pytest in your project
environment and records MCP Pal executions. From the project root:

```bash
mcp-pal doctor
mcp-pal test -- tests/test_shipping.py
```

Everything after `--` is passed to pytest unchanged, so selectors such as
`-k`, `-m`, and individual test node IDs work normally. Add `--ui` before the
separator to open the bundled local viewer after the test run:

```bash
mcp-pal test --ui -- tests/test_shipping.py
```

The UI shows the recorded runs and keeps the command open until you press
Ctrl+C. It is bundled with the standalone CLI; the project does not need
Node.js or a separate frontend.

The tests remain ordinary pytest tests. Run pytest directly when you do not
need CLI-managed result storage or the UI:

```bash
uv run pytest tests/test_shipping.py
```

## Choose whether test executions persist

Persistence is optional when the SDK is used directly. An `MCPTestKit` with no
configured store keeps execution data in memory for the lifetime of the kit;
closing the kit does not leave a saved run history.

Direct SDK users who choose SQLite must install the storage extra in addition
to pytest support:

```bash
uv add "mcp-pal[pytest,storage]"
```

`mcp-pal test` makes a different product-level choice: it always enables the
SDK pytest plugin and supplies a SQLite results database. The default is
`.mcp-pal/executions.sqlite` below the project root, and `--results-db` selects
another path:

```bash
mcp-pal test --results-db /tmp/mcp-pal-runs.sqlite -- tests/test_shipping.py
```

Tests can opt into saved storage without the standalone CLI by constructing
the store explicitly:

```python
from mcp_pal import MCPTestKit
from mcp_pal.storage import SQLiteExecutionStore

store = SQLiteExecutionStore(".mcp-pal/executions.sqlite")
with MCPTestKit(store=store, env={}) as kit:
    result = kit.run(spec)
execution_id = result.snapshot.execution_id
store.close()
```

Alternatively, a direct pytest invocation can install the same plugin and
default-store flag used by the CLI:

```bash
uv run pytest -p mcp_pal.pytest_plugin \
  --mcp-pal-results-db .mcp-pal/executions.sqlite tests/test_shipping.py
```

SQLite saves SDK execution specifications and snapshots, recorded events
and traces, sessions and turns, persisted artifacts/raw-evidence references,
and evaluations explicitly attached to an execution. Use
`MCPTestKit(store=SQLiteExecutionStore(path))` or pytest's
`--mcp-pal-results-db PATH` to select it; no-store SDK use remains in memory.
With the MCP Pal pytest plugin active, pytest item outcomes are persisted in
internal run records and MCP Pal matcher checks are persisted as execution
evaluations. Other Python assertion results and printed diagnostics keep their
normal pytest meaning and are not inferred as MCP Pal evaluations.
Use `store.aggregate_evaluations(...)` for matrix/trial trends; do not infer a
pass from a merely completed execution.

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
[`built-in harness example`](examples.md#3-use-claude-code-or-opencode-with-the-local-stdio-server).
OpenCode needs its provider credential; the repository's external endpoint
example shows the explicit command and credential reference.

## Bring your own harness with ACP

Use this route for an ACP-compatible agent you provide. `session.send` returns
a completed `TurnResult`; after the session closes, finalized trace assertions
go through `session.result`:

```python
import sys
from pathlib import Path
from mcp_pal import MCPTestKit, expect
from mcp_pal.types import ACPAgent, AgentSpec, RestrictiveToolPolicy, ServerBinding, StdioServer

examples = Path("sdk/examples")
server = StdioServer(
    name="example-mcp", command=sys.executable,
    args=(str(examples / "servers" / "example_mcp_server.py"),),
    cwd=str(examples),
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

This direct pytest command is convenient for the repository's SDK subproject:
it installs the SDK project's pytest extra and runs every documented example
against a real local MCP subprocess. To run only the quick start:

```bash
uv run --project sdk --extra pytest pytest -q sdk/examples/tests/test_quick_start.py
```

Continue with the [concepts](concepts.md) or choose a scenario from the
[examples catalog](examples.md).
