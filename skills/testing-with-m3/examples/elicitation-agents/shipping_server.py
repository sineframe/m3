from __future__ import annotations

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
    "required": ["street", "city"],
}
# Each tool asks for one address form before it books.
ADDRESS_FORMS = {
    "book_shipment": ("shipping_address", "Enter the delivery address."),
    "book_verified_shipment": (
        "business_address",
        "Enter the business delivery address.",
    ),
}


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="book_shipment",
                description="Book a shipment after requesting its address",
                input_schema={
                    "type": "object",
                    "properties": {"weight_kg": {"type": "number"}},
                    "required": ["weight_kg"],
                },
            ),
            types.Tool(
                name="book_verified_shipment",
                description="Book a shipment after requesting its business address",
                input_schema={
                    "type": "object",
                    "properties": {"address_kind": {"type": "string"}},
                    "required": ["address_kind"],
                },
            ),
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    key, message = ADDRESS_FORMS[params.name]
    if params.request_state is None:
        return types.InputRequiredResult(
            input_requests={
                key: types.ElicitRequest(
                    params=types.ElicitRequestFormParams(
                        message=message, requested_schema=ADDRESS_SCHEMA
                    )
                )
            },
            request_state=key,
        )

    response = (params.input_responses or {}).get(key)
    if not isinstance(response, types.ElicitResult) or response.action != "accept":
        return types.CallToolResult(
            content=[types.TextContent(text="address was not provided")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="Shipment booked.")],
        structured_content={"status": "booked"},
    )


async def main() -> None:
    server: Server[object] = Server(
        "shipping-agent-elicitation",
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
