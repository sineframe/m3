---
title: "Handle elicitation in agent tests"
description: "Test action-bound elicitation in Codex and Pi sessions with a fresh plan for each turn."
---

# Handle elicitation in agent tests

Attach a complete elicitation plan to the agent action that may request input.
Give each `session.send` turn its own plan, then read that turn's trace for the
elicitation evidence.

## Requirements

Works with Python 3.10 or newer, Codex CLI `0.156.1`, and Pi `0.85.1`. The
examples acquire both harness versions through managed runtimes. Other
harnesses are unverified; direct SDK elicitation does not require a harness.

From the repository root, install the candidate package with
`python -m pip install -e 'sdk[pytest]'`. Installing `sf-m3[pytest]` from the
package index selects a published release. Authenticate each provider and set
its model variable to a model available to that account:

```sh
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
export M3_DOCS_PI_MODEL='<model available to your Pi provider>'
```

The provider calls need network access and credentials. The MCP server runs
locally over stdio.

## Complete server and two session examples

Create a directory named `elicitation-agents`. Save this server as
`shipping_server.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

ADDRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
    "required": ["street", "city"],
}


def build_server() -> Server:
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

    async def call_tool(_context: object, params: types.CallToolRequestParams):
        responses = params.input_responses or {}
        if params.name == "book_shipment":
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
                    request_state="address",
                    meta={
                        "io.modelcontextprotocol/serverInfo": {
                            "name": "shipping-agent-elicitation",
                            "version": "1.0.0",
                        }
                    },
                )
            address = responses.get("shipping_address")
            if (
                isinstance(address, types.ElicitResult)
                and address.action == "accept"
                and address.content == {"street": "1 Main Street", "city": "Pune"}
            ):
                return types.CallToolResult(
                    content=[types.TextContent(text="Shipment booked.")],
                    structured_content={"status": "booked"},
                )
        elif params.name == "book_verified_shipment":
            if params.request_state is None:
                return types.InputRequiredResult(
                    input_requests={
                        "business_address": types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message="Enter the business delivery address.",
                                requested_schema=ADDRESS_SCHEMA,
                            )
                        )
                    },
                    request_state="business-address",
                    meta={
                        "io.modelcontextprotocol/serverInfo": {
                            "name": "shipping-agent-elicitation",
                            "version": "1.0.0",
                        }
                    },
                )
            address = responses.get("business_address")
            if (
                params.request_state == "business-address"
                and isinstance(address, types.ElicitResult)
                and address.action == "accept"
                and address.content == {"street": "2 Business Street", "city": "Pune"}
            ):
                return types.CallToolResult(
                    content=[types.TextContent(text="Shipment booked.")],
                    structured_content={
                        "status": "booked",
                        "address_kind": "business",
                    },
                )
        return types.CallToolResult(
            content=[types.TextContent(text="Invalid response.")], is_error=True
        )

    return Server(
        "shipping-agent-elicitation", on_list_tools=list_tools, on_call_tool=call_tool
    )


async def serve() -> None:
    from mcp.server.stdio import stdio_server

    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(serve())
```

Save as `test_agent_sessions.py` in the same directory:

