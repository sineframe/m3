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
                name="credential_check",
                description="Return whether the expected dummy service credential arrived.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult:
    expected = os.environ.get("DEMO_SERVICE_TOKEN")
    if params.name != "credential_check" or expected != "dummy-service-token":
        return types.CallToolResult(
            content=[types.TextContent(text="credential check failed")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="stdio credential accepted")],
        structured_content={"credential_present": True},
    )


async def main() -> None:
    server: Server[object] = Server(
        "credential-demo", on_list_tools=list_tools, on_call_tool=call_tool
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
