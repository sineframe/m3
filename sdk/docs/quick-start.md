# Quick start

## Response judge

`m3 setup` installs judge support in the project environment. Rerun setup to
add it to an environment created by an older CLI.

`m3 init` creates `.env.example` with blank agent and judge key names. Copy it
to `.env` if that file is absent, or add the needed names to your existing
`.env`. Keep `.env` out of version control. Put the judge key in `.env` as
`M3_JUDGE_API_KEY`. For an OpenCode agent, the same file can also hold
`OPENCODE_API_KEY`:

```dotenv
OPENCODE_API_KEY=agent-secret
M3_JUDGE_API_KEY=judge-secret
```

Load the file when running tests:

```sh
m3 test --env-file .env -- tests/test_answer.py
```

```python
import pytest
from m3.judges import LLMJudge

@pytest.mark.m3
def test_answer(m3_kit):
    judge = LLMJudge(model="judge-model")
    result = m3_kit.judge_response(
        name="answer.correctness.v1", input="What is 2 + 3?",
        actual="The answer is 5.", expected="The answer is 5.", judge=judge,
    )
    assert result.status.value == "passed"
```

`required=True` persists a failed or error result before raising. Durable
records retain score, rationale, safe details, provenance, and a subject
digest; raw submitted text and provider payloads are omitted. Use
`--judge-max-requests N` to cap requests for a run, including retries.

## Agent behavior tests

Use an ordinary marked pytest test; the CLI supplies harness and model:

```python
import pytest
from m3 import expect

@pytest.mark.m3
def test_shipping(agent, shipping_server):
    result = agent.run("Get a local quote", server=shipping_server)
    expect(result).to_have_tool_call("shipping_quote", server=shipping_server.name,
                                     status="success")
```

Provider credentials are `OPENCODE_API_KEY`, `OPENAI_API_KEY`, or
`ANTHROPIC_API_KEY` in the process environment. Use `--env-file .env` to load
them explicitly. MCP endpoint credentials remain in `HTTPServer.headers`.

For a deployed MCP URL, use `HTTPServer` and assert direct discovery
and a tool call. The complete external example is
[`examples/nondeterministic/test_streamable_http.py`](../examples/nondeterministic/test_streamable_http.py);
it is nondeterministic and is run by invoking that exact file. For a local
command, use the deterministic stdio example
[`examples/tests/test_quick_start.py`](../examples/tests/test_quick_start.py).
For the HTTP route, see the [Streamable HTTP guide](http.md).

```python
from collections.abc import Mapping

from m3 import MCPTestKit
from m3.types import HTTPServer

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

Choose a release version and add the SDK wheel with pytest support to the
project being tested:

```bash
VERSION=X.Y.Z
uv add \
  "m3[pytest] @ https://github.com/sineframe/m3/releases/download/v${VERSION}/m3-${VERSION}-py3-none-any.whl"
```

The SDK requires Python 3.10 or newer.

This installs only the project SDK and pytest support. It does **not** install
the standalone `m3` command or the bundled UI.

## Install the standalone CLI

Install the CLI separately when you want CLI-managed test runs, persistent run
history, or the local UI. The CLI is a machine-level tool isolated from the
project environment; its release installer includes the production UI. Follow
the [CLI installation guide](../../cli/README.md#install), which covers GitHub
authentication and macOS, Linux, and Windows installation.

If you install the CLI, it can prepare the project environment and verify that
the SDK version matches the CLI:

```bash
cd my-project
m3 setup
m3 doctor
```

`m3 setup` installs the matching `m3[pytest,storage,judge]` SDK into the
selected project environment. It does not install the CLI there and does not
edit dependency manifests or lockfiles. This setup step is separate from both
the machine-level CLI installation and declaring the SDK as a project
dependency.

## Define the server under test

The remainder of this walkthrough uses the deterministic local fixture.
M3 receives a server definition rather than starting a hidden fixture.
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
from m3.types import ExecutionOutcome

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

The separately installed M3 CLI runs the test with pytest in your project
environment and records M3 executions. From the project root:

```bash
m3 doctor
m3 test -- tests/test_shipping.py
```

Everything after `--` is passed to pytest unchanged, so selectors such as
`-k`, `-m`, and individual test node IDs work normally. Add `--ui` before the
separator to open the bundled local viewer after the test run:

```bash
m3 test --ui -- tests/test_shipping.py
```

The UI shows the recorded runs and keeps the command open until you press
Ctrl+C. It is bundled with the standalone CLI; the project does not need
Node.js or a separate frontend.

The tests remain ordinary pytest tests. Run pytest directly when you do not
need CLI-managed result storage or the UI:

```bash
uv run pytest tests/test_shipping.py
```

Use the existing marker across files to name one suite:

```python
import pytest
pytestmark = pytest.mark.m3(suite_name="catalog")
```

Select it with `m3 test --suite catalog -- tests`; combine it
with `--harness`, `--trials`, paths, `-k`, and `-m`. A standalone kit or
execution specification can set `suite_name="catalog"` directly. An explicit
specification name overrides the kit default; the effective name must still
match the pytest marker when one is active.

## Choose whether test executions persist

Persistence is optional when the SDK is used directly. An `MCPTestKit` with no
configured store keeps execution data in memory for the lifetime of the kit;
closing the kit does not leave a saved run history.

Direct SDK users who choose SQLite can include storage support when adding the
release wheel:

```bash
VERSION=X.Y.Z
uv add \
  "m3[pytest,storage,judge] @ https://github.com/sineframe/m3/releases/download/v${VERSION}/m3-${VERSION}-py3-none-any.whl"
