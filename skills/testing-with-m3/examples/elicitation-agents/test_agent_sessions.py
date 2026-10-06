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
