"""Action-bound MRTR examples through the unmodified Codex App Server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.codex_mrtr_server import ADDRESS_SCHEMA, VERIFIED_ADDRESSES

if TYPE_CHECKING:
    from examples.tests.codex_conftest import CodexExample

from m3 import expect, expect_form, expect_url, one_of, optional, round_of, sequence
from m3.elicitation import ElicitationPlan
from m3.types import ExecutionOutcome, StdioServer, TurnOutcome

pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]
pytest_plugins = ("examples.tests.codex_conftest",)


def _address(server: StdioServer, kind: str) -> ElicitationPlan:
    return expect_form(
        f"{kind}_address",
        message=f"Enter the {kind} delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_verified_shipment",
    ).accept(VERIFIED_ADDRESSES[f"{kind}_address"])


def _verification(server: StdioServer) -> ElicitationPlan:
    return expect_url(
        "verification",
        message="Complete shipment verification.",
        url="https://example.test/verify/123",
        server=server,
        operation_kind="tool",
        operation_name="book_verified_shipment",
    ).accept()


def _assert_completed_call(
    result: Any,
    tool_name: str,
    example: CodexExample,
    server: str = "fixture",
) -> None:
    if result.snapshot.outcome is not ExecutionOutcome.COMPLETED:
        evidence_path = example.failure_bundle(result.error)
        pytest.fail(
            "Codex MRTR example did not complete: "
            f"error={result.error!r}; exact provider/MCP/app-server evidence="
            f"{evidence_path}"
        )
    view = result.trace_view
    assert view is not None
    calls = [call for call in view.tool_calls if call.tool.value == tool_name]
    trace_identity = [
        {
            "entry_id": call.entry_id,
            "call_id": call.call_id,
            "provider_call_id": call.provider_call_id.value,
            "server": call.server.value,
            "tool": call.tool.value,
            "correlation": call.correlation.value,
            "status": call.tool_status.value,
            "jsonrpc_id": call.jsonrpc_id.value,
            "attempts": [
                {
                    "index": attempt.attempt_index,
                    "jsonrpc_id": attempt.jsonrpc_id.value,
                    "request_state": attempt.request_state.value,
                    "input_required": attempt.input_required,
                    "status": attempt.status.value,
                    "sequence": [attempt.sequence_start, attempt.sequence_end],
                }
                for attempt in call.attempts
            ],
        }
        for call in calls
    ]
    if len(calls) != 1:
        pytest.fail(
            "Codex one-operation trace must collapse retries to one logical call; "
            f"actual trace identities: {trace_identity!r}"
        )
    assert calls[0].result.value is not None
    assert calls[0].result.value.is_error is False
    expect(result).to_have_tool_call(
        tool_name,
        server=server,
        status="success",
        count=1,
    )
    approval_prompts = [
        frame
        for adapter in example.adapters
        for frame in adapter.app_server_frames
        if frame.get("method") == "mcpServer/elicitation/request"
        and isinstance(frame.get("params"), dict)
        and isinstance(frame["params"].get("_meta"), dict)
        and frame["params"]["_meta"].get("codex_approval_kind") == "mcp_tool_call"
    ]
    assert approval_prompts, "Codex native MCP tool approval was not observed"
    for prompt in approval_prompts:
        answers = [
            frame
            for adapter in example.adapters
            for frame in adapter.client_frames
            if frame.get("id") == prompt.get("id")
            and isinstance(frame.get("result"), dict)
        ]
        assert len(answers) == 1
        assert answers[0]["result"]["action"] == "accept"


def test_qualified_codex_agent_retries_one_logical_tool_call(
    codex_example: CodexExample,
) -> None:
    """A plan bound to server and operation resumes one Codex tool call."""
    server = codex_example.server()
    codex_example.enqueue_tool_and_final(
        "book_shipment", {"weight_kg": 2, "zone": "local"}
    )
    plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept(VERIFIED_ADDRESSES["home_address"])

    result = codex_example.agent().run(
        "Use m3-gate:book_shipment once and report success.",
        server=server,
        tools=["fixture:book_shipment"],
        elicitation=plan,
        timeout=120,
        permission_policy="allow",
    )

    _assert_completed_call(result, "book_shipment", codex_example)
    calls = codex_example.wire_calls()
    assert [call.get("requestState") for call in calls] == [None, "shipping-address"]
    assert calls[1]["inputResponses"] == {
        "shipping_address": {
            "action": "accept",
            "content": VERIFIED_ADDRESSES["home_address"],
        }
    }
    assert len(codex_example.provider.requests) == 2
    assert "authorization" not in codex_example.provider.requests[0].headers


def test_unqualified_codex_agent_leaves_tool_choice_to_provider(
    codex_example: CodexExample,
) -> None:
    """An unqualified plan answers the tool selected by the provider output."""
    server = codex_example.server()
    codex_example.enqueue_tool_and_final(
        "book_shipment", {"weight_kg": 2, "zone": "local"}
    )
    plan = expect_form("shipping_address").accept(VERIFIED_ADDRESSES["home_address"])

    result = codex_example.agent().run(
        "Arrange a shipment and collect the delivery address before confirming.",
        server=server,
        tools=["fixture:book_shipment"],
        elicitation=plan,
        timeout=120,
        permission_policy="allow",
    )

    _assert_completed_call(result, "book_shipment", codex_example)
    calls = codex_example.wire_calls()
    assert [call["name"] for call in calls] == ["book_shipment", "book_shipment"]
    assert (
        calls[1]["inputResponses"]["shipping_address"]["content"]
        == (VERIFIED_ADDRESSES["home_address"])
    )


@pytest.mark.parametrize("address_kind", ["home", "business"])
def test_codex_address_choice_then_url_in_one_tool_call(
    codex_example: CodexExample, address_kind: str
) -> None:
    """Codex answers one selected address form and a later URL request."""
    server = codex_example.server()
    codex_example.enqueue_tool_and_final(
        "book_verified_shipment",
        {"address_kind": address_kind},
    )
    plan = sequence(
        one_of(_address(server, "home"), _address(server, "business")),
        _verification(server),
    )

    result = codex_example.agent().run(
        f"Use m3-gate:book_verified_shipment for a {address_kind} shipment, "
        "then report whether it was booked.",
        server=server,
        tools=["fixture:book_verified_shipment"],
        elicitation=plan,
        timeout=120,
        permission_policy="allow",
    )

    _assert_completed_call(result, "book_verified_shipment", codex_example)
    view = result.trace_view
    assert view is not None
    assert [entry.request_key for entry in view.elicitations] == [
        f"{address_kind}_address",
        "verification",
    ]
    calls = codex_example.wire_calls()
    assert len(calls) == 3
    assert [call.get("requestState") for call in calls] == [
        None,
        f"verified-address:{address_kind}",
        f"verified-url:{address_kind}",
    ]
    assert calls[1]["inputResponses"] == {
        f"{address_kind}_address": {
            "action": "accept",
            "content": VERIFIED_ADDRESSES[f"{address_kind}_address"],
        }
    }
    assert set(calls[2]["inputResponses"]) == {"verification"}


@pytest.mark.parametrize("address_kind", ["none", "home", "business"])
def test_codex_optional_address_choice_then_url(
    codex_example: CodexExample, address_kind: str
) -> None:
    """An optional form round can be skipped before the required URL round."""
    server = codex_example.server()
    codex_example.enqueue_tool_and_final(
        "book_verified_shipment",
        {"address_kind": address_kind},
    )
    plan = sequence(
        optional(one_of(_address(server, "home"), _address(server, "business"))),
        _verification(server),
    )
    instruction = (
        "without an address"
        if address_kind == "none"
        else f"for a {address_kind} shipment"
    )

    result = codex_example.agent().run(
        f"Use m3-gate:book_verified_shipment {instruction}, then report whether it was booked.",
        server=server,
        tools=["fixture:book_verified_shipment"],
        elicitation=plan,
        timeout=120,
        permission_policy="allow",
    )

    _assert_completed_call(result, "book_verified_shipment", codex_example)
    view = result.trace_view
    assert view is not None
    expected_keys = (
        ["verification"]
        if address_kind == "none"
        else [f"{address_kind}_address", "verification"]
    )
    assert [entry.request_key for entry in view.elicitations] == expected_keys
    calls = codex_example.wire_calls()
    assert [call.get("requestState") for call in calls] == (
        [None, "verified-url:none"]
        if address_kind == "none"
        else [None, f"verified-address:{address_kind}", f"verified-url:{address_kind}"]
    )
    if address_kind != "none":
        assert (
            calls[1]["inputResponses"][f"{address_kind}_address"]["content"]
            == (VERIFIED_ADDRESSES[f"{address_kind}_address"])
        )
    assert set(calls[-1]["inputResponses"]) == {"verification"}


def test_codex_two_addresses_in_one_round_then_url(
    codex_example: CodexExample,
) -> None:
    """Both address prompts are answered before the later URL round."""
    server = codex_example.server()
    codex_example.enqueue_tool_and_final(
        "book_verified_shipment",
        {"address_kind": "both"},
    )
    plan = sequence(
        round_of(_address(server, "home"), _address(server, "business")),
        _verification(server),
    )

    result = codex_example.agent().run(
        "Use m3-gate:book_verified_shipment with both addresses, then report whether it was booked.",
        server=server,
        tools=["fixture:book_verified_shipment"],
        elicitation=plan,
        timeout=120,
        permission_policy="allow",
    )

    _assert_completed_call(result, "book_verified_shipment", codex_example)
    view = result.trace_view
    assert view is not None
    assert {entry.request_key for entry in view.elicitations[:2]} == {
        "home_address",
        "business_address",
    }
    assert [entry.request_key for entry in view.elicitations[2:]] == ["verification"]
    calls = codex_example.wire_calls()
    assert len(calls) == 3
    assert set(calls[1]["inputResponses"]) == {"home_address", "business_address"}
    assert (
        calls[1]["inputResponses"]["home_address"]["content"]
        == (VERIFIED_ADDRESSES["home_address"])
    )
    assert (
        calls[1]["inputResponses"]["business_address"]["content"]
        == (VERIFIED_ADDRESSES["business_address"])
    )
    assert set(calls[2]["inputResponses"]) == {"verification"}


def test_codex_session_attaches_plan_only_to_second_turn(
    codex_example: CodexExample,
) -> None:
    """A session keeps its first turn ordinary and binds the plan to turn two."""
    server = codex_example.server()
    codex_example.enqueue_tool_and_final(
        "shipping_quote", {"weight_kg": 2, "zone": "local"}
    )
    codex_example.enqueue_tool_and_final(
        "book_shipment", {"weight_kg": 2, "zone": "local"}
    )
    plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept(VERIFIED_ADDRESSES["home_address"])

    with codex_example.agent().session(
        server=server,
        tools=["fixture:shipping_quote", "fixture:book_shipment"],
        timeout=120,
        permission_policy="allow",
    ) as session:
        first = session.send(
            "Use m3-gate:shipping_quote for a 2 kg parcel in the local zone.",
            timeout=120,
        )
        assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
        second = session.send(
            "Use m3-gate:book_shipment once and report success.",
            elicitation=plan,
            timeout=120,
        )

    assert second.snapshot.outcome is TurnOutcome.COMPLETED, second.error
    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
    expect(session.result).to_have_tool_call(
        "shipping_quote",
        turn=first,
        server="fixture",
        status="success",
        count=1,
    )
    expect(session.result).to_have_tool_call(
        "book_shipment",
        turn=second,
        server="fixture",
        status="success",
        count=1,
    )
    view = session.result.trace_view
    assert view is not None
    assert not view.for_turn(first).elicitations
    assert [entry.request_key for entry in view.for_turn(second).elicitations] == [
        "shipping_address"
    ]
    calls = codex_example.wire_calls()
    assert [call["name"] for call in calls] == [
        "shipping_quote",
        "book_shipment",
        "book_shipment",
    ]
    assert set(calls[-1]["inputResponses"]) == {"shipping_address"}


def test_codex_submit_binds_plan_to_submitted_action(
    codex_example: CodexExample,
) -> None:
    """A planned async submission resolves its native form before finalizing."""
    server = codex_example.server()
    codex_example.enqueue_tool_and_final(
        "book_shipment", {"weight_kg": 2, "zone": "local"}
    )
    plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept(VERIFIED_ADDRESSES["home_address"])

    handle = codex_example.agent().submit(
        "Use m3-gate:book_shipment once and report success.",
        server=server,
        tools=["fixture:book_shipment"],
        elicitation=plan,
        timeout=120,
        permission_policy="allow",
    )
    result = handle.result(timeout=120)

    _assert_completed_call(result, "book_shipment", codex_example)
    calls = codex_example.wire_calls()
    assert len(calls) == 2
    assert (
        calls[1]["inputResponses"]["shipping_address"]["content"]
        == (VERIFIED_ADDRESSES["home_address"])
    )
