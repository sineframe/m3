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
