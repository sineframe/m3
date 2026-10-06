from __future__ import annotations

from mcp import types
from mcp.server.lowlevel import Server

CITY_FORM = types.ElicitRequestFormParams(
    message="Enter your city.",
    requested_schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)
CHECKOUT_URL = types.ElicitRequestURLParams(
    message="Continue checkout.",
    url="https://example.test/checkout/123",
)


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="request_input",
                description="Ask for a form or URL response and report the answer",
                input_schema={
                    "type": "object",
                    "properties": {"mode": {"enum": ["form", "url"]}},
                    "required": ["mode"],
                },
            )
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    if params.request_state is None:
        mode = (params.arguments or {})["mode"]
        request = CITY_FORM if mode == "form" else CHECKOUT_URL
        return types.InputRequiredResult(
            input_requests={"input": types.ElicitRequest(params=request)},
            request_state="awaiting-input",
        )

    # Report what M3 sent so the test can check it.
    response = (params.input_responses or {})["input"]
    return types.CallToolResult(
        content=[types.TextContent(text="Response received.")],
        structured_content={"action": response.action, "content": response.content},
    )


def build_server() -> Server:
    return Server(
        "elicitation-responses", on_list_tools=list_tools, on_call_tool=call_tool
    )
