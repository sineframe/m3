---
title: "Handle elicitation in agent tests"
description: "Test action-bound elicitation in Codex and Pi sessions with a fresh plan for each turn."
---

# Handle elicitation in agent tests

When an agent calls a tool that asks for input, M3 answers from the plan
attached to that agent action. In a session, attach a plan to each
`session.send` turn. A plan covers only the turn it is passed to.

## Requirements

Use Python 3.10 or later and a project set up with `m3 init` and `m3 setup`,
as in [Write your first MCP test](../../getting-started.md). Agent-driven
elicitation works with Codex and Pi. Other harnesses are unverified. The test
calls a real model, so it needs network access, provider credentials, and a
model available to your account:

```sh
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
```

## Example

Create a directory named `elicitation-agents`. The server has two tools:
`book_shipment` asks for a `shipping_address` form, and
`book_verified_shipment` asks for a `business_address` form. Each one books the
shipment after the form is accepted.

Save as `shipping_server.py`:

```python
from __future__ import annotations

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
    "required": ["street", "city"],
}
# Each tool asks for one address form before it books.
ADDRESS_FORMS = {
    "book_shipment": ("shipping_address", "Enter the delivery address."),
    "book_verified_shipment": (
        "business_address",
        "Enter the business delivery address.",
    ),
}


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="book_shipment",
                description="Book a shipment after requesting its address",
                input_schema={
                    "type": "object",
                    "properties": {"weight_kg": {"type": "number"}},
                    "required": ["weight_kg"],
                },
            ),
            types.Tool(
                name="book_verified_shipment",
                description="Book a shipment after requesting its business address",
                input_schema={
                    "type": "object",
                    "properties": {"address_kind": {"type": "string"}},
                    "required": ["address_kind"],
                },
            ),
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    key, message = ADDRESS_FORMS[params.name]
    if params.request_state is None:
        return types.InputRequiredResult(
            input_requests={
                key: types.ElicitRequest(
                    params=types.ElicitRequestFormParams(
                        message=message, requested_schema=ADDRESS_SCHEMA
                    )
                )
            },
            request_state=key,
        )

    response = (params.input_responses or {}).get(key)
    if not isinstance(response, types.ElicitResult) or response.action != "accept":
        return types.CallToolResult(
            content=[types.TextContent(text="address was not provided")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="Shipment booked.")],
        structured_content={"status": "booked"},
    )


async def main() -> None:
    server: Server[object] = Server(
        "shipping-agent-elicitation",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
```

Save as `test_agent_sessions.py` in the same directory:

```python
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from m3 import ExecutionOutcome, expect, expect_form
from m3.types import StdioServer, TurnOutcome

HERE = Path(__file__).resolve().parent
HOME = {"street": "1 Main Street", "city": "Pune"}
BUSINESS = {"street": "2 Business Street", "city": "Pune"}

pytestmark = pytest.mark.m3(suite_name="elicitation")


def test_each_turn_gets_its_own_plan(agent) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    with agent.session(
        server=server,
        tools=["shipping:book_shipment", "shipping:book_verified_shipment"],
        timeout=120,
    ) as session:
        # Each turn gets a plan for the form its tool will request.
        first = session.send(
            "Book one 2 kg shipment and report its status.",
            elicitation=expect_form("shipping_address").accept(HOME),
            timeout=120,
        )
        second = session.send(
            "Book one business shipment and report its status.",
            elicitation=expect_form("business_address").accept(BUSINESS),
            timeout=120,
        )
    result = session.result

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
    assert second.snapshot.outcome is TurnOutcome.COMPLETED, second.error
    expect(result).to_have_tool_call(
        "book_shipment", turn=first, status="success", count=1
    )
    expect(result).to_have_tool_call(
        "book_verified_shipment", turn=second, status="success", count=1
    )

    # The trace records which request each turn answered and with what.
    trace = result.trace_view
    assert trace is not None
    answered = [
        (item.request_key, item.action, item.content.value)
        for turn in (first, second)
        for item in trace.for_turn(turn).elicitations
    ]
    assert answered == [
        ("shipping_address", "accept", HOME),
        ("business_address", "accept", BUSINESS),
    ]
```

The test takes M3's `agent` fixture, so the harness and model come from the
command line. Run it from `elicitation-agents`:

```sh
m3 test --harness "codex=${M3_DOCS_CODEX_MODEL}" -- test_agent_sessions.py
```

To run a specific harness version, add `--runtime managed` and put the version
in the selector. For example, the version this guide was tested with:

```sh
m3 test --runtime managed --harness "codex@0.156.1=${M3_DOCS_CODEX_MODEL}" \
  -- test_agent_sessions.py
```

For Pi, pass `--harness "pi=<provider>/<model>"` and the provider credential;
[Add another harness](../agents/versions.md#add-another-harness) shows the
credential setup. This guide was also tested with Pi `0.85.1`.

The first turn's plan answers only `shipping_address`, and the second turn's
plan answers only `business_address`. `expect(result).to_have_tool_call(...)`
with `turn=` checks that each tool succeeded in its own turn.
`trace.for_turn(turn).elicitations` lists the requests answered during that
turn, with the key, action, and content M3 sent.

The test depends on the model choosing the tool the prompt names. If the agent
never triggers a request that its plan requires, the turn fails. The
`tools=[...]` list limits the agent to the two shipping tools, and M3 approves
calls to them from that list.

The complete project is in
[`sdk/examples/docs/elicitation-agents`](../../../../sdk/examples/docs/elicitation-agents).

## Harness differences

The test uses the default round limit of 10. Codex `0.156.1`, for example,
stops an action at 9 rounds even when you set a higher limit; see
[Round limit](plans.md#round-limit). Pi's managed-input control protocol
accepts a limit from 1 through 1024.

This example uses form requests only. For URL requests and combined plans
without an agent, see [Compose elicitation workflows](composed.md).

For a person answering while the agent waits, see
[Submit input to a paused execution](managed-input.md). The
[elicitation reference](../../reference/python/m3/elicitation.md) describes how
plans match requests.
