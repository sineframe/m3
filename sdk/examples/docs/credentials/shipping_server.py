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
                description="Calculate a fixed local shipping quote.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "weight_kg": {"type": "number"},
                        "zone": {"type": "string", "enum": ["local"]},
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
    if (
        params.name != "shipping_quote"
        or (params.arguments or {}).get("weight_kg") != 2
        or (params.arguments or {}).get("zone") != "local"
    ):
        return types.CallToolResult(
            content=[types.TextContent(text="unexpected quote request")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="9.00 USD")],
        structured_content={"amount": 9.0, "currency": "USD"},
    )


async def main() -> None:
    server: Server[object] = Server(
        "shipping", on_list_tools=list_tools, on_call_tool=call_tool
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
