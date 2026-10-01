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
                        "zone": {"type": "string", "enum": ["local", "regional"]},
                    },
                    "required": ["weight_kg", "zone"],
                    "additionalProperties": False,
                },
            ),
            types.Tool(
                name="shipping_window",
                description="Return the local delivery window",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        ]
    )


async def call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    arguments = params.arguments or {}
    if params.name == "shipping_quote":
        rates = {"local": 2.0, "regional": 3.5}
        zone = arguments.get("zone")
        weight = arguments.get("weight_kg")
        if (
            zone not in rates
            or not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or weight <= 0
        ):
            return types.CallToolResult(
                content=[types.TextContent(text="invalid shipping request")],
                is_error=True,
            )
        result = {"amount": round(5 + weight * rates[zone], 2), "currency": "USD"}
    elif params.name == "shipping_window" and not arguments:
        result = {"days": 2, "zone": "local"}
    else:
        return types.CallToolResult(
            content=[types.TextContent(text="invalid shipping request")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="shipping result ready")],
        structured_content=result,
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
