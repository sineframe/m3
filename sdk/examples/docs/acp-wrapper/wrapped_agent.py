from __future__ import annotations

import asyncio
import json
import os
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
