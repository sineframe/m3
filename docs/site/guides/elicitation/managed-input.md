---
title: "Submit input to a paused execution"
description: "Pause an agent execution for MCP elicitation, submit a keyed response, and wait for the worker to finish."
---

# Submit input to a paused execution

With managed input, an agent execution pauses for a person’s response to an MCP
elicitation request. Read the persisted request, submit a response under its
request key, and wait for the worker to finish.

## Requirements

Works with Codex CLI `0.156.1`; other agent harnesses are unverified for this
workflow. The live example requires a model available to the signed-in Codex
account, network access, and a persistent SQLite execution store that
implements M3's managed-input API. Review Codex's tool approval prompt before
continuing.

From the repository root, install the candidate package and pytest with
`python -m pip install -e 'sdk[pytest]'`. Installing `sf-m3[pytest]` from the
package index selects a published release. The separate storage contract test
uses SQLite and needs no harness or credentials.

Set a model available to the signed-in Codex account before running that
provider scenario:

```sh
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
```

Create a directory named `elicitation-managed-input`. Save this complete
server as `shipping_server.py`:

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

## Pause, read, submit, and wait

Save this live-provider test as `test_managed_input.py` beside
`shipping_server.py`:

```python
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from m3 import ElicitationResponse, ExecutionOutcome, MCPTestKit, expect
from m3.storage import SQLiteExecutionStore
from m3.types import ExecutionStatus, StdioServer

HERE = Path(__file__).resolve().parent


def test_managed_form_input_resumes_codex_execution(tmp_path: Path) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    store = SQLiteExecutionStore(tmp_path / "executions.sqlite")
    try:
        with MCPTestKit(store=store, env={}) as kit:
            agent = kit.agents(
                [
                    {
                        "harness": "codex",
                        "models": [os.environ["M3_DOCS_CODEX_MODEL"]],
                        "runtime": "managed",
                        "version": "0.156.1",
                    }
                ]
            )[0]
            handle = agent.submit(
                "Use shipping:book_shipment once for a 2 kg parcel. Ask me for the "
                "address if needed, then report whether the shipment was booked.",
                server=server,
                tools=["shipping:book_shipment"],
                timeout=120,
                human_input="managed",
                permission_policy="allow",
            )

            deadline = time.monotonic() + 120
            pending = handle.pending_elicitation()
            while pending is None and time.monotonic() < deadline:
                snapshot = handle.snapshot()
                if snapshot.lifecycle is ExecutionStatus.FINISHED:
                    raise AssertionError(
                        f"execution finished before input was pending: {snapshot.outcome}"
                    )
                time.sleep(0.2)
                pending = handle.pending_elicitation()

            assert pending is not None, "execution did not request managed input"
            assert set(pending.requests) == {"shipping_address"}
            handle.respond_elicitation(
                pending.round_id,
                {
                    "shipping_address": ElicitationResponse(
                        action="accept",
                        content={"street": "1 Main Street", "city": "Pune"},
                    )
                },
                idempotency_key=f"docs-response-{pending.round_id}",
            )
            result = handle.result(timeout=120)

        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
        expect(result).to_have_tool_call(
            "book_shipment",
            server="shipping",
            status="success",
            count=1,
        )
    finally:
        store.close()
```

From the parent of the `elicitation-managed-input` directory, run:

```sh
export M3_DOCS_CODEX_MODEL='<model available to your Codex login>'
cd elicitation-managed-input
python -m pytest -q test_managed_input.py
```

`test_managed_form_input_resumes_codex_execution` polls for the form request
for up to two minutes, submits a response with the request key and round ID
from its own execution, and waits for the worker to finish the tool call. Its
`permission_policy="allow"` applies only to this non-destructive teaching tool
and avoids the need for an interactive approval handler.

This documentation build did not produce live output because
`M3_DOCS_CODEX_MODEL` was unset, and it did not check Codex authentication. A
maintainer test covers the managed protocol flow with the pinned Codex
executable and a local Responses fixture, but does not test provider access.

The test derives its idempotency key from the pending round ID. Persist the key
with the response so retries reuse the same pair. This live path handles form
requests only; URL requests need an explicit consent flow and a response
without form content.

## Verify keyed storage, retry, and stale-response behavior locally

The storage test creates its own execution and round IDs, then reopens the same
SQLite database through a second store instance. `SQLiteManagedInputStore`
opens a connection per operation and has no `close()` method. No native
harness process starts or resumes.

