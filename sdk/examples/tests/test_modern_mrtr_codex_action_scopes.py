"""Additional installed Codex action-scope and response-shape checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest
from tests.fixtures.codex_mrtr_server import (
    ADDRESS_SCHEMA,
    BUSINESS_ADDRESS,
    HOME_ADDRESS,
)

from m3 import expect_form, expect_url, sequence
from m3.elicitation import ElicitationPlan, ElicitationResponse
from m3.types import ExecutionOutcome, TurnOutcome

if TYPE_CHECKING:
    from examples.tests.codex_conftest import CodexExample

pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]
pytest_plugins = ("examples.tests.codex_conftest",)


def _plan_with_response_meta(
    expectation: ElicitationPlan,
    action: Literal["decline", "cancel"],
    meta: dict[str, object],
) -> ElicitationPlan:
    """Bind response metadata while keeping the public plan request shape."""

    plan = expectation.cancel() if action == "cancel" else expectation.decline()
    return plan.model_copy(
        update={
            "response": ElicitationResponse(action=action, meta=meta),
        }
    )


@pytest.mark.parametrize(
    ("tool", "mode", "action", "request_key", "message"),
    [
        (
            "form_round",
            "form",
            "decline",
            "shipping_address",
            "Enter the delivery address.",
        ),
        (
            "form_round",
            "form",
            "cancel",
            "shipping_address",
            "Enter the delivery address.",
        ),
        ("url_round", "url", "decline", "checkout", "Continue checkout."),
        ("url_round", "url", "cancel", "checkout", "Continue checkout."),
    ],
)
def test_codex_planned_non_accept_response_omits_wire_content_and_keeps_meta(
    codex_example: CodexExample,
    tool: str,
    mode: str,
    action: str,
    request_key: str,
    message: str,
) -> None:
    server = codex_example.server()
    codex_example.enqueue_tool_and_final(tool, {})
    response_meta = {"fixture/response": f"planned-{action}"}
    if mode == "form":
        expectation = expect_form(
            request_key,
            message=message,
            schema=ADDRESS_SCHEMA,
            server=server,
            operation_kind="tool",
            operation_name=tool,
        )
    else:
        expectation = expect_url(
            request_key,
            message=message,
            url="https://example.test/checkout/123",
            elicitation_id="checkout-123",
            server=server,
            operation_kind="tool",
            operation_name=tool,
        )
    plan = _plan_with_response_meta(expectation, action, response_meta)

    result = codex_example.agent().run(
        f"Call fixture {tool} once and report the result.",
        server=server,
        tools=[f"fixture:{tool}"],
        elicitation=plan,
        timeout=120,
        permission_policy="allow",
    )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, {
        "error": result.error,
        "wire_calls": codex_example.wire_calls(),
        "evidence": codex_example.adapter_evidence(),
    }
    calls = [call for call in codex_example.wire_calls() if call["name"] == tool]
    assert len(calls) == 2
    retry = calls[-1]
    assert retry["requestState"] == ("form-state" if mode == "form" else "url-state")
    assert retry["inputResponses"] == {
        request_key: {"action": action, "_meta": response_meta}
    }
    assert "content" not in retry["inputResponses"][request_key]

    adapters = codex_example.adapters
    native_prompt = next(
        frame
        for adapter in adapters
        for frame in adapter.app_server_frames
        if frame.get("method") == "mcpServer/elicitation/request"
        and isinstance(frame.get("params"), dict)
        and frame["params"].get("mode") == mode
        and frame["params"].get("message") == message
    )
    native_response = next(
        frame
        for adapter in adapters
        for frame in adapter.client_frames
        if frame.get("id") == native_prompt.get("id")
        and isinstance(frame.get("result"), dict)
    )
    assert native_response["result"]["action"] == action
    assert native_response["result"]["_meta"] == response_meta


def test_same_codex_session_uses_fresh_scope_for_two_planned_turns(
    codex_example: CodexExample,
) -> None:
    server = codex_example.server()
    codex_example.enqueue_tool_and_final(
        "book_shipment", {"weight_kg": 2, "zone": "local"}
    )
    codex_example.enqueue_tool_and_final(
        "book_verified_shipment", {"address_kind": "business"}
    )
    first_plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept(HOME_ADDRESS)
    second_plan = sequence(
        expect_form(
            "business_address",
            message="Enter the business delivery address.",
            schema=ADDRESS_SCHEMA,
            server=server,
            operation_kind="tool",
            operation_name="book_verified_shipment",
        ).accept(BUSINESS_ADDRESS),
        expect_url(
            "verification",
            message="Complete shipment verification.",
            url="https://example.test/verify/123",
            elicitation_id="verify-123",
            server=server,
            operation_kind="tool",
            operation_name="book_verified_shipment",
        ).accept(),
    )

    with codex_example.agent().session(
        server=server,
        tools=["fixture:book_shipment", "fixture:book_verified_shipment"],
        timeout=120,
        permission_policy="allow",
    ) as session:
        first = session.send(
            "Book one local shipment and report its status.",
            elicitation=first_plan,
            timeout=120,
        )
        second = session.send(
            "Book one business shipment and report its status.",
            elicitation=second_plan,
            timeout=120,
        )

    assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
    assert second.snapshot.outcome is TurnOutcome.COMPLETED, second.error
    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert session.result.trace_view is not None
    trace = session.result.trace_view
    assert [item.request_key for item in trace.for_turn(first).elicitations] == [
        "shipping_address"
    ]
    assert [item.request_key for item in trace.for_turn(second).elicitations] == [
        "business_address",
        "verification",
    ]

    calls = codex_example.wire_calls()
    first_calls = [call for call in calls if call["name"] == "book_shipment"]
    second_calls = [call for call in calls if call["name"] == "book_verified_shipment"]
    assert len(first_calls) == 2
    assert first_calls[-1]["inputResponses"] == {
        "shipping_address": {"action": "accept", "content": HOME_ADDRESS}
    }
    assert len(second_calls) == 3
    assert second_calls[1]["inputResponses"] == {
        "business_address": {"action": "accept", "content": BUSINESS_ADDRESS}
    }
    assert second_calls[2]["inputResponses"] == {
        "verification": {"action": "accept", "content": {}}
    }

    turns_by_prompt: dict[str, str] = {}
    for adapter in codex_example.adapters:
        for frame in adapter.app_server_frames:
            if frame.get("method") != "mcpServer/elicitation/request":
                continue
            params = frame.get("params")
            if not isinstance(params, dict):
                continue
            message = params.get("message")
            turn_id = params.get("turnId")
            if message in {
                "Enter the delivery address.",
                "Enter the business delivery address.",
                "Complete shipment verification.",
            } and isinstance(turn_id, str):
                turns_by_prompt[str(message)] = turn_id
    first_native_turn = turns_by_prompt["Enter the delivery address."]
    second_native_turn = turns_by_prompt["Enter the business delivery address."]
    assert first_native_turn != second_native_turn
    assert turns_by_prompt["Complete shipment verification."] == second_native_turn


def test_installed_codex_fails_when_required_plan_is_unused(
    codex_example: CodexExample,
) -> None:
    server = codex_example.server()
    codex_example.enqueue_tool_and_final("shipping_quote", {})
    plan = expect_form(
        "address",
        message="Enter an address.",
        server=server,
        operation_kind="tool",
        operation_name="shipping_quote",
    ).accept({})

    result = codex_example.agent().run(
        "Use fixture shipping_quote once and report the quote.",
        server=server,
        tools=["fixture:shipping_quote"],
        elicitation=plan,
        timeout=120,
        permission_policy="allow",
    )

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert codex_example.adapters[-1].last_turn is not None
    error = codex_example.adapters[-1].last_turn.error
    assert error is not None
    assert error.details.get("reason") == "plan_incomplete"
    calls = [
        call for call in codex_example.wire_calls() if call["name"] == "shipping_quote"
    ]
    assert len(calls) == 1
    assert "requestState" not in calls[0]
    assert "inputResponses" not in calls[0]
    native_mrtr_prompts = [
        frame
        for adapter in codex_example.adapters
        for frame in adapter.app_server_frames
        if frame.get("method") == "mcpServer/elicitation/request"
        and isinstance(frame.get("params"), dict)
        and frame["params"].get("mode") in {"form", "url"}
        and not (
            isinstance(frame["params"].get("_meta"), dict)
            and frame["params"]["_meta"].get("codex_approval_kind") == "mcp_tool_call"
        )
    ]
    assert not native_mrtr_prompts
