---
title: "Connect an ACP-compatible agent"
description: "Launch your existing ACP agent with M3 and check the MCP server result."
---

# Connect an ACP-compatible agent

Use M3 to launch an agent that already speaks ACP and check how it calls your MCP server. This test asks for one shipping quote, then checks the captured MCP response.

## Requirements

Use Python 3.10 or later with `sf-m3[pytest]` installed in the project environment. Your agent must support ACP over stdin and stdout and accept a stdio MCP server. Install and authenticate it using its own instructions.

Set these variables before running the test:

| Variable | Value |
| --- | --- |
| `ACP_AGENT_COMMAND` | The installed agent executable, as an absolute path or a command on `PATH`. |
| `ACP_AGENT_ARGS` | Optional JSON array of command arguments; defaults to `[]`. |
| `ACP_AGENT_CREDENTIAL_ENV` | The provider credential variable the agent reads. |
| `M3_DOCS_PROVIDER_API_KEY` | The provider key to pass to that variable. |
| `M3_DOCS_AGENT_MODEL` | A label for the selected agent/model. This is not a portable ACP model switch; configure the actual model in your agent. |

Provider access may incur cost. Confirm the agent's model, approvals, and command arguments before running it.

## Complete test

Create a project directory containing `test_connect_external.py` and `shipping_server.py`.

`test_connect_external.py`:

```python
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_external_acp_agent_calls_shipping_tool() -> None:
    manifest = {
        "schema_version": "m3.harness.v1",
        "protocol": "acp",
        "protocol_version": 1,
        "command": os.environ["ACP_AGENT_COMMAND"],
        "args": json.loads(os.environ.get("ACP_AGENT_ARGS", "[]")),
        "env": {os.environ["ACP_AGENT_CREDENTIAL_ENV"]: "${M3_DOCS_PROVIDER_API_KEY}"},
    }
    selection = {
        "harness": "acp",
        "models": [os.environ["M3_DOCS_AGENT_MODEL"]],
        "manifest": manifest,
    }
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    prompt = (
        "Call shipping_quote once for weight_kg 2 in zone local. "
        "Return the amount from the tool result."
    )
    with MCPTestKit() as kit:
        result = kit.agents([selection])[0].run(
            prompt, server=server, tools=["shipping:shipping_quote"], timeout=120
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
    captured = result.trace_view.tool_calls[0].result.value
    assert captured.content[0].text == "9.00 USD"
```

`shipping_server.py`:

```python
from __future__ import annotations

import json
import sys


def reply(message: dict[str, object]) -> None:
    print(json.dumps(message, separators=(",", ":")), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    identifier = request.get("id")
    if method == "initialize":
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "shipping", "version": "1"},
                },
            }
        )
    elif method == "tools/list":
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "tools": [
                        {
                            "name": "shipping_quote",
                            "description": "Calculate a deterministic shipping quote",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "weight_kg": {"type": "number"},
                                    "zone": {
                                        "type": "string",
                                        "enum": ["local", "regional"],
                                    },
                                },
                                "required": ["weight_kg", "zone"],
                                "additionalProperties": False,
                            },
                        }
                    ]
                },
            }
        )
    elif method == "tools/call":
        arguments = request.get("params", {}).get("arguments", {})
        rate = 2 if arguments["zone"] == "local" else 3.5
        amount = round(5 + float(arguments["weight_kg"]) * rate, 2)
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "content": [{"type": "text", "text": f"{amount:.2f} USD"}],
                    "structuredContent": {"amount": amount, "currency": "USD"},
                },
            }
        )
    elif identifier is not None:
        reply({"jsonrpc": "2.0", "id": identifier, "result": {}})
```

## Run it

From that project directory, run:

```sh
python -m pytest -q test_connect_external.py
```

A passing test checks one successful `shipping_quote` call with the requested arguments and the server's captured `9.00 USD` response. An agent-reported tool update alone is not enough for the result assertion.

The manifest maps the parent `M3_DOCS_PROVIDER_API_KEY` to the child variable named by `ACP_AGENT_CREDENTIAL_ENV`. The agent receives an isolated environment; it does not inherit all of your shell variables.

## Try the connection without a provider

For a local smoke test, add these two files to the same directory. The fixture uses the ACP SDK's typed API and an MCP client. It selects a fixed tool call instead of using a model.

`deterministic_acp_agent.py`:

```python
from __future__ import annotations

import asyncio
import json
import os

from acp import Agent, Client, run_agent
from acp.schema import (
    AgentMessageChunk,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    TextContentBlock,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ShippingAgent(Agent):
    def on_connect(self, conn: Client) -> None:
        self.client = conn

    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(
            protocol_version=1,
            agent_info=Implementation(name="shipping-fixture", version="1"),
        )

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        self.server = mcp_servers[0]
        self.cwd = cwd
        return NewSessionResponse(session_id="shipping-session")

    async def prompt(self, session_id, prompt, **kwargs):
        text = " ".join(
            block.text for block in prompt if isinstance(block, TextContentBlock)
        )
        if text == (
            "Call shipping_quote once for weight_kg 2 in zone local. "
            "Return the amount from the tool result."
        ):
            instruction = {
                "tool": "shipping_quote",
                "arguments": {"weight_kg": 2, "zone": "local"},
            }
        else:
            instruction = json.loads(text)
        environment = {
            **os.environ,
            **{item.name: item.value for item in self.server.env},
        }
        params = StdioServerParameters(
            command=self.server.command,
            args=self.server.args,
            env=environment,
            cwd=self.cwd,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as mcp:
                await mcp.initialize()
                await mcp.list_tools()
                result = await mcp.call_tool(
                    instruction["tool"], instruction["arguments"]
                )
        message = "".join(item.text for item in result.content if item.type == "text")
        await self.client.session_update(
            session_id,
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=message),
            ),
        )
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(run_agent(ShippingAgent()))
```

`test_connect.py`:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_existing_local_acp_agent_calls_shipping_tool() -> None:
    manifest = {
        "schema_version": "m3.harness.v1",
        "protocol": "acp",
        "protocol_version": 1,
        "command": sys.executable,
        "args": [str(HERE / "deterministic_acp_agent.py")],
    }
    selection = {"harness": "acp", "models": ["fixture"], "manifest": manifest}
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    prompt = json.dumps(
        {
            "server": "shipping",
            "tool": "shipping_quote",
            "arguments": {"weight_kg": 2, "zone": "local"},
        }
    )
    with MCPTestKit(env={}) as kit:
        result = kit.agents([selection])[0].run(
            prompt, server=server, tools=["shipping:shipping_quote"], timeout=20
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
    captured = result.trace_view.tool_calls[0].result.value
    assert captured.content[0].text == "9.00 USD"
    assert captured.structured_content.value == {
        "amount": 9.0,
        "currency": "USD",
    }
```

From the same directory, run:

```sh
python -m pytest -q test_connect.py
```

```text
1 passed
```

This local test checks the ACP connection and captured MCP result without provider credentials.

## Startup failures

If readiness reports `acp_executable_missing`, check the command on the test machine. For `acp_environment_unavailable`, set the referenced parent variable. An invalid `agent_mode_id` or `session_config` causes a generic startup failure; choose IDs advertised in the agent's `session/new` response.

Next: [Expose a custom agent through ACP](acp-wrapper.md). The [ACP reference](../../reference/acp.md) covers manifest fields, workspace behavior, and platform-specific cleanup.
