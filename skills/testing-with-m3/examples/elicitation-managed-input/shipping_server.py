from __future__ import annotations

from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {
        "street": {"type": "string"},
        "city": {"type": "string"},
    },
    "required": ["street", "city"],
}


async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="book_shipment",
                description="Book a shipment after confirming the delivery address",
                input_schema={
                    "type": "object",
                    "properties": {"weight_kg": {"type": "number"}},
                    "required": ["weight_kg"],
                },
            )
        ]
    )


async def call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    responses = params.input_responses or {}
    if params.request_state is None:
        return types.InputRequiredResult(
            input_requests={
                "shipping_address": types.ElicitRequest(
                    params=types.ElicitRequestFormParams(
                        message="Enter the delivery address.",
                        requested_schema=ADDRESS_SCHEMA,
                    )
                )
            },
            request_state="shipping-address",
        )

    address = responses.get("shipping_address")
    if (
        params.request_state != "shipping-address"
        or not isinstance(address, types.ElicitResult)
        or address.action != "accept"
        or address.content != {"street": "1 Main Street", "city": "Pune"}
    ):
        return types.CallToolResult(
            content=[types.TextContent(text="address response did not match")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="Shipment booked.")],
        structured_content={"status": "booked", "city": "Pune"},
    )


async def main() -> None:
    server: Server[object] = Server(
        "shipping-managed-input",
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
