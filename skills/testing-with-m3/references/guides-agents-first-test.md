<!-- Generated from docs/site/guides/agents/first-test.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Test an agent’s tool use

This test starts a local MCP server, gives Codex access to one named tool, and checks the recorded call. It verifies the interaction M3 observed; it does not prove that the agent’s final prose is correct.

## Requirements

- Install the M3 SDK and pytest in the environment used to run the test.
- Install and sign in to the Codex CLI. This example uses the system installation and its normal local authentication.
- Set `M3_DOCS_CODEX_MODEL` to a model identifier available to that installation. M3 does not choose a provider model for you.
- M3 approves Codex's MCP tool calls to the test server. The `tools` selection below restricts the test to `shipping:shipping_quote`.
- Agent tests use your own agent login or API key, and your model provider bills the tokens each run uses. M3 adds no charge and supplies no key. Direct tests, such as [your first MCP test](getting-started.md), need no key.

To use Claude Code instead of Codex, read the requirements in [run the test with Claude Code](#run-the-test-with-claude-code) below.

Credential-free documentation checks skip this live example. See
[harness compatibility](reference-compatibility.md).

## Example project

Save these two files in one directory. The server uses the MCP Python SDK included with M3’s dependency set.

`shipping_server.py`:

```python
from __future__ import annotations

from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="shipping_quote",
                description="Calculate a deterministic shipping quote",
                input_schema={
                    "type": "object",
                    "properties": {
                        "weight_kg": {"type": "number", "exclusiveMinimum": 0},
                        "zone": {
                            "type": "string",
                            "enum": ["local", "regional", "international"],
                        },
                    },
                    "required": ["weight_kg", "zone"],
                    "additionalProperties": False,
                },
            )
        ]
    )


async def call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    rates = {"local": 2.0, "regional": 3.5, "international": 7.0}
    arguments = params.arguments or {}
    weight = arguments.get("weight_kg")
    zone = arguments.get("zone")
    if params.name != "shipping_quote" or zone not in rates:
        return types.CallToolResult(
            content=[types.TextContent(text="unknown tool or shipping zone")],
            is_error=True,
        )
    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
        return types.CallToolResult(
            content=[types.TextContent(text="weight_kg must be positive")],
            is_error=True,
        )
    quote = {"amount": round(5 + weight * rates[zone], 2), "currency": "USD"}
    return types.CallToolResult(
        content=[types.TextContent(text="Quote calculated")],
        structured_content=quote,
    )


async def main() -> None:
    server: Server[object] = Server(
        "shipping",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
```

`test_agent.py`:

```python
import os
import sys
from pathlib import Path

from m3 import MCPTestKit, expect
from m3.types import StdioServer, TurnOutcome

HERE = Path(__file__).resolve().parent


def test_agent_calls_the_shipping_tool() -> None:
    model = os.environ["M3_DOCS_CODEX_MODEL"]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    with MCPTestKit(env={}) as kit:
        agent = kit.agents([{"harness": "codex", "models": [model]}])[0]
        session = agent.session(
            server=server,
            tools=["shipping:shipping_quote"],
            timeout=120,
        )
        with session:
            turn = session.send(
                "Use shipping:shipping_quote once with weight_kg 2 and zone local. "
                "Report the returned amount.",
                timeout=120,
            )
        result = session.result

    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        turn=turn,
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
```

From the directory containing both files, run:

```bash
python -m pytest -q test_agent.py
```

The matcher checks the tool name, server alias, arguments, successful result, and call count in this turn. If the agent completes without that call, the assertion fails even if its reply claims it used the tool.

This test selects Codex with `kit.agents(...)`, so it runs under plain pytest. To pass the harness and model on the command line instead, request the pytest `agent` fixture and run `m3 test --harness codex=MODEL`. Read [agent troubleshooting](troubleshooting-agents.md) before you mix fixture-based agent tests with direct tests.

## Run the test with Claude Code

Claude Code needs three changes to the test above: a different harness name, a
credential you pass explicitly, and no `tools` selection.

Requirements:

- Install the Claude Code CLI; `claude --version` must work in the terminal that runs the test.
- Set `M3_DOCS_CLAUDE_MODEL` to a model identifier your account can use. M3 passes it to Claude Code as `--model`.
- Provide a credential. M3 starts Claude Code with a temporary home directory and does not copy your Claude Code login into it, so being signed in on the machine is not enough. Use one of:
  - An API key. Set `ANTHROPIC_API_KEY` in the environment that runs the test. M3 forwards it when the selection has no `credential_env`.
  - A subscription token. Run `claude setup-token`, export the token it prints under a name you choose, such as `MY_CLAUDE_TOKEN`, and add `"credential_env": {"CLAUDE_CODE_OAUTH_TOKEN": "MY_CLAUDE_TOKEN"}` to the selection. The mapping holds variable names, not the token.

Check both the executable and the credential before you run the test:

```bash
m3 doctor --require harness:claude
```

It prints `harness: ready` when `ANTHROPIC_API_KEY` is set to a non-empty value. For the subscription token, add `--credential-env claude_code:CLAUDE_CODE_OAUTH_TOKEN=MY_CLAUDE_TOKEN`. With neither, it exits with status 1 and reports `harness: unavailable (no credential for Claude Code)`. The check looks for the variable; it does not validate the key or token with the provider.

Save this complete test beside `shipping_server.py` as `test_agent_claude.py`:

```python
import os
import sys
from pathlib import Path

from m3 import MCPTestKit, expect
from m3.types import StdioServer, TurnOutcome

HERE = Path(__file__).resolve().parent


def test_claude_code_calls_the_shipping_tool() -> None:
    model = os.environ["M3_DOCS_CLAUDE_MODEL"]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    with MCPTestKit(env={}) as kit:
        agent = kit.agents([{"harness": "claude_code", "models": [model]}])[0]
        session = agent.session(server=server, timeout=120)
        with session:
            turn = session.send(
                "Use the shipping_quote tool once with weight_kg 2 and zone local. "
                "Report the returned amount.",
                timeout=120,
            )
        result = session.result

    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        turn=turn,
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
```

Run it from the same directory:

```bash
python -m pytest -q test_agent_claude.py
```

The session has no `tools=[...]` argument. Claude Code cannot restrict a
session to exact tool names, so passing one raises `UnsupportedFeature: Claude
Code does not support exact tool restrictions; assert the selected tool in the
trace`. Without `tools`, M3 allows Claude Code the MCP tools of the one bound
server, at server scope. A Claude Code session takes exactly one server. The
`to_have_tool_call` assertion is what checks which tool the agent chose.

The first assertion prints `result.error`, the execution error, which carries
Claude Code's reason. With no `ANTHROPIC_API_KEY` and no mapped token, the turn
fails within seconds and the message is `Claude Code turn failed: no credential
was passed to Claude Code; set ANTHROPIC_API_KEY or map CLAUDE_CODE_OAUTH_TOKEN
with credential_env`. `turn.error` reports only `turn failed` for every failed
turn. See
[agent troubleshooting](troubleshooting-agents.md#claude-code-turn-failed).

To test a different server, change the `StdioServer` command and arguments, then update the alias, allowed tool, prompt, and expected call together. Keep `arguments` explicit in the matcher when argument selection is part of the behavior under test.

If Codex is unavailable or the test times out, first run `codex --version`, confirm the selected model is available, and inspect the M3 turn result. Do not treat an unavailable harness as a passing agent test.

Next: compare transports in [the server guides](guides-servers-stdio.md), or run multiple agent configurations with [a matrix](guides-agents-matrices.md).