```

`m3 test` makes a different product-level choice: it always enables the
SDK pytest plugin and supplies a SQLite results database. The default is
`.m3/executions.sqlite` below the project root, and `--results-db` selects
another path:

```bash
m3 test --results-db /tmp/m3-runs.sqlite -- tests/test_shipping.py
```

Scripts can opt into saved storage without the standalone CLI by passing
`SQLiteExecutionStore(".m3/executions.sqlite")` to `MCPTestKit(store=...)`.
Run a selected agent as shown below and use `result.snapshot.execution_id` to
reopen its trace. Close the store after the kit.

Alternatively, a direct pytest invocation can install the same plugin and
default-store flag used by the CLI:

```bash
uv run pytest -p m3.pytest_plugin \
  --results-db .m3/executions.sqlite tests/test_shipping.py
```

SQLite saves SDK execution specifications and snapshots, recorded events
and traces, sessions and turns, persisted artifacts/raw-evidence references,
and evaluations explicitly attached to an execution. Use
`MCPTestKit(store=SQLiteExecutionStore(path))` or pytest's
`--results-db PATH` to select it; no-store SDK use remains in memory.
With the M3 pytest plugin active, pytest item outcomes are persisted in
internal run records and M3 matcher checks are persisted as execution
evaluations. Other Python assertion results and printed diagnostics keep their
normal pytest meaning and are not inferred as M3 evaluations.
Use `store.aggregate_evaluations(...)` for matrix/trial trends; do not infer a
pass from a merely completed execution.

## Run one test per server-owned tool

When several servers expose different tools, keep each tool under its owning
`ServerCase`. `ToolMatrix` expands those definitions into ordinary pytest
items, each receiving one immutable case:

```python
from m3.matrix import ServerCase, ToolCase, ToolMatrix

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

## Use a native agent harness

The same marked test runs against any native harness and model supplied by the
CLI. Define the server fixture in your project, then request `agent`:

```python
import pytest
from m3 import expect

@pytest.mark.m3
def test_agent_selects_shipping_quote(agent, shipping_server):
    result = agent.run(
        "Get a local shipping quote for a 2 kg parcel.",
        server=shipping_server,
    )
    expect(result).to_have_tool_call(
        "shipping_quote", server=shipping_server.name, status="success"
    )
```

```bash
m3 test --env-file .env \
  --harness opencode=opencode/big-pickle \
  --harness codex=gpt-5.6-sol \
  --trials 2 -- tests/test_shipping.py
```

This command creates four agent test items. Put `OPENCODE_API_KEY` in `.env` for
OpenCode and `OPENAI_API_KEY` for Codex when using provider keys. Existing
native login can also authenticate a harness where supported. Claude Code uses
`ANTHROPIC_API_KEY`; OpenCode and Pi use the key for their model provider.
Put provider keys in `.env` under the names expected by the selected harness.

To keep defaults in code for direct pytest, use
`@pytest.mark.m3(agents=[{"harness": "opencode", "models": ["opencode/big-pickle"]}])`
and load the plugin with `python -m pytest -p m3.pytest_plugin`. CLI choices
replace those defaults. The marker with no arguments is the clean path for
CLI-selected tests.

