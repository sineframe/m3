<!-- Generated from docs/site/guides/elicitation/manual.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Handle elicitation directly with the SDK

Without a plan, your code can receive the server's `InputRequiredResult` and
answer it. Call the same tool again with the returned `request_state` and one
response for each requested key.

## Requirements

Use Python 3.10 or later with `sf-m3[pytest]` installed in the project
environment. The test uses an in-process server and needs no agent harness,
network service, or credentials.

## Example

The `book_shipment` tool asks for a `shipping_address` form, then books the
shipment and returns the address it received.

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


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
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
            request_state="awaiting-address",
        )

    response = (params.input_responses or {}).get("shipping_address")
    if (
        params.request_state != "awaiting-address"
        or not isinstance(response, types.ElicitResult)
        or response.action != "accept"
    ):
        return types.CallToolResult(
            content=[types.TextContent(text="address response did not match")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="Shipment booked.")],
        structured_content={"status": "booked", "address": response.content},
    )


def build_server() -> Server:
    return Server("shipping-manual", on_list_tools=list_tools, on_call_tool=call_tool)
```

Save as `test_manual.py` beside it:

```python
from __future__ import annotations

from mcp import types
from shipping_server import build_server

from m3 import Config, InProcessServer, MCPTestKit
from m3.sync_api import InputRequiredResult

ADDRESS = {"street": "1 Main Street", "city": "Pune"}


def test_answer_the_returned_request_yourself() -> None:
    server = InProcessServer(name="shipping", factory=build_server)
    config = Config(protocol_revision="2026-07-28")
    with MCPTestKit(config=config, env={}) as kit, kit.direct(server) as client:
        pending = client.call_tool(
            "book_shipment", {"weight_kg": 2}, allow_input_required=True
        )
        assert isinstance(pending, InputRequiredResult)
        assert set(pending.input_requests) == {"shipping_address"}

        result = client.call_tool(
            "book_shipment",
            {"weight_kg": 2},
            request_state=pending.request_state,
            input_responses={
                "shipping_address": types.ElicitResult(action="accept", content=ADDRESS)
            },
        )

    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "address": ADDRESS}
```

Run:

```sh
python -m pytest -q test_manual.py
```

Captured output:

```text
1 passed
```

`allow_input_required=True` makes the first call return the
`InputRequiredResult`. Without it, a direct call that receives a request and has
no plan raises `ElicitationExpectationError`. The second call passes
`pending.request_state` back unchanged and answers under the key from
`pending.input_requests`.

Treat `request_state` as opaque. The server decides what it means, so pass back
the value you received rather than building one. M3 sends the state, keys, and
content you provide without checking them against the request. The server has
to reject anything it doesn't accept; this one returns `is_error=True`.

If the server may ask again, pass `allow_input_required=True` on the retry as
well. The retry then returns a new `InputRequiredResult`; read the keys from it
instead of reusing the previous ones.

You can't combine `elicitation=` with `allow_input_required`, `request_state`,
or `input_responses` on one call; M3 raises `ModelValidationError`.

The complete project is in
[`sdk/examples/docs/elicitation-manual`](../examples/elicitation-manual).
To let M3 answer for you, see
[Plan answers to elicitation requests](guides-elicitation-plans.md).
