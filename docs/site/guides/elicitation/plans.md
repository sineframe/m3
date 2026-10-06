---
title: "Plan answers to elicitation requests"
description: "Build an elicitation plan, attach it to one action, and assert the result."
---

# Plan answers to elicitation requests

An MCP server can pause a tool call to ask the client for input, such as a form
to fill in or a URL to visit. An elicitation plan tells M3 which requests to
expect and how to answer each one, so a test can run the whole exchange without
a person.

## Requirements

Use Python 3.10 or later and a project set up with `m3 init` and `m3 setup`,
as in [Write your first MCP test](../../getting-started.md). Elicitation needs MCP protocol revision `2026-07-28`, so the
examples set it in `Config`. Direct SDK calls like the ones below need no agent
harness, model, or credentials. Agent-driven elicitation works with Codex and Pi; it
was tested with Codex CLI `0.156.1` and Pi `0.85.1`. See
[compatibility details](../../reference/compatibility.md).

## Example

The server has one tool, `book_shipment`. The first call returns an
`InputRequiredResult` asking for a `shipping_address` form. When the client
retries with an accepted address, the tool books the shipment.

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
from elicitation_server import build_server

from m3 import (
    Config,
    ElicitationExpectationError,
    ElicitationPlan,
    InProcessServer,
    MCPTestKit,
    expect_form,
)

pytestmark = pytest.mark.m3(suite_name="elicitation")

ADDRESS = {"street": "1 Main Street", "city": "Pune"}


def book_shipment(plan: ElicitationPlan):
    server = InProcessServer(name="shipping", factory=build_server)
    config = Config(protocol_revision="2026-07-28")
    with MCPTestKit(config=config, env={}) as kit, kit.direct(server) as client:
        return client.call_tool("book_shipment", {"weight_kg": 2}, elicitation=plan)


def test_plan_answers_the_address_form() -> None:
    plan = expect_form("shipping_address").accept(ADDRESS)

    result = book_shipment(plan)

    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "city": "Pune"}


def test_wrong_request_key_fails() -> None:
    plan = expect_form("billing_address").accept(ADDRESS)

    with pytest.raises(ElicitationExpectationError, match="did not match"):
        book_shipment(plan)


def test_content_must_match_the_server_schema() -> None:
    plan = expect_form("shipping_address").accept({"street": 3, "city": "Pune"})

    with pytest.raises(ElicitationExpectationError, match="schema"):
        book_shipment(plan)
```

Run:

```sh
m3 test -- test_plan.py
```

The summary ends with:

```text
M3 verdicts: 3 passed
```

`expect_form("shipping_address")` matches a form request with that key, and
`.accept(...)` sets the content M3 sends back. With the plan attached,
`call_tool` makes the first call, answers the request, retries, and returns the
final `ToolCallResult`.

M3 checks each response against the request before it retries.
`test_wrong_request_key_fails` expects `billing_address`, which the server never
asks for. `test_content_must_match_the_server_schema` sends a number where the
server's schema wants a string. Both raise `ElicitationExpectationError`, and
the server never receives the response.

The complete project is in
[`sdk/examples/docs/elicitation-plans`](../../../../sdk/examples/docs/elicitation-plans).

## Narrow what a plan matches

A plan matches a request by its key and mode (form or URL). `expect_form` also
takes `message`, `schema`, `server`, `operation_kind`, and `operation_name`.
`expect_url` takes `message`, `url`, `elicitation_id`, `server`,
`operation_kind`, and `operation_name`. Each one you pass must match the request
as well, which helps when two servers or tools use the same key.

`expect_url(...).accept()` takes no content. Accepting a URL request sends the
`accept` action; M3 does not visit the URL or complete any sign-in behind it.
For declined and cancelled answers, see
[Respond to elicitation requests](responses.md).

## Plan several requests

Use `sequence(...)` for requests that arrive in later rounds, `one_of(...)` for
alternatives, `optional(...)` for a step that may not happen, and
`round_of(...)` for requests the server sends together. The server still
decides what to ask and when, and the plan has to fit what it does.
[Compose elicitation workflows](composed.md) has an example of each.

## Round limit

Each `InputRequiredResult` the server returns during an action counts as one
round, including a result that carries only `requestState`. A plan that chains
three steps with `sequence(...)` needs at least three rounds. The limit is 10 by
default; pass `elicitation_round_limit` beside `elicitation` to change it.

When a direct operation goes past the limit, the client raises
`ElicitationRoundLimitError`. In an agent test, the turn fails instead.

A harness can stop earlier than M3's limit. Codex `0.156.1`, for example, fails
the action when a server asks for a tenth round, so a Codex action can use at most 9
rounds, and setting `elicitation_round_limit` above 9 does not raise that cap.
With Pi, M3 enforces the limit you pass. Managed input on Pi has its own
maximum, listed in [compatibility details](../../reference/compatibility.md).

## Where to attach a plan

Direct tool calls, prompt retrieval, and resource reads take `elicitation=` on
the call itself. For agents, pass the plan to `agent.run`, `agent.submit`, or a
single `session.send` turn, not to `agent.session(...)`. ACP agents that ask
before calling tools also need tool approval through `permission_policy`.

- [Compose elicitation workflows](composed.md) covers alternatives, optional
  steps, and later rounds.
- [Handle elicitation in agent tests](agents.md) attaches plans to Codex and Pi
  turns.
- [Handle elicitation directly with the SDK](manual.md) answers the request in
  your own code.
- [Submit input to a paused execution](managed-input.md) lets a person answer
  while an agent waits.
