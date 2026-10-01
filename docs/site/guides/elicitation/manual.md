---
title: "Handle elicitation directly with the SDK"
description: "Read an InputRequiredResult, preserve its request state, and submit a response under the server request key."
---

# Handle elicitation directly with the SDK

Use manual handling when application code must inspect a server's
`InputRequiredResult` and decide how to answer. Pass the returned opaque
`request_state` and a response mapping under the returned request key on the
next call.

## Requirements

Install Python 3.10 or newer and the candidate from this checkout with
`python -m pip install -e 'sdk[pytest]'`. An index install of
`sf-m3[pytest]` selects a published release. This example uses MCP protocol
revision `2026-07-28` and local transports; it needs no model, credentials, or
network service.

## Complete server and test

Save as `shipping_server.py`:

```python
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
```

Save as `test_manual.py` beside it:

```python
from __future__ import annotations

from mcp import types
from shipping_server import build_server

from m3 import Config, InProcessServer, MCPTestKit
from m3.sync_api import InputRequiredResult, ToolCallResult


def test_manual_input_reuses_returned_state_and_keyed_response() -> None:
    calls: list[dict[str, object]] = []
    server = InProcessServer(name="shipping", factory=lambda: build_server(calls))
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            pending = client.call_tool(
                "book_shipment", {"weight_kg": 2}, allow_input_required=True
            )
            assert isinstance(pending, InputRequiredResult)
            assert set(pending.input_requests) == {"shipping_address"}
            assert pending.request_state == "shipping-address:opaque-v1"

            result = client.call_tool(
                "book_shipment",
                {"weight_kg": 2},
                request_state=pending.request_state,
                input_responses={
                    "shipping_address": types.ElicitResult(
                        action="accept",
                        content={"street": "1 Main Street", "city": "Pune"},
                    )
                },
            )

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "city": "Pune"}
    assert calls[1]["request_state"] == pending.request_state
    assert set(calls[1]["responses"]) == set(pending.input_requests)
```

From the directory containing both files, run:

```sh
python -m pytest -q test_manual.py
```

Captured output:

```text
1 passed
```

The assertions show that the first call returns a typed pending result, the
second call reuses its state verbatim and responds under `shipping_address`,
and the server returns a successful booking result. The server owns the
meaning of `request_state`; application code should preserve the returned
value rather than construct it.

Manual handling is mutually exclusive with a predefined `elicitation` plan
on the same direct operation. Combining them raises `ModelValidationError`.
Use the exact keys in `pending.input_requests`: an unknown key or a state that
the server does not recognize is sent to that server and may produce its
error result. A schema-invalid accepted form response raises
`ElicitationExpectationError` before the retry. Inspect each new
`InputRequiredResult` instead of reusing a previous round's keys.

The complete project is at
[`sdk/examples/docs/elicitation-manual`](../../../../sdk/examples/docs/elicitation-manual).
Use a plan for automatic test responses in
[Plan answers to elicitation requests](plans.md). For accept, decline, and
cancel behavior, see [Respond to elicitation requests](responses.md). For
multi-round ordering, see [Compose elicitation workflows](composed.md).
