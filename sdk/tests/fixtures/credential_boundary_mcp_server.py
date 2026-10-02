"""Report credential presence without returning credential values."""

from __future__ import annotations

import os

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="credential_boundary",
                description="Check the MCP subprocess credential boundary",
                input_schema={"type": "object", "properties": {}},
            )
        ]
    )


async def call_tool(
    _context: object, _params: types.CallToolRequestParams
) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(text="credential boundary checked")],
        structured_content={
            "provider_key_present": "WRAPPER_PROVIDER_KEY" in os.environ,
            "future_key_present": "WRAPPER_FUTURE_KEY" in os.environ,
            "server_key_received": os.environ.get("MCP_SERVER_KEY")
            == "dummy-server-key",
        },
    )


async def main() -> None:
    server: Server[object] = Server(
        "credential-boundary", on_list_tools=list_tools, on_call_tool=call_tool
    )
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
