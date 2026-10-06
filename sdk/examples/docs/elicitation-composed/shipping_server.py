from __future__ import annotations

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
    "required": ["street", "city"],
}
# Which address forms the server asks for in round 1, by `address_kind`.
ADDRESS_FORMS = {
    "home": ["home_address"],
    "business": ["business_address"],
    "both": ["home_address", "business_address"],
    "none": [],
}
VERIFICATION = types.ElicitRequest(
    params=types.ElicitRequestURLParams(
        message="Complete shipment verification.",
        url="https://example.test/verify/123",
    )
)


def address_form(key: str) -> types.ElicitRequest:
    return types.ElicitRequest(
        params=types.ElicitRequestFormParams(
            message=f"Enter the {key.replace('_', ' ')}.",
            requested_schema=ADDRESS_SCHEMA,
        )
    )


def accepted(response: object) -> bool:
    return isinstance(response, types.ElicitResult) and response.action == "accept"


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="book_verified_shipment",
                description="Collect addresses, then ask for verification",
                input_schema={
                    "type": "object",
                    "properties": {"address_kind": {"enum": list(ADDRESS_FORMS)}},
                    "required": ["address_kind"],
                },
            )
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    kind = (params.arguments or {})["address_kind"]
    forms = ADDRESS_FORMS[kind]
    responses = params.input_responses or {}
    state = params.request_state

    # First round: ask for every address form at once.
    if state is None and forms:
        return types.InputRequiredResult(
            input_requests={key: address_form(key) for key in forms},
            request_state="addresses",
        )
    # Next round: ask for verification once each address form is accepted.
    if state is None or (
        state == "addresses" and all(accepted(responses.get(k)) for k in forms)
    ):
        return types.InputRequiredResult(
            input_requests={"verification": VERIFICATION},
            request_state="verification",
        )
    if state == "verification" and accepted(responses.get("verification")):
        return types.CallToolResult(
            content=[types.TextContent(text="Shipment verified.")],
            structured_content={"status": "booked", "address_kind": kind},
        )
    return types.CallToolResult(
        content=[types.TextContent(text="unexpected response")], is_error=True
    )


def build_server() -> Server:
    return Server("shipping-composed", on_list_tools=list_tools, on_call_tool=call_tool)
