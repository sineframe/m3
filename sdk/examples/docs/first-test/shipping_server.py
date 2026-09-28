"""A deterministic MCP shipping quote server used by the first-test guide."""

from __future__ import annotations

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
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
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult:
    arguments = params.arguments or {}
    rates = {"local": 2.0, "regional": 3.5, "international": 7.0}
    amount = round(5 + float(arguments["weight_kg"]) * rates[str(arguments["zone"])], 2)
    quote = {"amount": amount, "currency": "USD"}
    return types.CallToolResult(
        content=[types.TextContent(text=f"{amount:.2f} USD")],
        structured_content=quote,
    )


async def main() -> None:
    server = Server(
        "shipping-server",
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
