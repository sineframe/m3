"""Small MCP SDK 2.0 server used by the modern MRTR examples.

It exposes ordinary booking and a verified booking tool that can request one
of two address forms, both forms together, or no form before a later URL round.
Each retry validates the current round's keyed responses.
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
                    name="shipping_quote",
                    description="Return a shipping quote for a parcel.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "weight_kg": {"type": "number"},
                            "zone": {"type": "string"},
                        },
                        "required": ["weight_kg", "zone"],
                    },
                ),
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
                ),
                types.Tool(
                    name="book_verified_shipment",
                    description="Book a shipment after optional addresses and URL verification.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "address_kind": {
                                "type": "string",
                            }
                        },
                        "required": ["address_kind"],
                    },
                ),
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
        if params.name == "shipping_quote":
            return types.CallToolResult(
                content=[
                    types.TextContent(type="text", text="Shipping quote: USD 12.50")
                ],
                structured_content={"currency": "USD", "amount": 12.50},
            )
        if params.name == "book_verified_shipment":
            arguments = params.arguments or {}
            kind = arguments.get("address_kind")
            if kind not in {"home", "business", "both", "none"}:
                return types.CallToolResult(
                    content=[
                        types.TextContent(type="text", text="Invalid address kind.")
                    ],
                    is_error=True,
                )
            if kind == "both":
                address_keys = ("home_address", "business_address")
            elif kind == "none":
                address_keys = ()
            else:
                address_keys = (f"{kind}_address",)

            def verification_round() -> types.InputRequiredResult:
                return types.InputRequiredResult(
                    input_requests={
                        "verification": types.ElicitRequest(
                            params=types.ElicitRequestURLParams(
                                message="Complete shipment verification.",
                                url="https://example.test/verify/123",
                            )
                        )
                    },
                    request_state=f"verified-url:{kind}",
                )

            if params.request_state is None:
                if responses:
                    return types.CallToolResult(
                        content=[
                            types.TextContent(
                                type="text", text="Unexpected first response."
                            )
                        ],
                        is_error=True,
                    )
                if not address_keys:
                    return verification_round()
                return types.InputRequiredResult(
                    input_requests={
                        key: types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message=f"Enter the {key.split('_')[0]} delivery address.",
                                requested_schema=ADDRESS_SCHEMA,
                            )
                        )
                        for key in address_keys
                    },
                    request_state=f"verified-address:{kind}",
                )
            if params.request_state == f"verified-address:{kind}":
                if set(responses) != set(address_keys) or any(
                    not isinstance(responses[key], types.ElicitResult)
                    or responses[key].action != "accept"
                    for key in address_keys
                ):
                    return types.CallToolResult(
                        content=[
                            types.TextContent(
                                type="text", text="Invalid address response."
                            )
                        ],
                        is_error=True,
                    )
                return verification_round()
            if params.request_state == f"verified-url:{kind}":
                verification = responses.get("verification")
                if (
                    set(responses) != {"verification"}
                    or not isinstance(verification, types.ElicitResult)
                    or verification.action != "accept"
                ):
                    return types.CallToolResult(
                        content=[
                            types.TextContent(
                                type="text", text="Invalid verification response."
                            )
                        ],
                        is_error=True,
                    )
                return types.CallToolResult(
                    content=[
                        types.TextContent(type="text", text="Verified shipment booked.")
                    ],
                    structured_content={"status": "booked", "address_kind": kind},
                )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Invalid request state.")],
                is_error=True,
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
