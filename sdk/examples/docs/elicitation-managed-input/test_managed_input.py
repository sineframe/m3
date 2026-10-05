from __future__ import annotations

import os
import sys
import time
from contextlib import closing
from pathlib import Path

from m3 import (
    ElicitationResponse,
    ExecutionOutcome,
    MCPTestKit,
    PendingElicitationRound,
    expect,
)
from m3.storage import SQLiteExecutionStore
from m3.types import ExecutionStatus, StdioServer

HERE = Path(__file__).resolve().parent


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


def test_managed_form_input_resumes_codex_execution(tmp_path: Path) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    # Managed input needs a persistent store to hold the pending request.
    store = SQLiteExecutionStore(tmp_path / "executions.sqlite")
    with closing(store), MCPTestKit(store=store, env={}) as kit:
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
