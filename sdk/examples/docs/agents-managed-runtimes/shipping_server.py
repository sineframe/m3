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
