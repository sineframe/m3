"""Adversarial contracts for the framework-neutral assertion boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_pal.matchers import check, expect
from mcp_pal.trace.redaction import RedactionConfig
from mcp_pal.types import (
    CanonicalEvent,
    ConnectionId,
    EventDirection,
    EventId,
    EventKind,
    EventOrigin,
    EventProvenance,
    ExecutionId,
    ExecutionOutcome,
    ExecutionSnapshot,
    LifecycleState,
    RequestCorrelation,
    TraceId,
    TraceResult,
)


_REDACTION = RedactionConfig(secrets=frozenset({"top-secret"}), include_environment=False)


def _trace(
    *,
    server: str = "server-a",
    second_server: str | None = None,
    origin: EventOrigin = EventOrigin.WIRE_OBSERVED,
    result_kind: EventKind = EventKind.TOOL_RESULT_RECEIVED,
    result_payload: dict[str, object] | None = None,
    include_result: bool = True,
    arguments: dict[str, object] | None = None,
) -> TraceResult:
    execution = ExecutionId("execution-1")
    connection = ConnectionId("connection-1")
    events = [
        CanonicalEvent(
            event_id=EventId("event-request"),
            execution_id=execution,
            sequence=0,
            kind=EventKind.TOOL_CALL_REQUESTED,
            monotonic_offset_ms=1,
            server_binding=server,
            connection_id=connection,
            correlation=RequestCorrelation(
                jsonrpc_id=1,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=1,
            ),
            payload={"params": {"name": "lookup", "arguments": arguments or {"query": "top-secret"}}},
            provenance=EventProvenance(origin=origin, source="fixture"),
        ),
    ]
    if include_result:
        events.append(
            CanonicalEvent(
            event_id=EventId("event-result"),
            execution_id=execution,
            sequence=1,
            kind=result_kind,
            monotonic_offset_ms=2,
            server_binding=server,
            connection_id=connection,
            correlation=RequestCorrelation(
                jsonrpc_id=1,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=1,
            ),
            payload=result_payload or ({"error": {"code": -1}} if result_kind is EventKind.MCP_ERROR else {"result": {"content": [{"text": "ok"}]}}),
            provenance=EventProvenance(origin=origin, source="fixture"),
            )
        )
    if second_server is not None:
        events.append(
            CanonicalEvent(
                event_id=EventId("event-request-2"),
                execution_id=execution,
                sequence=2,
                kind=EventKind.TOOL_CALL_REQUESTED,
                monotonic_offset_ms=3,
                server_binding=second_server,
                connection_id=connection,
                correlation=RequestCorrelation(
                    jsonrpc_id=2,
                    direction=EventDirection.CLIENT_TO_SERVER,
                    request_sequence=2,
                ),
                payload={"params": {"name": "lookup", "arguments": {}}},
                provenance=EventProvenance(origin=origin, source="fixture"),
            )
        )
    return TraceResult(trace_id=TraceId("trace-1"), execution_id=execution, events=tuple(events), highest_sequence=len(events) - 1)


def test_unordered_content_is_a_duplicate_aware_multiset() -> None:
    subject = SimpleNamespace(content=({"text": "a"}, {"text": "b"}))
    expect(subject).to_have_unordered_content(({"text": "b"}, {"text": "a"}))
    with pytest.raises(AssertionError):
        expect(subject).to_have_unordered_content(({"text": "a"}, {"text": "a"}))


def test_failures_redact_expected_actual_and_predicate_values() -> None:
    with pytest.raises(AssertionError) as failure:
        expect("actual top-secret value", redaction_config=_REDACTION).to_have_text("expected top-secret value")
    assert "top-secret" not in str(failure.value)

    with pytest.raises(AssertionError) as predicate_failure:
        expect(_trace(), redaction_config=_REDACTION).to_have_tool_call(
            "lookup", argument_predicate=lambda _arguments: (_ for _ in ()).throw(RuntimeError("top-secret"))
        )
    assert "top-secret" not in str(predicate_failure.value)


def test_structural_diff_is_bounded() -> None:
    with pytest.raises(AssertionError) as failure:
        expect("x" * 100_000, redaction_config=_REDACTION).to_have_text("y" * 100_000)
    assert len(str(failure.value)) < 5_000
    assert "<diff truncated>" in str(failure.value)


def test_serverless_duplicate_tool_name_is_rejected() -> None:
    with pytest.raises(AssertionError, match="ambiguous"):
        expect(_trace(second_server="server-b")).to_have_tool_call("lookup")


def test_discovery_only_duplicate_tool_name_is_ambiguous() -> None:
    execution = ExecutionId("execution-discovery")
    events = tuple(
        CanonicalEvent(
            event_id=EventId(f"discovery-{index}"),
            execution_id=execution,
            sequence=index,
            kind=EventKind.MCP_RESPONSE,
            monotonic_offset_ms=float(index),
            server_binding=server,
            payload={"result": {"tools": [{"name": "lookup"}]}},
            provenance=EventProvenance(origin=EventOrigin.WIRE_OBSERVED, source="fixture"),
        )
        for index, server in enumerate(("server-a", "server-b"))
    )
    trace = TraceResult(trace_id=TraceId("trace-discovery"), execution_id=execution, events=events, highest_sequence=1)
    with pytest.raises(AssertionError, match="ambiguous"):
        expect(trace).to_have_tool_call("lookup")


def test_wire_is_default_and_reported_requires_explicit_selector() -> None:
    reported = _trace(origin=EventOrigin.HARNESS_REPORTED)
    with pytest.raises(AssertionError):
        expect(reported).to_have_tool_call("lookup")
    expect(reported).to_have_reported_tool_call("lookup")


def test_argument_modes_are_explicit_and_count_bounds_allow_zero() -> None:
    trace = _trace()
    expect(trace).to_have_tool_call("lookup", arguments={"query": "top-.*"}, arguments_regex=True)
    expect(trace).to_have_tool_call("lookup", min_count=1, max_count=1, arguments_partial=True)
    expect(trace).to_have_no_tool_call("missing", max_count=0)
    unordered = _trace(arguments={"values": [1, 2]})
    expect(unordered).to_have_tool_call("lookup", arguments={"values": [2, 1]}, arguments_unordered=True)
    tolerant = _trace(arguments={"value": 1.01})
    expect(tolerant).to_have_tool_call("lookup", arguments={"value": 1.0}, arguments_tolerance=0.02)


def test_session_like_subject_aggregates_turn_traces() -> None:
    subject = SimpleNamespace(turns=(SimpleNamespace(trace=_trace()), SimpleNamespace(trace=_trace())))
    expect(subject).to_have_tool_call("lookup", server="server-a")
    expect(subject).to_have_tool_call("lookup", count=2, evidence="any")


def test_duration_and_terminal_statuses_are_assertable() -> None:
    trace = _trace()
    expect(trace).to_have_duration(min_ms=0, max_ms=2)
    expect(trace).to_have_tool_call("lookup", min_latency_ms=0, max_latency_ms=2)


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1.0, "not-a-duration"])
def test_invalid_duration_values_do_not_satisfy_timing_assertions(duration: object) -> None:
    with pytest.raises(AssertionError):
        expect(SimpleNamespace(duration_ms=duration)).to_have_duration(min_ms=0)


def test_non_numeric_snapshot_timestamps_do_not_leak_into_timing() -> None:
    subject = SimpleNamespace(snapshot=SimpleNamespace(created_at="start", finished_at="finish"))
    with pytest.raises(AssertionError):
        expect(subject).to_have_duration(min_ms=0)


@pytest.mark.parametrize(
    ("result_kind", "result_payload", "status"),
    [
        (EventKind.TOOL_RESULT_RECEIVED, {"result": {"is_error": True}}, "tool_error"),
        (EventKind.MCP_ERROR, {"error": {"code": -32603}}, "protocol_error"),
    ],
)
def test_tool_call_error_statuses_are_distinct(result_kind: EventKind, result_payload: dict[str, object], status: str) -> None:
    expect(_trace(result_kind=result_kind, result_payload=result_payload)).to_have_tool_call("lookup", status=status)


def test_unanswered_wire_call_is_incomplete() -> None:
    expect(_trace(include_result=False)).to_have_tool_call("lookup", status="incomplete")


def test_tool_response_correlation_includes_connection_identity() -> None:
    base = _trace()
    request, response = base.events
    request = request.model_copy(update={"connection_id": ConnectionId("connection-a")})
    response = response.model_copy(update={"connection_id": ConnectionId("connection-b")})
    trace = TraceResult(trace_id=base.trace_id, execution_id=base.execution_id, events=(request, response), highest_sequence=1)
    expect(trace).to_have_tool_call("lookup", status="incomplete")


def test_cancelled_wire_call_is_distinct() -> None:
    expect(_trace(result_kind=EventKind.MCP_CANCELLATION_COMPLETED, result_payload={})).to_have_tool_call("lookup", status="cancelled")


def test_direct_snapshot_is_a_supported_subject() -> None:
    snapshot = ExecutionSnapshot(execution_id=ExecutionId("execution-1")).transition(
        LifecycleState.FINISHED, ExecutionOutcome.COMPLETED
    )
    expect(snapshot).to_be_completed()


def test_grouped_assertions_aggregate_and_redact() -> None:
    with pytest.raises(AssertionError) as failure:
        with check(redaction_config=_REDACTION) as checks:
            checks.expect("wrong top-secret").to_have_text("expected top-secret")
            checks.expect("still wrong").to_have_text("expected")
    message = str(failure.value)
    assert "2 grouped assertion(s) failed" in message
    assert "top-secret" not in message