Save as `test_managed_store.py` in the same directory:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from m3 import ElicitationResponse, FormElicitationRequest, PendingElicitationRound
from m3.errors import ManagedInputConflict, ManagedInputValidationError
from m3.storage import SQLiteManagedInputStore


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def pending_round() -> PendingElicitationRound:
    execution_id = f"docs-execution-{uuid4().hex}"
    round_id = f"docs-round-{uuid4().hex}"
    request = FormElicitationRequest(
        request_key="shipping_address",
        message="Enter the delivery address.",
        requested_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    return PendingElicitationRound(
        round_id=round_id,
        execution_id=execution_id,
        logical_operation_id=f"docs-operation-{uuid4().hex}",
        server="shipping",
        operation_kind="tool",
        operation_name="book_shipment",
        request_state="shipping-address:server-state",
        requests={"shipping_address": request},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        deadline=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
    )


def test_response_retry_survives_store_reopen_and_stale_lease_is_rejected(
    tmp_path: Path,
) -> None:
    database = tmp_path / "managed-input.sqlite"
    clock = Clock()
    pending = pending_round()
    store = SQLiteManagedInputStore(database, clock=clock)
    record = store.create_round(
        pending,
        round_index=0,
        round_limit=10,
        owner_id="docs-worker",
        lease_seconds=30,
    )
    response = {
        "shipping_address": ElicitationResponse(
            action="accept", content={"city": "Pune"}
        )
    }

    with pytest.raises(ManagedInputValidationError, match="exactly"):
        store.submit_responses(
            pending.execution_id,
            pending.round_id,
            {},
            owner_id=record.owner_id,
            lease_token=record.lease_token,
            response_idempotency_key="docs-response-1",
        )
    assert store.get_round(pending.execution_id, pending.round_id).status == "pending"

    accepted = store.submit_responses(
        pending.execution_id,
        pending.round_id,
        response,
        owner_id=record.owner_id,
        lease_token=record.lease_token,
        response_idempotency_key="docs-response-1",
    )
    assert accepted.status == "response_validated"
    del store
    reopened = SQLiteManagedInputStore(database, clock=clock)
    assert reopened.get_round(pending.execution_id, pending.round_id) == accepted
    retried = reopened.submit_responses(
        pending.execution_id,
        pending.round_id,
        response,
        owner_id=record.owner_id,
        lease_token=record.lease_token,
        response_idempotency_key="docs-response-1",
    )
    assert retried == accepted

    pending_stale = pending_round()
    stale = reopened.create_round(
        pending_stale,
        round_index=0,
        round_limit=10,
        owner_id="docs-worker",
        lease_seconds=30,
    )
    clock.value += timedelta(seconds=31)
    with pytest.raises(ManagedInputConflict, match="expired"):
        reopened.submit_responses(
            pending_stale.execution_id,
            pending_stale.round_id,
            response,
            owner_id=stale.owner_id,
            lease_token=stale.lease_token,
            response_idempotency_key="docs-response-stale",
        )
    assert (
        reopened.get_round(pending_stale.execution_id, pending_stale.round_id).status
        == "pending"
    )
```

From `elicitation-managed-input`, run this deterministic scenario with:

```sh
python -m pytest -q test_managed_store.py
```

Captured output:

```text
1 passed
```

`test_response_retry_survives_store_reopen_and_stale_lease_is_rejected` covers
exact-key validation, a valid keyed response, the same idempotent submission
after reopening the store, and rejection after the worker lease expires. It
does not establish that a native harness can resume after process loss.
Managed worker recovery marks an unresolved native request terminal unless it
can resume safely; persisting the round alone does not make the harness action
resumable. See the
[managed-input API reference](../../reference/python/m3/managed-input.md).

The complete source project is
[`sdk/examples/docs/elicitation-managed-input`](../../../../sdk/examples/docs/elicitation-managed-input).

Managed input cannot be combined with a predefined elicitation plan on the
same execution. Managed submissions also cannot be combined with direct
`input_responses`, `request_state`, or `allow_input_required` arguments.
Direct SDK elicitation needs no agent harness; agent-driven elicitation uses
the harness capabilities described in [the agent guide](agents.md). See the
[elicitation plan reference](../../reference/python/m3/elicitation.md) and
[execution lifecycle](../../concepts/lifecycle.md).
