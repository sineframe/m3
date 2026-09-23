"""Low-level MCP SDK 2.0 server used by direct MRTR integration tests."""

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


def build_modern_mrtr_server(
    observed_tool_calls: list[dict[str, object]] | None = None,
) -> Server:
    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="book_shipment",
                    description="Book a shipment after collecting an address.",
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
                    name="checkout",
                    description="Request URL authorization.",
                    input_schema={"type": "object"},
                ),
                types.Tool(
                    name="multi_round",
                    description="Request two separate rounds.",
                    input_schema={"type": "object"},
                ),
                types.Tool(
                    name="same_round",
                    description="Request two keyed elicitations in one round.",
                    input_schema={"type": "object"},
                ),
                types.Tool(
                    name="ten_rounds",
                    description="Request ten rounds before completion.",
                    input_schema={"type": "object"},
                ),
                types.Tool(
                    name="sampling_round",
                    description="Request client sampling.",
                    input_schema={"type": "object"},
                ),
                types.Tool(
                    name="roots_round",
                    description="Request client roots.",
                    input_schema={"type": "object"},
                ),
                types.Tool(
                    name="mixed_round",
                    description="Request elicitation and sampling together.",
                    input_schema={"type": "object"},
                ),
            ]
        )

    async def call_tool(
        context: object,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult | types.InputRequiredResult:
        marker = os.environ.get("M3_MRTR_WIRE_MARKER")
        if marker:
            request_id = getattr(context, "request_id", None)
            record = {
                "id": str(request_id) if request_id is not None else None,
                "name": params.name,
                "arguments": params.arguments,
                "requestState": params.request_state,
                "inputResponses": {
                    key: value.model_dump(mode="json", by_alias=True)
                    if hasattr(value, "model_dump")
                    else value
                    for key, value in (params.input_responses or {}).items()
                },
            }
            with open(marker, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        if os.environ.get("M3_MRTR_BLOCK_RETRY") and params.request_state is not None:
            await asyncio.Event().wait()
        if observed_tool_calls is not None:
            observed_tool_calls.append(
                {
                    "request_id": getattr(context, "request_id", None),
                    "name": params.name,
                    "arguments": params.arguments,
                    "meta": params.meta,
                    "request_state": params.request_state,
                    "input_responses": dict(params.input_responses or {}),
                }
            )
        if params.name == "checkout":
            if not params.input_responses:
                return types.InputRequiredResult(
                    input_requests={
                        "checkout": types.ElicitRequest(
                            params=types.ElicitRequestURLParams(
                                message="Continue checkout.",
                                url="https://example.test/checkout/123",
                                elicitation_id="checkout-123",
                            )
                        )
                    },
                    request_state="checkout-state",
                )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="checked out")],
                structured_content={"status": "authorized"},
            )

        if params.name == "multi_round":
            responses = params.input_responses or {}
            if params.request_state is None:
                if set(responses) != set():
                    return types.CallToolResult(
                        content=[
                            types.TextContent(type="text", text="bad first round")
                        ],
                        is_error=True,
                    )
                return types.InputRequiredResult(
                    input_requests={
                        "address": types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message="Address",
                                requested_schema={"type": "object", "properties": {}},
                            )
                        )
                    },
                    request_state="",
                )
            if params.request_state in {"", "multi-address"}:
                if set(responses) != {"address"}:
                    return types.CallToolResult(
                        content=[
                            types.TextContent(type="text", text="bad second input")
                        ],
                        is_error=True,
                    )
                return types.InputRequiredResult(
                    input_requests={
                        "contact": types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message="Contact",
                                requested_schema={"type": "object", "properties": {}},
                            )
                        )
                    },
                    request_state="multi-contact",
                )
            if params.request_state == "multi-contact":
                if set(responses) != {"contact"}:
                    return types.CallToolResult(
                        content=[
                            types.TextContent(type="text", text="old input leaked")
                        ],
                        is_error=True,
                    )
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="complete")],
                    structured_content={"status": "complete"},
                )

        if params.name == "same_round":
            responses = params.input_responses or {}
            if not responses:
                return types.InputRequiredResult(
                    input_requests={
                        "address": types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message="Address",
                                requested_schema={"type": "object", "properties": {}},
                            )
                        ),
                        "contact": types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message="Contact",
                                requested_schema={"type": "object", "properties": {}},
                            )
                        ),
                    },
                    request_state="same-round",
                )
            if set(responses) != {"address", "contact"}:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="wrong keys")],
                    is_error=True,
                )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="complete")],
                structured_content={"status": "complete"},
            )

        if params.name == "ten_rounds":
            round_number = (
                1 if params.request_state is None else int(params.request_state) + 1
            )
            requested_rounds = int((params.arguments or {}).get("rounds", 10))
            if round_number <= requested_rounds:
                expected_key = f"round-{round_number}"
                if set(params.input_responses or {}) != (
                    set() if round_number == 1 else {f"round-{round_number - 1}"}
                ):
                    return types.CallToolResult(
                        content=[
                            types.TextContent(type="text", text="old input leaked")
                        ],
                        is_error=True,
                    )
                return types.InputRequiredResult(
                    input_requests={
                        expected_key: types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message=expected_key,
                                requested_schema={"type": "object", "properties": {}},
                            )
                        )
                    },
                    request_state=str(round_number),
                )
            if round_number == requested_rounds + 1:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="complete")],
                    structured_content={"status": "complete"},
                )

        if params.name in {"sampling_round", "roots_round", "mixed_round"}:
            key = (
                "sampling"
                if params.name == "sampling_round"
                else "roots"
                if params.name == "roots_round"
                else "sampling"
            )
            method = "sampling/createMessage" if key == "sampling" else "roots/list"
            if not params.input_responses:
                request: types.CreateMessageRequest | types.ListRootsRequest
                if key == "sampling":
                    request = types.CreateMessageRequest(
                        params=types.CreateMessageRequestParams(
                            messages=[],
                            max_tokens=16,
                        )
                    )
                else:
                    request = types.ListRootsRequest()
                requests: dict[str, Any] = {key: request}
                if params.name == "mixed_round":
                    requests["approval"] = types.ElicitRequest(
                        params=types.ElicitRequestFormParams(
                            message="Approve",
                            requested_schema={"type": "object", "properties": {}},
                        )
                    )
                return types.InputRequiredResult(
                    input_requests=requests,
                    request_state=f"{method}-state",
                )
            expected_keys = (
                {"sampling", "approval"} if params.name == "mixed_round" else {key}
            )
            if set(params.input_responses or {}) != expected_keys:
                return types.CallToolResult(
                    content=[
                        types.TextContent(type="text", text="missing input response")
                    ],
                    is_error=True,
                )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="complete")],
                structured_content={"status": "complete"},
            )

        if params.name != "book_shipment":
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="unknown tool")],
                is_error=True,
            )

        address = (params.input_responses or {}).get("shipping_address")
        if address is None:
            return types.InputRequiredResult(
                input_requests={
                    "shipping_address": types.ElicitRequest(
                        params=types.ElicitRequestFormParams(
                            message="Enter the delivery address.",
                            requested_schema=ADDRESS_SCHEMA,
                        )
                    )
                },
                request_state="book-shipment-address",
            )
        if not isinstance(address, types.ElicitResult):
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="invalid response")],
                is_error=True,
            )

        return types.CallToolResult(
            content=[types.TextContent(type="text", text="Shipment booked.")],
            structured_content={"status": "booked", "address": address.content},
        )

    async def list_resources(
        _context: object, _params: object
    ) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    name="interactive-document",
                    uri="memory://interactive-document",
                    mime_type="text/plain",
                ),
                types.Resource(
                    name="interactive-url-document",
                    uri="memory://interactive-url-document",
                    mime_type="text/plain",
                ),
            ]
        )

    async def read_resource(
        _context: object,
        params: types.ReadResourceRequestParams,
    ) -> types.ReadResourceResult | types.InputRequiredResult:
        if not params.input_responses:
            if params.uri == "memory://interactive-url-document":
                return types.InputRequiredResult(
                    input_requests={
                        "resource_authorization": types.ElicitRequest(
                            params=types.ElicitRequestURLParams(
                                message="Authorize resource access",
                                url="https://example.test/resource/123",
                            )
                        )
                    },
                    request_state="resource-url-state",
                )
            return types.InputRequiredResult(
                input_requests={
                    "resource_access": types.ElicitRequest(
                        params=types.ElicitRequestFormParams(
                            message="Allow resource access",
                            requested_schema={"type": "object", "properties": {}},
                        )
                    )
                },
                request_state="resource-state",
            )
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=params.uri,
                    mime_type="text/plain",
                    text="resource contents",
                )
            ]
        )

    async def list_prompts(
        _context: object, _params: object
    ) -> types.ListPromptsResult:
        return types.ListPromptsResult(
            prompts=[
                types.Prompt(name="interactive-prompt"),
                types.Prompt(name="interactive-url-prompt"),
            ]
        )

    async def get_prompt(
        _context: object,
        params: types.GetPromptRequestParams,
    ) -> types.GetPromptResult | types.InputRequiredResult:
        if not params.input_responses:
            if params.name == "interactive-url-prompt":
                return types.InputRequiredResult(
                    input_requests={
                        "prompt_authorization": types.ElicitRequest(
                            params=types.ElicitRequestURLParams(
                                message="Authorize prompt access",
                                url="https://example.test/prompt/123",
                            )
                        )
                    },
                    request_state="prompt-url-state",
                )
            return types.InputRequiredResult(
                input_requests={
                    "prompt_context": types.ElicitRequest(
                        params=types.ElicitRequestFormParams(
                            message="Provide prompt context",
                            requested_schema={"type": "object", "properties": {}},
                        )
                    )
                },
                request_state="prompt-state",
            )
        return types.GetPromptResult(
            description="interactive prompt",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text="prompt contents"),
                )
            ],
        )

    return Server(
        "modern-mrtr",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_read_resource=read_resource,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
    )


async def _serve_stdio() -> None:
    from mcp.server.stdio import stdio_server

    server = build_modern_mrtr_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(_serve_stdio())
