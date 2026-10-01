from __future__ import annotations

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
    "required": ["street", "city"],
}


def build_server(calls: list[dict[str, object]]) -> Server:
    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="book_shipment",
                    description="Book a shipment after confirming its address",
                    input_schema={
                        "type": "object",
                        "properties": {"weight_kg": {"type": "number"}},
                        "required": ["weight_kg"],
                    },
                )
            ]
        )

    async def call_tool(_context: object, params: types.CallToolRequestParams):
        calls.append(
            {
                "request_state": params.request_state,
                "responses": dict(params.input_responses or {}),
            }
        )
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
                request_state="shipping-address:opaque-v1",
            )
        response = (params.input_responses or {}).get("shipping_address")
        if (
            params.request_state != "shipping-address:opaque-v1"
            or not isinstance(response, types.ElicitResult)
            or response.action != "accept"
            or response.content != {"street": "1 Main Street", "city": "Pune"}
        ):
            return types.CallToolResult(content=[], is_error=True)
        return types.CallToolResult(
            content=[types.TextContent(text="Shipment booked.")],
            structured_content={"status": "booked", "city": "Pune"},
        )

    return Server("shipping-manual", on_list_tools=list_tools, on_call_tool=call_tool)
