---
title: "Plan answers to elicitation requests"
description: "Build an elicitation plan, attach it to one action, and assert the result."
---

# Plan answers to elicitation requests

An elicitation plan maps the requests an operation may make to the responses M3
should submit. Attach a complete plan to the action that can request input,
then assert the action’s result.

## Requirements and support

Works with Python 3.10 or newer and MCP protocol revision `2026-07-28` through
direct SDK operations. Direct tests need no agent harness. Agent-driven
elicitation is verified with Codex CLI `0.156.1` and Pi `0.85.1`; other
harnesses are unverified. See
[compatibility details](../../reference/compatibility.md).

From the repository root, install the candidate package and pytest with
`python -m pip install -e 'sdk[pytest]'`. Installing `sf-m3[pytest]` from the
package index selects a published release. The local in-process server needs no
model, network service, or credentials. It returns `InputRequiredResult` for
`book_shipment` and completes after receiving the keyed `shipping_address`
response.

## Complete local server and test

Save as `elicitation_server.py`:

```python
from __future__ import annotations

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {
        "street": {"type": "string"},
        "city": {"type": "string"},
    },
    "required": ["street", "city"],
}


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="book_shipment",
                description="Book a shipment after confirming a delivery address",
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
    responses = params.input_responses or {}
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

    response = responses.get("shipping_address")
    if (
        not isinstance(response, types.ElicitResult)
        or response.action != "accept"
        or response.content != {"street": "1 Main Street", "city": "Pune"}
    ):
        return types.CallToolResult(
            content=[types.TextContent(text="address response did not match")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="Shipment booked.")],
        structured_content={"status": "booked", "city": "Pune"},
    )


def build_server() -> Server:
    return Server(
        "shipping-elicitation",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
```

Save as `test_plan.py` beside it:

```python
import pytest
from elicitation_server import ADDRESS_SCHEMA, build_server

from m3 import (
    Config,
    ElicitationExpectationError,
    InProcessServer,
    MCPTestKit,
    expect_form,
)
from m3.sync_api import ToolCallResult


def test_direct_tool_call_answers_a_form_request() -> None:
    server = InProcessServer(name="shipping", factory=build_server)
    plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server="shipping",
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept({"street": "1 Main Street", "city": "Pune"})

    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool(
                "book_shipment",
                {"weight_kg": 2},
                elicitation=plan,
            )

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "city": "Pune"}


def test_request_key_mismatch_and_invalid_content_fail_before_retry() -> None:
    server = InProcessServer(name="shipping", factory=build_server)
    wrong_key = expect_form("address").accept(
        {"street": "1 Main Street", "city": "Pune"}
    )
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ElicitationExpectationError, match="did not match"):
                client.call_tool(
                    "book_shipment", {"weight_kg": 2}, elicitation=wrong_key
                )

    invalid_content = expect_form("shipping_address", schema=ADDRESS_SCHEMA).accept(
        {"street": 3, "city": "Pune"}
    )
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ElicitationExpectationError, match="schema"):
                client.call_tool(
                    "book_shipment", {"weight_kg": 2}, elicitation=invalid_content
                )
```

From the directory where you saved `elicitation_server.py` and `test_plan.py`,
run:

```sh
python -m pytest -q test_plan.py
```

Captured output:

```text
2 passed
```

`test_direct_tool_call_answers_a_form_request` passes after the direct client
matches the request, submits the planned response, and receives the booked
result. `test_request_key_mismatch_and_invalid_content_fail_before_retry`
raises `ElicitationExpectationError` for a wrong key or schema-invalid content
before the retry. The complete source project is at
[`sdk/examples/docs/elicitation-plans`](../../../../sdk/examples/docs/elicitation-plans).

The plan’s request key and mode must match the server request. Context fields
such as server and operation match when provided. A mismatch raises
`ElicitationExpectationError`. A form acceptance needs a mapping; URL
acceptance uses `.accept()` without form content. A URL response does not
visit the URL or complete authentication. See [response semantics](responses.md)
for declined and cancelled responses.

Compose bound leaves with `sequence(...)` when a second request must arrive in
a later protocol round. Use `one_of(...)` for alternatives and `round_of(...)`
for requests that must share a round. These helpers match server behavior;
they do not cause the server to issue requests.

Tool calls, prompt retrieval, and resource reads accept plans on the direct
operation. Agent actions bind a plan to `agent.run`, `agent.submit`, or one
`session.send` turn. Do not put a plan on long-lived session creation.

Continue with [Compose elicitation workflows](composed.md) for alternatives
and later rounds. For an agent action, see
[Handle elicitation in agent tests](agents.md). If application code needs the
raw `InputRequiredResult`, use [manual handling](manual.md). To collect a
response from a person, [submit input to a paused execution](managed-input.md).
