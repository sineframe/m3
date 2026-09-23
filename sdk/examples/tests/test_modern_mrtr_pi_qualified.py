"""Qualified Pi agent MRTR example: plan and prompt name the operation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from examples.servers.modern_mrtr_server import ADDRESS_SCHEMA
from m3 import expect, expect_form
from m3.types import ExecutionOutcome, StdioServer

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
def test_qualified_pi_agent_retries_one_logical_tool_call(
    agent: Any, example_server: StdioServer, pi_fixture: Path
) -> None:
    marker = pi_fixture
    plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server=example_server,
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept({"street": "1 Main Street", "city": "Pune", "postal_code": "411001"})

    result = agent.run(
        "Use m3-gate:book_shipment once and report success.",
        server=example_server,
        elicitation=plan,
        timeout=120,
    )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "book_shipment",
        server="modern-mrtr-example",
        status="success",
        count=1,
    )
    view = result.trace_view
    assert view is not None
    tool_calls = [
        call for call in view.tool_calls if call.tool.value == "book_shipment"
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
    assert all(call["arguments"] == {"weight_kg": 2, "zone": "local"} for call in calls)
    assert calls[0]["requestState"] is None
    assert calls[1]["requestState"] == "shipping-address"
    assert set(calls[1]["inputResponses"]) == {"shipping_address"}
