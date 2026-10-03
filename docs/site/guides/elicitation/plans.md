---
title: "Plan answers to elicitation requests"
description: "An elicitation plan describes the requests an operation may make and the response M3 should submit. Attach the plan to the action that can trigger those requests, then assert the action’s result."
---

# Plan answers to elicitation requests

An elicitation plan describes the requests an operation may make and the response M3 should submit. Attach the plan to the action that can trigger those requests, then assert the action’s result.

## Requirements and support

Direct SDK operations can use elicitation without an agent harness. M3 tests agent-driven elicitation with Codex CLI `0.156.1` and Pi `0.85.1`; other harnesses have not been verified for this action. This limit does not apply to direct SDK elicitation. See [compatibility details](../../reference/compatibility.md).

This example uses a local MCP server that returns `InputRequiredResult` for `book_shipment`, asks for the `shipping_address` form, and completes only when it receives the keyed response. The [runnable project](../../../../sdk/examples/docs/elicitation-plans) contains that server as `elicitation_server.py` and the test as `test_plan.py`.

The server fixture emits keyed `InputRequiredResult` and validates the next
call's `requestState` and `inputResponses`.

## Bind a form answer to a direct operation

Save this as `test_plan.py` beside `elicitation_server.py`:

```python
from elicitation_server import ADDRESS_SCHEMA, build_server

from m3 import Config, InProcessServer, MCPTestKit, expect_form
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
                {"weight_kg": 2, "zone": "local"},
                elicitation=plan,
            )

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "city": "Pune"}
```

From the directory containing the two files, run `python -m pytest -q test_plan.py`. The expected result is one passing test. It proves that the direct client matched the request and submitted the planned response; the server then returned a booked result.

The plan’s request key, mode, server, and operation must match the request sent by the server. A mismatch raises an elicitation expectation error. A form acceptance needs a mapping; URL acceptance uses `.accept()` without form content.

To require a second request in a later protocol round, compose bound leaves with `sequence(...)`. Use `one_of(...)` when one listed request may arrive, and `round_of(...)` when all listed requests belong to the same round. The server must exhibit that ordering; these helpers do not cause the server to ask.

The tool assertion runs after the action because one logical call owns all
retries. Assert one logical operation and its ordered attempts.

## Round limit

Each `InputRequiredResult` the server returns during an action counts as one round, including a result that carries only `requestState`. A plan that chains three steps with `sequence(...)` needs at least three rounds. The limit is 10 by default; pass `elicitation_round_limit` beside `elicitation` to change it.

When a direct operation goes past the limit, the client raises `ElicitationRoundLimitError`. In an agent test, the turn fails instead.

Codex `0.156.1` fails the action when a server asks for a tenth round, so a Codex action can use at most 9 rounds. Setting `elicitation_round_limit` above 9 does not raise that cap. With Pi, M3 enforces the limit you pass. Managed input on Pi has its own maximum, listed in [compatibility details](../../reference/compatibility.md).

Tool calls, prompt retrieval, resource reads, and agent actions bind the plan at different API boundaries. Agent-driven elicitation also depends on harness capability, and ACP agents that ask before calling tools need explicit tool approval through `permission_policy`. Identify the execution mode before applying an example. Next: [submit input to a paused execution](managed-input.md).
