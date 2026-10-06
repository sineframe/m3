<!-- Generated from docs/site/guides/elicitation/managed-input.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Submit input to a paused execution

With managed input, an agent execution pauses when an MCP server asks for input
and waits for a response from outside the test, usually a person using your
application. Your code reads the pending request from the execution handle and
submits a response, and then the agent picks up where it stopped.

## Requirements

Use Python 3.10 or later and a project set up with `m3 init` and `m3 setup`,
as in [Write your first MCP test](getting-started.md). This example
was tested with Codex CLI `0.156.1`. Managed input on Pi is also verified for
form, multi-round, and URL rounds; see
[compatibility details](reference-compatibility.md). The example calls a real model, so it needs
network access, a signed-in Codex account, and a model name:

```sh
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
```

The pending request is stored with the execution, so managed input needs a
persistent store. `m3 test` provides one: it records executions in
`.m3/executions.sqlite`.

## Example

Create a directory named `elicitation-managed-input`. Save the server as
`shipping_server.py`. Its `book_shipment` tool asks for a `shipping_address`
form before it books the shipment:

```python
from __future__ import annotations

from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {
        "street": {"type": "string"},
        "city": {"type": "string"},
    },
    "required": ["street", "city"],
}


async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="book_shipment",
                description="Book a shipment after confirming the delivery address",
                input_schema={
                    "type": "object",
                    "properties": {"weight_kg": {"type": "number"}},
                    "required": ["weight_kg"],
                },
            )
        ]
    )


async def call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    responses = params.input_responses or {}
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
            request_state="shipping-address",
        )

    address = responses.get("shipping_address")
    if (
        params.request_state != "shipping-address"
        or not isinstance(address, types.ElicitResult)
        or address.action != "accept"
        or address.content != {"street": "1 Main Street", "city": "Pune"}
    ):
        return types.CallToolResult(
            content=[types.TextContent(text="address response did not match")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="Shipment booked.")],
        structured_content={"status": "booked", "city": "Pune"},
    )


async def main() -> None:
    server: Server[object] = Server(
        "shipping-managed-input",
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

Save the test as `test_managed_input.py` beside it:

```python
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from m3 import ElicitationResponse, ExecutionOutcome, PendingElicitationRound, expect
from m3.types import ExecutionStatus, StdioServer

HERE = Path(__file__).resolve().parent

pytestmark = pytest.mark.m3(suite_name="elicitation")


def wait_for_input(handle, timeout: float = 120) -> PendingElicitationRound:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = handle.pending_elicitation()
        if pending is not None:
            return pending
        if handle.snapshot().lifecycle is ExecutionStatus.FINISHED:
            raise AssertionError("execution finished without asking for input")
        time.sleep(0.2)
    raise AssertionError("execution did not ask for input in time")


def test_person_answers_while_the_agent_waits(agent) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    handle = agent.submit(
        "Use shipping:book_shipment once for a 2 kg parcel. Ask me for the "
        "address if needed, then report whether the shipment was booked.",
        server=server,
        tools=["shipping:book_shipment"],
        timeout=120,
        human_input="managed",
    )

    pending = wait_for_input(handle)
    assert set(pending.requests) == {"shipping_address"}
    handle.respond_elicitation(
        pending.round_id,
        {
            "shipping_address": ElicitationResponse(
                action="accept",
                content={"street": "1 Main Street", "city": "Pune"},
            )
        },
        idempotency_key=f"address-{pending.round_id}",
    )
    result = handle.result(timeout=120)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "book_shipment", server="shipping", status="success", count=1
    )
```

The test takes M3's `agent` fixture, so the harness and model come from the
command line. Run it from `elicitation-managed-input`:

```sh
m3 test --harness "codex=${M3_DOCS_CODEX_MODEL}" -- test_managed_input.py
```

To use the version this guide was tested with, add `--runtime managed` and pin
it: `--harness "codex@0.156.1=${M3_DOCS_CODEX_MODEL}"`.

`agent.submit(..., human_input="managed")` starts the execution and returns a
handle right away. When the server asks for the address, the execution pauses
and `handle.pending_elicitation()` returns a `PendingElicitationRound` with the
round ID and the requests, keyed by name. `wait_for_input` polls for it and
fails early if the execution finishes without asking. `respond_elicitation`
sends one response per request key, and `handle.result()` waits for the agent
to finish.

`tools=["shipping:book_shipment"]` limits the agent to that one tool, and M3
approves Codex's calls to it from that selection.

Every response needs an `idempotency_key`. If you submit the same key and
response again, for example after a lost acknowledgement, the call succeeds
and M3 does not apply the response twice. `respond_elicitation` returns `None`
either way. Reusing a key with a different
response raises `ManagedInputConflict`. The example derives the key from the
round ID; in an application, store the key with the response so a retry sends
both unchanged.

This example handles form requests only. For a URL request, your application
shows the URL, collects the user's consent, and submits the action without form
content.

The complete project is in
[`sdk/examples/docs/elicitation-managed-input`](../examples/elicitation-managed-input).

## Limits

An execution that uses managed input can't also take a predefined `elicitation`
plan, and a managed submission can't be combined with direct `input_responses`,
`request_state`, or `allow_input_required` arguments.

If the worker stops while a request is pending, the execution does not resume.
Recovery marks the round failed and the execution finishes as failed. Saving
the round in SQLite does not make the harness action resumable.

The [managed-input API reference](reference-python-m3-managed-input.md)
lists the pending request fields, validation rules, and store contract. See
[execution lifecycle](concepts-lifecycle.md) for terminal outcomes.
