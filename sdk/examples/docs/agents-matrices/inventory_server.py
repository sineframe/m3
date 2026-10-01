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
                name="stock_count",
                description="Return stock for a SKU",
                input_schema={
                    "type": "object",
                    "properties": {"sku": {"type": "string"}},
                    "required": ["sku"],
                    "additionalProperties": False,
                },
            ),
            types.Tool(
                name="reorder_status",
                description="Return the reorder status for a SKU",
                input_schema={
                    "type": "object",
                    "properties": {"sku": {"type": "string"}},
                    "required": ["sku"],
                    "additionalProperties": False,
                },
            ),
        ]
    )


async def call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    sku = (params.arguments or {}).get("sku")
    if not isinstance(sku, str) or not sku:
        return types.CallToolResult(
            content=[types.TextContent(text="sku is required")], is_error=True
        )
    if sku != "widget-1":
        return types.CallToolResult(
            content=[types.TextContent(text="unknown sku")], is_error=True
        )
    if params.name == "stock_count":
        result = {"sku": sku, "quantity": 12}
    elif params.name == "reorder_status":
        result = {"sku": sku, "reorder": False}
    else:
        return types.CallToolResult(
            content=[types.TextContent(text="unknown tool")], is_error=True
        )
    return types.CallToolResult(
        content=[types.TextContent(text="inventory result ready")],
        structured_content=result,
    )


async def main() -> None:
    server: Server[object] = Server(
        "inventory",
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
