from __future__ import annotations

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {
        "street": {"type": "string"},
        "city": {"type": "string"},
    },
    "required": ["street", "city"],
}


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="book_shipment",
                description="Book a shipment after confirming a delivery address",
                input_schema={
                    "type": "object",
                    "properties": {"weight_kg": {"type": "number"}},
                    "required": ["weight_kg"],
                },
            )
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    responses = params.input_responses or {}
    if not responses:
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

    response = responses.get("shipping_address")
    if (
        not isinstance(response, types.ElicitResult)
        or response.action != "accept"
        or response.content != {"street": "1 Main Street", "city": "Pune"}
    ):
        return types.CallToolResult(
            content=[types.TextContent(text="address response did not match")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="Shipment booked.")],
        structured_content={"status": "booked", "city": "Pune"},
    )


def build_server() -> Server:
    return Server(
        "shipping-elicitation",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
