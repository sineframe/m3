from __future__ import annotations

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
}


def build_server(calls: list[dict[str, object]]) -> Server:
    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="request_input",
                    description="Request one form or URL response",
                    input_schema={
                        "type": "object",
                        "properties": {"mode": {"type": "string"}},
                        "required": ["mode"],
                    },
                )
            ]
        )

    async def call_tool(
        _context: object, params: types.CallToolRequestParams
    ) -> types.CallToolResult | types.InputRequiredResult:
        state = params.request_state
        responses = params.input_responses or {}
        mode = (params.arguments or {}).get("mode")
        calls.append(
            {
                "state": state,
                "responses": {
                    key: item.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for key, item in responses.items()
                },
            }
        )
        if state is None:
            if mode == "form":
                request = types.ElicitRequest(
                    params=types.ElicitRequestFormParams(
                        message="Enter your city.", requested_schema=ADDRESS_SCHEMA
                    )
                )
            elif mode == "url":
                request = types.ElicitRequest(
                    params=types.ElicitRequestURLParams(
                        message="Continue checkout.",
                        url="https://example.test/checkout/123",
                    )
                )
            else:
                return types.CallToolResult(content=[], is_error=True)
            return types.InputRequiredResult(
                input_requests={"input": request}, request_state=f"{mode}-state"
            )
        response = responses.get("input")
        return types.CallToolResult(
            content=[types.TextContent(text="Response received.")],
            structured_content={
                "action": response.action,
                "content": response.content,
                "meta": response.meta,
            },
        )

    return Server(
        "elicitation-responses", on_list_tools=list_tools, on_call_tool=call_tool
    )
