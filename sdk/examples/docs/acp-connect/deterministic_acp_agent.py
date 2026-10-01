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
