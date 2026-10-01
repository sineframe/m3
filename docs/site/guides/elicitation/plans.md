---
title: "Plan answers to elicitation requests"
description: "An elicitation plan describes the requests an operation may make and the response M3 should submit. Attach the plan to the action that can trigger those requests, then assert the action’s result."
---

# Plan answers to elicitation requests

An elicitation plan describes the requests an operation may make and the response M3 should submit. Attach the plan to the action that can trigger those requests, then assert the action’s result.

## Requirements and support

Direct SDK operations can use elicitation without an agent harness. M3 tests agent-driven elicitation with Codex CLI `0.156.1` and Pi `0.85.1`; other harnesses have not been verified for this action. This limit does not apply to direct SDK elicitation. See [compatibility details](../../reference/compatibility.md).

Works with Python 3.10 or newer and MCP protocol revision `2026-07-28`.
From the repository root, install the candidate
package and pytest with
`python -m pip install -e 'sdk[pytest]'`. Installing `sf-m3[pytest]` from the
package index selects a published release. The example uses a local
in-process server; the test needs no model, network service, or credentials.

This direct SDK example uses a local MCP server that returns
`InputRequiredResult` for `book_shipment`, asks for the `shipping_address`
form, and completes only when it receives the keyed response. It needs no
agent harness.

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

The first test proves that the direct client matched the request and submitted
the planned response; the server then returned a booked result. The second
test proves that a wrong key and content that fails the requested schema raise
`ElicitationExpectationError` before the retry. The complete source project is also available at
[`sdk/examples/docs/elicitation-plans`](../../../../sdk/examples/docs/elicitation-plans).

The plan’s request key and mode must match the server request. Context fields
such as server and operation match when provided. A mismatch raises
`ElicitationExpectationError`. A form acceptance needs a mapping; URL
acceptance uses `.accept()` without form content. A URL response does not
visit the URL or complete authentication. See [response semantics](responses.md)
for declined and cancelled responses.

To require a second request in a later protocol round, compose bound leaves with `sequence(...)`. Use `one_of(...)` when one listed request may arrive, and `round_of(...)` when all listed requests belong to the same round. The server must exhibit that ordering; these helpers do not cause the server to ask.

Tool calls, prompt retrieval, and resource reads accept plans on the direct
operation. Agent actions bind a plan to `agent.run`, `agent.submit`, or one
`session.send` turn. Do not put a plan on long-lived session creation. The
[composed workflow](composed.md) demonstrates alternatives and later rounds;
[agent-specific guides](agents.md) describe Codex and Pi support; and the
[manual guide](manual.md) handles a returned `InputRequiredResult`. Next:
[submit input to a paused execution](managed-input.md).
