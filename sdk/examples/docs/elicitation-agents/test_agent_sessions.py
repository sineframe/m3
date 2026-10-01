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
