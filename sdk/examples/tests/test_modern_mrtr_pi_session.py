"""Multi-turn Pi session: ordinary first turn, planned elicitation second."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from m3 import expect, expect_form
from m3.types import ExecutionOutcome, StdioServer, TurnOutcome

pytest_plugins = ("examples.tests.pi_conftest",)

EXAMPLES_ROOT = Path(__file__).parents[1]


# Server wiring -------------------------------------------------------------
@pytest.fixture
def example_server(pi_fixture: Path) -> StdioServer:
    marker = pi_fixture
    return StdioServer(
        name="modern-mrtr-example",
        command=sys.executable,
        args=(str(EXAMPLES_ROOT / "servers" / "modern_mrtr_server.py"),),
        cwd=str(EXAMPLES_ROOT),
        environment={"M3_MRTR_WIRE_MARKER": str(marker)},
    )


# Agent test ----------------------------------------------------------------
@pytest.mark.m3(agents=[{"harness": "pi", "models": ["fixture-model"]}])
def test_pi_session_attaches_plan_only_to_second_turn(
    agent: Any, example_server: StdioServer, pi_fixture: Path
) -> None:
    marker = pi_fixture
    with agent.session(server=example_server, timeout=120) as session:
        # Turn 1 — an ordinary session message has no elicitation plan.
        first = session.send("Give me a brief greeting.", timeout=120)
        assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error

        # Turn 2 — the immutable plan is attached at this send boundary.
        plan = expect_form("shipping_address").accept(
            {"street": "1 Main Street", "city": "Pune", "postal_code": "411001"}
        )
        second = session.send(
            "Use m3-gate:book_shipment once and report success.",
            timeout=120,
            elicitation=plan,
        )

    assert second.snapshot.outcome is TurnOutcome.COMPLETED, second.error
    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
    expect(session.result).to_have_tool_call(
        "book_shipment",
        turn=second,
        server="modern-mrtr-example",
        status="success",
        count=1,
    )
    view = session.result.trace_view
    assert view is not None
    first_view = view.for_turn(first)
    second_view = view.for_turn(second)
    assert not first_view.tool_calls
    tool_calls = [
        call for call in second_view.tool_calls if call.tool.value == "book_shipment"
    ]
    eliciting = [
        call
        for call in tool_calls
        if any(attempt.input_required for attempt in call.attempts)
    ]
    assert len(eliciting) == 1
    assert eliciting[0].attempts[0].input_required is True
    completed = [call for call in tool_calls if call.result.value is not None]
    assert completed
    assert completed[-1].result.value.is_error is False
    # Pi's current normalized capture exposes the final text result; its
    # structured_content field is not yet projected into this trace surface.
    assert completed[-1].result.value.content[0].text == "Shipment booked."
    calls = [json.loads(line) for line in marker.read_text().splitlines()]
    assert len(calls) == 2
    assert len({call["id"] for call in calls}) == 2
    assert all(call["arguments"] == {"weight_kg": 1, "zone": "local"} for call in calls)
    assert calls[0]["requestState"] is None
    assert calls[1]["requestState"] == "shipping-address"
    assert set(calls[1]["inputResponses"]) == {"shipping_address"}
