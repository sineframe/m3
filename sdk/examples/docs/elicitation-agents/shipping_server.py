from __future__ import annotations

import asyncio
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
    "required": ["street", "city"],
}


def build_server() -> Server:
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

    async def call_tool(_context: object, params: types.CallToolRequestParams):
        responses = params.input_responses or {}
        if params.name == "book_shipment":
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
                    request_state="address",
                    meta={
                        "io.modelcontextprotocol/serverInfo": {
                            "name": "shipping-agent-elicitation",
                            "version": "1.0.0",
                        }
                    },
                )
            address = responses.get("shipping_address")
            if (
                isinstance(address, types.ElicitResult)
                and address.action == "accept"
                and address.content == {"street": "1 Main Street", "city": "Pune"}
            ):
                return types.CallToolResult(
                    content=[types.TextContent(text="Shipment booked.")],
                    structured_content={"status": "booked"},
                )
        elif params.name == "book_verified_shipment":
            if params.request_state is None:
                return types.InputRequiredResult(
                    input_requests={
                        "business_address": types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message="Enter the business delivery address.",
                                requested_schema=ADDRESS_SCHEMA,
                            )
                        )
                    },
                    request_state="business-address",
                    meta={
                        "io.modelcontextprotocol/serverInfo": {
                            "name": "shipping-agent-elicitation",
                            "version": "1.0.0",
                        }
                    },
                )
            address = responses.get("business_address")
            if (
                params.request_state == "business-address"
                and isinstance(address, types.ElicitResult)
                and address.action == "accept"
                and address.content == {"street": "2 Business Street", "city": "Pune"}
            ):
                return types.CallToolResult(
                    content=[types.TextContent(text="Shipment booked.")],
                    structured_content={
                        "status": "booked",
                        "address_kind": "business",
                    },
                )
        return types.CallToolResult(
            content=[types.TextContent(text="Invalid response.")], is_error=True
        )

    return Server(
        "shipping-agent-elicitation", on_list_tools=list_tools, on_call_tool=call_tool
    )


async def serve() -> None:
    from mcp.server.stdio import stdio_server

    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(serve())