```python
from __future__ import annotations

import os
import sys
from pathlib import Path

from m3 import MCPTestKit, expect_form
from m3.observability import ToolCallStatus
from m3.types import ExecutionOutcome, StdioServer, TurnOutcome

HERE = Path(__file__).resolve().parent
ADDRESS = {"street": "1 Main Street", "city": "Pune"}
BUSINESS = {"street": "2 Business Street", "city": "Pune"}


def server() -> StdioServer:
    return StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )


def run_two_turns(harness: str, version: str, model_variable: str) -> None:
    selected_model = os.environ[model_variable]
    shipping = server()
    with MCPTestKit(env={}) as kit:
        agent = kit.agents(
            [
                {
                    "harness": harness,
                    "models": [selected_model],
                    "runtime": "managed",
                    "version": version,
                }
            ]
        )[0]
        first_plan = expect_form(
            "shipping_address",
            message="Enter the delivery address.",
            server=shipping,
            operation_kind="tool",
            operation_name="book_shipment",
        ).accept(ADDRESS)
        second_plan = expect_form(
            "business_address",
            message="Enter the business delivery address.",
            server=shipping,
            operation_kind="tool",
            operation_name="book_verified_shipment",
        ).accept(BUSINESS)
        with agent.session(
            server=shipping,
            tools=["shipping:book_shipment", "shipping:book_verified_shipment"],
            timeout=120,
            permission_policy="allow",
        ) as session:
            first = session.send(
                "Book one 2 kg shipment and report its status.",
                elicitation=first_plan,
                timeout=120,
            )
            assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
            second = session.send(
                "Book one business shipment and report its status.",
                elicitation=second_plan,
                timeout=120,
            )
        assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
        assert second.snapshot.outcome is TurnOutcome.COMPLETED, second.error
        assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
        trace = session.result.trace_view
        assert trace is not None
        first_view = trace.for_turn(first)
        second_view = trace.for_turn(second)
        first_elicitations = first_view.elicitations
        second_elicitations = second_view.elicitations
        assert [item.request_key for item in first_elicitations] == ["shipping_address"]
        assert first_elicitations[0].action == "accept"
        assert first_elicitations[0].content.value == ADDRESS
        assert [item.request_key for item in second_elicitations] == [
            "business_address"
        ]
        assert [item.action for item in second_elicitations] == ["accept"]
        assert second_elicitations[0].content.value == BUSINESS
        assert len(first_view.tool_calls) == len(second_view.tool_calls) == 1
        first_call = first_view.tool_calls[0]
        second_call = second_view.tool_calls[0]
        assert first_call.tool_status is ToolCallStatus.SUCCESS
        assert second_call.tool_status is ToolCallStatus.SUCCESS
        assert first_call.tool.value == "book_shipment"
        assert second_call.tool.value == "book_verified_shipment"
        assert first_call.result.value.is_error is False
        assert first_call.result.value.structured_content.value["status"] == "booked"
        assert second_call.result.value.is_error is False
        assert second_call.result.value.structured_content.value["status"] == "booked"


def test_codex_uses_fresh_plan_for_each_session_turn() -> None:
    run_two_turns("codex", "0.156.1", "M3_DOCS_CODEX_MODEL")


def test_pi_uses_fresh_plan_for_each_session_turn() -> None:
    run_two_turns("pi", "0.85.1", "M3_DOCS_PI_MODEL")
```

Run each harness separately from `elicitation-agents`:

```sh
python -m pytest -q test_agent_sessions.py -k codex
python -m pytest -q test_agent_sessions.py -k pi
```

No live transcript is included because this documentation build did not call
the providers. Both tests pass with the pinned harness binaries and local
provider fixtures. Each test reads the trace for its own turn and checks the
accepted form content and successful tool result. Fixture-backed runs cover
the adapter integration, not live model tool choice or account authentication.
The runnable source project is
[`sdk/examples/docs/elicitation-agents`](../../../../sdk/examples/docs/elicitation-agents).

If the agent never uses a required plan, M3 marks the turn incomplete. Pi's
managed-input control protocol accepts a round limit from 1 through 1024.
Codex has no matching adapter-specific maximum. Both examples use the default
limit of 10.

The stdio server uses form requests on both turns. Codex `0.156.1` rejects
a URL-mode `InputRequiredResult` in this server's tool call with
`unsupported MCP tool input request`. Do not add a URL step to this native
example. To test URL matching and acceptance without an agent, use
[Compose elicitation workflows](composed.md). URL acceptance submits an action;
it does not visit the URL. For a human response that pauses an execution, see
[Submit input to a paused execution](managed-input.md). See the
[elicitation reference](../../reference/python/m3/elicitation.md) for exact
plan behavior.
