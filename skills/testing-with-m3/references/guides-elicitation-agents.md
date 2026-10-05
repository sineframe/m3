<!-- Generated from docs/site/guides/elicitation/agents.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Handle elicitation in agent tests

When an agent calls a tool that asks for input, M3 answers from the plan
attached to that agent action. In a session, attach a plan to each
`session.send` turn. A plan covers only the turn it is passed to.

## Requirements

Use Python 3.10 or later with `sf-m3[pytest]` installed in the project
environment. The example runs Codex CLI `0.156.1` and Pi `0.85.1`, which M3
downloads as managed runtimes. Other harnesses are unverified for agent-driven
elicitation. Both tests call a real model, so they need network access,
provider credentials, and a model name for each harness:

```sh
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
export M3_DOCS_PI_MODEL='<model available to your Pi provider>'
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

import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect, expect_form
from m3.types import StdioServer, TurnOutcome

HERE = Path(__file__).resolve().parent
HOME = {"street": "1 Main Street", "city": "Pune"}
BUSINESS = {"street": "2 Business Street", "city": "Pune"}


def run_two_turns(harness: str, version: str, model: str) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    with MCPTestKit(env={}) as kit:
        agent = kit.agents(
            [
                {
                    "harness": harness,
                    "models": [model],
                    "runtime": "managed",
                    "version": version,
                }
            ]
        )[0]
        with agent.session(
            server=server,
            tools=["shipping:book_shipment", "shipping:book_verified_shipment"],
            timeout=120,
            permission_policy="allow",
        ) as session:
            # Each turn gets its own plan for the form its tool will request.
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


def test_codex_uses_fresh_plan_for_each_session_turn() -> None:
    run_two_turns("codex", "0.156.1", os.environ["M3_DOCS_CODEX_MODEL"])


def test_pi_uses_fresh_plan_for_each_session_turn() -> None:
    run_two_turns("pi", "0.85.1", os.environ["M3_DOCS_PI_MODEL"])
```

Run each harness separately from `elicitation-agents`:

```sh
python -m pytest -q test_agent_sessions.py -k codex
python -m pytest -q test_agent_sessions.py -k pi
```

The first turn's plan answers only `shipping_address`, and the second turn's
plan answers only `business_address`. `expect(result).to_have_tool_call(...)`
with `turn=` checks that each tool succeeded in its own turn.
`trace.for_turn(turn).elicitations` lists the requests answered during that
turn, with the key, action, and content M3 sent.

Both tests depend on the model choosing the tool the prompt names. If the agent
never triggers a request that its plan requires, M3 marks the turn incomplete.
`permission_policy="allow"` lets M3 approve the tool calls without prompting;
use it only for tools that are safe to run unattended.

The complete project is in
[`sdk/examples/docs/elicitation-agents`](../examples/elicitation-agents).

## Harness differences

Both tests use the default round limit of 10. The tested Codex version stops an
action at 9 rounds even when you set a higher limit; see
[Round limit](guides-elicitation-plans.md#round-limit). Pi's managed-input control protocol
accepts a limit from 1 through 1024.

Codex `0.156.1` rejects a URL-mode `InputRequiredResult` from a tool call with
`unsupported MCP tool input request`, so this example uses forms only. To test
URL requests, use a direct call as in
[Compose elicitation workflows](guides-elicitation-composed.md).

For a person answering while the agent waits, see
[Submit input to a paused execution](guides-elicitation-managed-input.md). The
[elicitation reference](reference-python-m3-elicitation.md) describes how
plans match requests.
