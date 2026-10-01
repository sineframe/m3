from __future__ import annotations

from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
    "required": ["street", "city"],
}
ADDRESSES = {
    "home_address": {"street": "1 Home Street", "city": "Pune"},
    "business_address": {"street": "2 Business Street", "city": "Pune"},
}


def build_server(calls: list[dict[str, object]]) -> Server:
    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="book_verified_shipment",
                    description="Collect addresses and complete a verification request",
                    input_schema={
                        "type": "object",
                        "properties": {"address_kind": {"type": "string"}},
                        "required": ["address_kind"],
                    },
                )
            ]
        )

    async def call_tool(
        _context: object, params: types.CallToolRequestParams
    ) -> types.CallToolResult | types.InputRequiredResult:
        state = params.request_state
        responses = params.input_responses or {}
        kind = (params.arguments or {}).get("address_kind")
        calls.append(
            {
                "request_state": state,
                "response_keys": tuple(sorted(responses)),
                "responses": {
                    key: response.model_dump(mode="json", by_alias=True)
                    for key, response in responses.items()
                },
            }
        )
        if state is None:
            if kind == "both":
                keys = ("home_address", "business_address")
            elif kind in {"home", "business"}:
                keys = (f"{kind}_address",)
            elif kind == "none":
                return types.InputRequiredResult(
                    input_requests={
                        "verification": types.ElicitRequest(
                            params=types.ElicitRequestURLParams(
                                message="Complete shipment verification.",
                                url="https://example.test/verify/123",
                            )
                        )
                    },
                    request_state="verification",
                )
            else:
                return types.CallToolResult(content=[], is_error=True)
            if keys:
                return types.InputRequiredResult(
                    input_requests={
                        key: types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message=f"Enter the {key.removesuffix('_address')} address.",
                                requested_schema=ADDRESS_SCHEMA,
                            )
                        )
                        for key in keys
                    },
                    request_state="addresses",
                )
        if state == "addresses":
            expected = (
                ("home_address", "business_address")
                if kind == "both"
                else (f"{kind}_address",)
            )
            if set(responses) != set(expected) or any(
                not isinstance(responses[key], types.ElicitResult)
                or responses[key].action != "accept"
                or responses[key].content != ADDRESSES[key]
                for key in expected
            ):
                return types.CallToolResult(content=[], is_error=True)
            return types.InputRequiredResult(
                input_requests={
                    "verification": types.ElicitRequest(
                        params=types.ElicitRequestURLParams(
                            message="Complete shipment verification.",
                            url="https://example.test/verify/123",
                        )
                    )
                },
                request_state="verification",
            )
        if state == "verification" and set(responses) == {"verification"}:
            response = responses["verification"]
            if isinstance(response, types.ElicitResult) and response.action == "accept":
                return types.CallToolResult(
                    content=[types.TextContent(text="Shipment verified.")],
                    structured_content={"status": "booked", "address_kind": kind},
                )
        return types.CallToolResult(content=[], is_error=True)

    return Server("shipping-composed", on_list_tools=list_tools, on_call_tool=call_tool)
