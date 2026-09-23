"""Small MCP SDK 2.0 server used by the modern MRTR examples.

The server deliberately keeps the protocol behavior visible: the first call
returns a typed ``InputRequiredResult`` and the retry completes only when the
keyed ``shipping_address`` response is present.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "street": {"type": "string"},
        "city": {"type": "string"},
        "postal_code": {"type": "string"},
    },
    "required": ["street", "city", "postal_code"],
}


def build_server(
    observed_tool_calls: list[dict[str, object]] | None = None,
) -> Server:
    """Build the low-level server for in-process and stdio examples."""

    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="book_shipment",
                    description=(
                        "Book a shipment after collecting the delivery address."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "weight_kg": {"type": "number"},
                            "zone": {"type": "string"},
                        },
                        "required": ["weight_kg", "zone"],
                    },
                )
            ]
        )

    async def call_tool(
        context: object,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult | types.InputRequiredResult:
        responses = params.input_responses or {}
        if observed_tool_calls is not None:
            observed_tool_calls.append(
                {
                    "request_id": getattr(context, "request_id", None),
                    "name": params.name,
                    "arguments": params.arguments,
                    "request_state": params.request_state,
                    "input_responses": {
                        key: value.model_dump(mode="json", by_alias=True)
                        if hasattr(value, "model_dump")
                        else value
                        for key, value in responses.items()
                    },
                }
            )
        marker = os.environ.get("M3_MRTR_WIRE_MARKER")
        if marker:
            with open(marker, "a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "id": str(getattr(context, "request_id", None)),
                            "name": params.name,
                            "arguments": params.arguments,
                            "requestState": params.request_state,
                            "inputResponses": {
                                key: value.model_dump(mode="json", by_alias=True)
                                if hasattr(value, "model_dump")
                                else value
                                for key, value in responses.items()
                            },
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
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
        address = responses.get("shipping_address")
        if not isinstance(address, types.ElicitResult):
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="missing address")],
                is_error=True,
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="Shipment booked.")],
            structured_content={"status": "booked", "address": address.content},
        )

    return Server(
        "modern-mrtr-example", on_list_tools=list_tools, on_call_tool=call_tool
    )


async def _serve_stdio() -> None:
    from mcp.server.stdio import stdio_server

    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(_serve_stdio())
