<!-- Generated from docs/site/guides/agents/acp-wrapper.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Expose your custom agent through ACP

Implement the ACP SDK's `Agent` interface so M3 can launch your agent and record its MCP interactions. Use `run_agent` to handle the protocol. The example selects an echo tool from a JSON instruction; replace that selection with your own orchestration.

## Requirements

Use Python 3.10 or later with `sf-m3[pytest]` installed in the project environment. Its dependencies include `agent-client-protocol==0.12.1`. This example uses one local stdio MCP server and needs no API key.

## Complete project

Create a directory containing these three files. The M3 test launches the wrapper and checks both the captured MCP result and the agent's response.

`test_wrapper.py`:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_custom_acp_agent_exposes_mcp_result() -> None:
    selection = {
        "harness": "acp",
        "models": ["fixture"],
        "manifest": {
            "schema_version": "m3.harness.v1",
            "protocol": "acp",
            "protocol_version": 1,
            "command": sys.executable,
            "args": [str(HERE / "wrapped_agent.py")],
        },
    }
    server = StdioServer(
        name="echo",
        command=sys.executable,
        args=(str(HERE / "echo_server.py"),),
        cwd=str(HERE),
    )
    prompt = json.dumps({"tool": "echo", "arguments": {"text": "m3-wrapper-ok"}})
    with MCPTestKit(env={}) as kit:
        result = kit.agents([selection])[0].run(
            prompt, server=server, tools=["echo:echo"], timeout=20
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "echo",
        server="echo",
        arguments={"text": "m3-wrapper-ok"},
        status="success",
        count=1,
    )
    trace = result.trace_view
    captured = trace.tool_calls[0].result.value
    assert captured.content[0].text == "m3-wrapper-ok"
    assert captured.structured_content.value == {"echo": "m3-wrapper-ok"}
    assert any(
        message.role == "assistant"
        and any(
            block.kind == "text" and block.text == "m3-wrapper-ok"
            for block in message.content
        )
        for message in trace.messages
    )
```

`wrapped_agent.py`:

```python
from __future__ import annotations

import asyncio
import json
import uuid

from acp import Agent, Client, run_agent
from acp.schema import (
    AgentMessageChunk,
    Implementation,
    InitializeResponse,
    McpServerStdio,
    NewSessionResponse,
    PromptResponse,
    TextContentBlock,
    ToolCallProgress,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class WrappedAgent(Agent):
    def on_connect(self, conn: Client) -> None:
        self.client = conn
        self.active_prompt = None

    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(
            protocol_version=1,
            agent_info=Implementation(name="wrapped-agent", version="1"),
        )

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        if not mcp_servers or len(mcp_servers) != 1:
            raise ValueError("This example requires one stdio MCP server")
        self.server = mcp_servers[0]
        if not isinstance(self.server, McpServerStdio):
            raise ValueError("This example requires stdio")
        self.cwd = cwd
        self.session_id = uuid.uuid4().hex
        return NewSessionResponse(session_id=self.session_id)

    async def run_agent(self, text):
        """Replace JSON instruction parsing with your agent's tool selection."""
        instruction = json.loads(text)
        environment = {item.name: item.value for item in self.server.env}
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
        return instruction, result

    async def prompt(self, session_id, prompt, **kwargs):
        if session_id != self.session_id:
            raise ValueError("Unknown session")
        self.active_prompt = asyncio.current_task()
        try:
            text = " ".join(
                block.text for block in prompt if isinstance(block, TextContentBlock)
            )
            instruction, result = await self.run_agent(text)
            await self.client.session_update(
                session_id,
                ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id="call-1",
                    title=f"{self.server.name}:{instruction['tool']}",
                    raw_input=instruction["arguments"],
                    raw_output=result.model_dump(mode="json", by_alias=True),
                    status="failed" if result.is_error else "completed",
                ),
            )
            message = "".join(
                item.text for item in result.content if item.type == "text"
            )
            await self.client.session_update(
                session_id,
                AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text=message),
                ),
            )
            return PromptResponse(stop_reason="end_turn")
        except asyncio.CancelledError:
            return PromptResponse(stop_reason="cancelled")
        finally:
            self.active_prompt = None

    async def cancel(self, session_id, **kwargs):
        if session_id == self.session_id and self.active_prompt is not None:
            self.active_prompt.cancel()


if __name__ == "__main__":
    asyncio.run(run_agent(WrappedAgent()))
```

`echo_server.py`:

```python
from __future__ import annotations

import json
import sys


def reply(value: dict[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


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
                    "serverInfo": {"name": "echo", "version": "1"},
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
                            "name": "echo",
                            "description": "Return the supplied text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                                "additionalProperties": False,
                            },
                        }
                    ]
                },
            }
        )
    elif method == "tools/call":
        value = request.get("params", {}).get("arguments", {}).get("text", "")
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "content": [{"type": "text", "text": value}],
                    "structuredContent": {"echo": value},
                },
            }
        )
    elif identifier is not None:
        reply({"jsonrpc": "2.0", "id": identifier, "result": {}})
```

## Run it

From the project directory, run:

```sh
python -m pytest -q test_wrapper.py
```

```text
1 passed
```

The test checks one echo call with the requested arguments, the captured MCP response, and the text returned by the agent.

## Connect your agent logic

Replace `WrappedAgent.run_agent` with your tool-selection logic. Return the chosen instruction and actual MCP result so `prompt` can report them. Keep stdout reserved for ACP frames; send diagnostics to stderr.

Pass only the MCP server's configured environment entries to its subprocess. The MCP client adds its small platform-variable allowlist. Keep the wrapper's provider credentials out of this mapping unless the MCP server itself needs them.

`ToolCallProgress` produces an agent-reported `tool_call_update`. The result assertions use MCP requests and responses captured by M3, independently of that update.

The typed `InitializeResponse` includes agent identity during `initialize`. `NewSessionResponse` returns the session ID. Stdio MCP support is baseline ACP behavior. Omit the undefined `mcpCapabilities.stdio` flag.

## Cancellation and cleanup

The wrapper tracks the active prompt task so its `cancel` handler can cancel it and return a cancelled stop reason. Each prompt opens an MCP connection through `stdio_client` and `ClientSession`. Their context managers close the connection and clean up the child process.

This example supports one active session, one stdio server, and text prompts containing JSON instructions. It does not implement optional mode selection, filesystem, terminal, or permission requests. M3's platform-specific process cleanup and workspace behavior are described in the [ACP reference](reference-acp.md).

Next: [Connect an existing ACP-compatible agent](guides-agents-acp-connect.md), or [configure credentials](guides-credentials.md) for your wrapper.