A normal Python file or notebook needs no pytest:

Normal Python reads provider credentials from the process environment. Export a
key before starting the notebook, or launch the file with an explicit dotenv
file:

```bash
export OPENCODE_API_KEY='<your provider key>'
uv run --env-file .env python notebook_example.py
```

For a provider with a custom source variable, map names in the agent
dictionary; `vendor/model` below is a placeholder for your configured model,
and the value stays in the process environment:

```python
agents = [{
    "harness": "opencode",
    "models": ["vendor/model"],
    "credential_env": {"VENDOR_API_KEY": "MY_VENDOR_KEY"},
}]
```

```python
import sys
from pathlib import Path
from m3 import MCPTestKit, StdioServer

examples = Path("sdk/examples").resolve()
shipping_server = StdioServer(
    name="example-mcp",
    command=sys.executable,
    args=[str(examples / "servers" / "example_mcp_server.py")],
    cwd=str(examples),
)
agents = [
    {"harness": "opencode", "models": ["opencode/big-pickle"]},
    {"harness": "codex", "models": ["gpt-5.6-sol"]},
]
with MCPTestKit() as kit:
    for agent in kit.agents(agents):
        result = agent.run("Find the shipping tool", server=shipping_server)
        print(agent.harness, agent.model,
              [call.tool.value for call in result.trace_view.tool_calls])
```

For a continuing conversation, open `agent.session(server=shipping_server)`,
call `session.send(...)` for each turn, then inspect `session.result` after the
session closes. `agent.submit(...)` is the advanced nonblocking path: it returns
an execution handle for `snapshot()`, `result(timeout=...)`, events, and
`cancel()`. Ordinary tests use `run`, which waits and returns the result.

### Diagnose a slow agent safely

`timeout=` on `agent.run(...)` or `agent.submit(...)` is the execution deadline:
it covers startup, the MCP server, the harness turn, and bounded cleanup. The
default selected-agent deadline is 180 seconds; pass a positive value for a
shorter deadline or `timeout=None` to disable it for a deliberately long run.
`handle.result(timeout=...)` is different: it only limits how long your Python
code waits and does not cancel the background execution.

Use the handle's committed event stream when diagnosing a timeout. Event
identity is safe to log because it contains no prompt, tool arguments, provider
response, or credentials:

```python
handle = agent.submit("Find the shipping tool", server=shipping_server, timeout=30)
for event in handle.events():
    print(event.sequence, event.kind.value, event.lifecycle_phase.value)
result = handle.result(timeout=35)
```

For a timeout, inspect `result.trace_view.diagnostics` after finalization. A
diagnostic includes `code`, `stage`, `operation`, `elapsed_seconds`, and
`timeout_seconds`. `waiting_for_harness_response` means the trace observed the
harness response wait; it does not prove why the provider is slow. The trace is
marked partial when cancellation prevents complete capture. The CLI equivalent
is `m3 test --execution-timeout 30 -- ...`; its per-execution feedback is
written under `.m3/reports/<run-id>/executions/` and `traces/`. The live UI
gate's `--process-timeout` is a separate outer process limit.

## Bring your own harness with ACP

Provide an ACP manifest in the same plain agent dictionary. The manifest names
the executable and its protocol; credentials are environment references, never
literal values. This deterministic local example runs in a plain Python file:

```python
import sys
from pathlib import Path
from m3 import MCPTestKit, expect, StdioServer

examples = Path("sdk/examples").resolve()
server = StdioServer(
    name="example-mcp", command=sys.executable,
    args=(str(examples / "servers" / "example_mcp_server.py"),),
    cwd=str(examples),
)
acp = [{
    "harness": "acp",
    "models": ["deterministic-fixture"],
    "manifest": {
        "schema_version": "m3.harness.v1",
        "protocol": "acp", "protocol_version": 1,
        "command": sys.executable,
        "args": [str(examples / "servers" / "deterministic_acp_agent.py")],
        "env": {},
    },
}]
with MCPTestKit() as kit:
    agent = kit.agents(acp)[0]
    with agent.session(server=server) as session:
        turn = session.send("Use shipping_quote for a local quote")
    expect(session.result).to_have_tool_call("shipping_quote", turn=turn)
```

The local ACP example needs no provider key. For an external ACP agent, set the
variables its manifest references in the process environment. The same
selection can go in a `m3(agents=[...])` marker when pytest is preferred.

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
