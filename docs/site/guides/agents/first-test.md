---
title: "Test an agent’s tool use"
description: "This test starts a local MCP server, gives Codex access to one named tool, and checks the recorded call. It verifies the interaction M3 observed; it does not prove that the agent’s final prose is correct."
---

# Test an agent’s tool use

This test starts a local MCP server, gives Codex access to one named tool, and checks the recorded call. It verifies the interaction M3 observed; it does not prove that the agent’s final prose is correct.

## Requirements

- Install the M3 SDK and pytest in the environment used to run the test.
- Install and sign in to the Codex CLI. This example uses the system installation and its normal local authentication.
- Set `M3_DOCS_CODEX_MODEL` to a model identifier available to that installation. M3 does not choose a provider model for you.
- Review Codex’s MCP approval prompt before accepting it. The M3 policy below restricts the test to `shipping:shipping_quote`; keep approval scoped to this local test.

Credential-free documentation checks skip this live example. See
[harness compatibility](../../reference/compatibility.md).

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

The test should pass after any required Codex approval. The matcher checks the tool name, server alias, arguments, successful result, and call count in this turn. If the agent completes without that call, the assertion fails even if its reply claims it used the tool.

This test selects Codex with `kit.agents(...)`, so it runs under plain pytest. To pass the harness and model on the command line instead, request the pytest `agent` fixture and run `m3 test --harness codex=MODEL`; see [the pytest plugin reference](../../reference/pytest.md). A test that requests `agent` fails at collection when no harness is selected, and `-k` does not prevent that, so keep these tests in a separate file from your direct tests.

To test a different server, change the `StdioServer` command and arguments, then update the alias, allowed tool, prompt, and expected call together. Keep `arguments` explicit in the matcher when argument selection is part of the behavior under test.

If Codex is unavailable or the test times out, first run `codex --version`, confirm the selected model is available, and inspect the M3 turn result. Do not treat an unavailable harness as a passing agent test.

Next: compare transports in [the server guides](../servers/stdio.md), or run multiple agent configurations with [a matrix](matrices.md).
