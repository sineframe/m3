"""Adversarial contracts for the framework-neutral assertion boundary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from mcp_pal.matchers import check, expect
from mcp_pal.observability import (
    InitializationEntry,
    InitializationValue,
    Observation,
    ObservationState,
    ReportedToolCall,
    ToolCallStatus,
)
from mcp_pal.trace.redaction import RedactionConfig
from mcp_pal.types import (
    CapabilityStatus,
    ConnectionId,
    Event,
    EventDirection,
    EventId,
    EventKind,
    EventOrigin,
    EventSource,
    ExecutionId,
    ExecutionOutcome,
    ExecutionResult,
    ExecutionState,
    ExecutionStatus,
    RequestLink,
    ToolInfo,
    TraceId,
    TraceResult,
    TurnId,
    TurnOutcome,
    TurnResult,
    TurnState,
    TurnStatus,
)

_REDACTION = RedactionConfig(
    secrets=frozenset({"top-secret"}), include_environment=False
)


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
        Event(
            event_id=EventId("event-created"),
            execution_id=execution,
            sequence=0,
            kind=EventKind.EXECUTION_CREATED,
            monotonic_offset_ms=0,
            payload={"trace_id": "trace-1", "lifecycle": "startup"},
            provenance=EventSource(origin=origin, source="fixture"),
        ),
        Event(
            event_id=EventId("event-request"),
            execution_id=execution,
            sequence=1,
            kind=EventKind.TOOL_CALL_REQUESTED,
            monotonic_offset_ms=1,
            server_binding=server,
            connection_id=connection,
            correlation=RequestLink(
                jsonrpc_id=1,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=1,
            ),
            payload={
                "params": {
                    "name": "lookup",
                    "arguments": arguments or {"query": "top-secret"},
                }
            },
            provenance=EventSource(origin=origin, source="fixture"),
        ),
    ]
    if include_result:
        events.append(
            Event(
                event_id=EventId("event-result"),
                execution_id=execution,
                sequence=2,
                kind=result_kind,
                monotonic_offset_ms=2,
                server_binding=server,
                connection_id=connection,
                correlation=RequestLink(
                    jsonrpc_id=1,
                    direction=EventDirection.SERVER_TO_CLIENT,
                    request_sequence=1,
                ),
                payload=result_payload
                or (
                    {"error": {"code": -1}}
                    if result_kind is EventKind.MCP_ERROR
                    else {"result": {"content": [{"type": "text", "text": "ok"}]}}
                ),
                provenance=EventSource(origin=origin, source="fixture"),
            )
        )
    if second_server is not None:
        events.append(
            Event(
                event_id=EventId("event-request-2"),
                execution_id=execution,
                sequence=3,
                kind=EventKind.TOOL_CALL_REQUESTED,
                monotonic_offset_ms=3,
                server_binding=second_server,
                connection_id=connection,
                correlation=RequestLink(
                    jsonrpc_id=2,
                    direction=EventDirection.CLIENT_TO_SERVER,
                    request_sequence=2,
                ),
                payload={"params": {"name": "lookup", "arguments": {}}},
                provenance=EventSource(origin=origin, source="fixture"),
            )
        )
    events.append(
        Event(
            event_id=EventId("event-finished"),
            execution_id=execution,
            sequence=len(events),
            kind=EventKind.EXECUTION_FINISHED,
            monotonic_offset_ms=float(len(events)),
            payload={
                "outcome": "completed",
                "completeness": "complete",
                "limitations": [],
            },
            provenance=EventSource(origin=origin, source="fixture"),
        )
    )
    return TraceResult(
        trace_id=TraceId("trace-1"),
        execution_id=execution,
        events=tuple(events),
        highest_sequence=len(events) - 1,
    )


def test_unordered_content_is_a_duplicate_aware_multiset() -> None:
    subject = SimpleNamespace(content=({"text": "a"}, {"text": "b"}))
    expect(subject).to_have_unordered_content(({"text": "b"}, {"text": "a"}))
    with pytest.raises(AssertionError):
        expect(subject).to_have_unordered_content(({"text": "a"}, {"text": "a"}))


def test_tool_call_list_checks_exact_order_and_unordered_multiset() -> None:
    trace = _trace(second_server="server-a")
    events = list(trace.events)
    events[3] = events[3].model_copy(
        update={"payload": {"params": {"name": "save", "arguments": {}}}}
    )
    view = trace.model_copy(update={"events": tuple(events)}).view()

    expect(view).to_have_tool_calls(["lookup", "save"])
    expect(view).to_have_tool_calls(["save", "lookup"], ordered=False)
    with pytest.raises(AssertionError, match="tool calls mismatch"):
        expect(view).to_have_tool_calls(["save", "lookup"])
    with pytest.raises(AssertionError, match="tool calls mismatch"):
        expect(view).to_have_tool_calls(["lookup"])
    with pytest.raises(AssertionError, match="tool calls mismatch"):
        expect(view).to_have_tool_calls(["lookup", "lookup"], ordered=False)

    duplicate_view = _trace(second_server="server-a").view()
    expect(duplicate_view).to_have_tool_calls(["lookup", "lookup"], ordered=False)
    with pytest.raises(AssertionError, match="tool calls mismatch"):
        expect(duplicate_view).to_have_tool_calls(["lookup"], ordered=False)


def test_tool_call_list_filters_server_and_evidence() -> None:
    view = _trace(second_server="server-b").view()
    expect(view).to_have_tool_calls(["lookup", "lookup"])
    expect(view).to_have_tool_calls(["lookup"], server="server-b")
    expect(view).to_have_tool_calls([], server="missing")
    expect(view).to_have_tool_calls([], evidence="reported")
    with pytest.raises(AssertionError, match="terminal/frozen"):
        expect(SimpleNamespace(wait_for=lambda *_: None)).to_have_tool_calls([])


def test_failures_redact_expected_actual_and_predicate_values() -> None:
    with pytest.raises(AssertionError) as failure:
        expect("actual top-secret value", redaction_config=_REDACTION).to_have_text(
            "expected top-secret value"
        )
    assert "top-secret" not in str(failure.value)

    with pytest.raises(AssertionError) as predicate_failure:
        expect(_trace(), redaction_config=_REDACTION).to_have_tool_call(
            "lookup",
            argument_predicate=lambda _arguments: (_ for _ in ()).throw(
                RuntimeError("top-secret")
            ),
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
    base = _trace()
    first = base.events[1].model_copy(
        update={
            "kind": EventKind.MCP_INITIALIZED,
            "server_binding": "server-a",
            "payload": {"tools": [{"name": "lookup"}]},
        }
    )
    second = base.events[2].model_copy(
        update={
            "kind": EventKind.MCP_INITIALIZED,
            "server_binding": "server-b",
            "payload": {"tools": [{"name": "lookup"}]},
        }
    )
    trace = base.model_copy(
        update={
            "events": (base.events[0], first, second, base.events[-1]),
            "highest_sequence": 3,
        }
    )
    with pytest.raises(AssertionError, match="ambiguous"):
        expect(trace).to_have_tool_call("lookup")


def test_wire_is_default_and_reported_requires_explicit_selector() -> None:
    view = _trace().view()
    entry = view.tool_calls[0]
    reported = ReportedToolCall(
        server=Observation(state=ObservationState.OBSERVED, value="server-a"),
        tool=Observation(state=ObservationState.OBSERVED, value="lookup"),
        arguments=Observation(
            state=ObservationState.OBSERVED, value={"query": "top-secret"}
        ),
        status=Observation(state=ObservationState.OBSERVED, value="success"),
    )
    patched = entry.model_copy(
        update={
            "reported": Observation(state=ObservationState.OBSERVED, value=reported)
        }
    )
    patched_view = view.model_copy(
        update={
            "timeline": tuple(
                patched if item.entry_id == entry.entry_id else item
                for item in view.timeline
            )
        }
    )
    expect(patched_view).to_have_tool_call("lookup")
    expect(patched_view).to_have_reported_tool_call("lookup")


def test_argument_modes_are_explicit_and_count_bounds_allow_zero() -> None:
    trace = _trace()
    expect(trace).to_have_tool_call(
        "lookup", arguments={"query": "top-.*"}, arguments_regex=True
    )
    expect(trace).to_have_tool_call(
        "lookup", min_count=1, max_count=1, arguments_partial=True
    )
    expect(trace).to_have_no_tool_call("missing", max_count=0)
    unordered = _trace(arguments={"values": [1, 2]})
    expect(unordered).to_have_tool_call(
        "lookup", arguments={"values": [2, 1]}, arguments_unordered=True
    )
    tolerant = _trace(arguments={"value": 1.01})
    expect(tolerant).to_have_tool_call(
        "lookup", arguments={"value": 1.0}, arguments_tolerance=0.02
    )


def test_session_like_subject_aggregates_turn_traces() -> None:
    subject = SimpleNamespace(
        turns=(SimpleNamespace(trace=_trace()), SimpleNamespace(trace=_trace()))
    )
    expect(subject).to_have_tool_call("lookup", server="server-a")
    expect(subject).to_have_tool_call("lookup", count=2, evidence="any")


def test_duration_and_terminal_statuses_are_assertable() -> None:
    trace = _trace()
    expect(trace).to_have_duration(min_ms=0, max_ms=3)
    expect(trace).to_have_tool_call("lookup", min_latency_ms=0, max_latency_ms=2)


@pytest.mark.parametrize(
    "duration", [float("nan"), float("inf"), -1.0, "not-a-duration"]
)
def test_invalid_duration_values_do_not_satisfy_timing_assertions(
    duration: object,
) -> None:
    with pytest.raises(AssertionError):
        expect(SimpleNamespace(duration_ms=duration)).to_have_duration(min_ms=0)


def test_non_numeric_snapshot_timestamps_do_not_leak_into_timing() -> None:
    subject = SimpleNamespace(
        snapshot=SimpleNamespace(created_at="start", finished_at="finish")
    )
    with pytest.raises(AssertionError):
        expect(subject).to_have_duration(min_ms=0)


@pytest.mark.parametrize(
    ("result_kind", "result_payload", "status"),
    [
        (EventKind.TOOL_RESULT_RECEIVED, {"result": {"is_error": True}}, "tool_error"),
        (EventKind.MCP_ERROR, {"error": {"code": -32603}}, "protocol_error"),
    ],
)
def test_tool_call_error_statuses_are_distinct(
    result_kind: EventKind, result_payload: dict[str, object], status: str
) -> None:
    expect(
        _trace(result_kind=result_kind, result_payload=result_payload)
    ).to_have_tool_call("lookup", status=status)


def test_unanswered_wire_call_is_incomplete() -> None:
    expect(_trace(include_result=False)).to_have_tool_call(
        "lookup", status="incomplete"
    )


def test_tool_response_correlation_includes_connection_identity() -> None:
    base = _trace()
    request, response = base.events[1:3]
    request = request.model_copy(update={"connection_id": ConnectionId("connection-a")})
    response = response.model_copy(
        update={"connection_id": ConnectionId("connection-b")}
    )
    trace = base.model_copy(
        update={
            "events": (base.events[0], request, response, base.events[-1]),
            "highest_sequence": 3,
        }
    )
    expect(trace).to_have_tool_call("lookup", status="incomplete")


def test_cancelled_wire_call_is_distinct() -> None:
    expect(
        _trace(result_kind=EventKind.MCP_CANCELLATION_COMPLETED, result_payload={})
    ).to_have_tool_call("lookup", status="cancelled")


@pytest.mark.parametrize("status", list(ToolCallStatus))
def test_every_typed_tool_status_is_matchable(status: ToolCallStatus) -> None:
    view = _trace().view()
    entry = view.tool_calls[0].model_copy(update={"tool_status": status})
    timeline = tuple(
        entry if item.entry_id == entry.entry_id else item for item in view.timeline
    )
    expect(view.model_copy(update={"timeline": timeline})).to_have_tool_call(
        "lookup", status=status
    )


def test_direct_snapshot_is_a_supported_subject() -> None:
    snapshot = ExecutionState(execution_id=ExecutionId("execution-1")).transition(
        ExecutionStatus.FINISHED, ExecutionOutcome.COMPLETED
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


def test_trace_aware_matchers_accept_typed_view_result_and_session_objects() -> None:
    trace = _trace()
    view = trace.view()
    expect(view).to_have_tool_call("lookup")
    result = ExecutionResult(
        snapshot=ExecutionState(execution_id=trace.execution_id).transition(
            ExecutionStatus.FINISHED, ExecutionOutcome.COMPLETED
        ),
        trace=trace,
    )
    expect(result).to_have_tool_call("lookup")
    expect(SimpleNamespace(trace=trace)).to_have_tool_call("lookup")
    expect(SimpleNamespace(result=result)).to_have_tool_call("lookup")


def test_tool_matcher_normalizes_turn_result_snapshot_id_and_rejects_invalid() -> None:
    trace = _trace().view()
    call = trace.tool_calls[0].model_copy(update={"turn_id": TurnId("turn-1")})
    view = trace.model_copy(
        update={
            "timeline": tuple(
                call if item.entry_id == call.entry_id else item
                for item in trace.timeline
            )
        }
    )
    snapshot = TurnState(turn_id="turn-1", session_id="session-1", number=1).transition(
        TurnStatus.FINISHED, TurnOutcome.COMPLETED
    )
    result = TurnResult(snapshot=snapshot)
    for selector in ("turn-1", TurnId("turn-1"), snapshot, result):
        expect(view).to_have_tool_call("lookup", turn=selector)
    expect(view).to_not_have_tool_call("lookup", turn="turn-2")
    with pytest.raises(TypeError, match="turn selector"):
        expect(view).to_have_tool_call("lookup", turn=object())  # type: ignore[arg-type]


def test_trace_aware_matchers_reject_nonfinalized_trace_instead_of_zero_calls() -> None:
    trace = _trace()
    partial = trace.model_copy(
        update={
            "events": trace.events[:-1],
            "highest_sequence": trace.events[-2].sequence,
        }
    )
    with pytest.raises(AssertionError, match="finalized trace unavailable"):
        expect(partial).to_have_tool_call("lookup")
    with pytest.raises(AssertionError, match="finalized trace unavailable"):
        expect(partial).to_have_duration(min_ms=0)


def test_callable_result_handle_is_not_mistaken_for_trace_subject() -> None:
    snapshot = SimpleNamespace(
        lifecycle="finished",
        outcome=ExecutionOutcome.COMPLETED,
        created_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc) + timedelta(milliseconds=1),
    )
    handle = SimpleNamespace(snapshot=snapshot, result=lambda: "ordinary result")
    expect(handle).to_have_outcome(ExecutionOutcome.COMPLETED)
    expect(handle).to_have_duration(min_ms=0)

    class PropertyResultHandle:
        def __init__(self) -> None:
            self.snapshot = snapshot

        @property
        def result(self) -> str:
            return "ordinary result"

    property_handle = PropertyResultHandle()
    expect(property_handle).to_have_outcome(ExecutionOutcome.COMPLETED)
    expect(property_handle).to_have_duration(min_ms=0)


@pytest.mark.parametrize("attribute", ["trace", "final_trace"])
def test_descriptor_trace_attribute_is_authoritative(attribute: str) -> None:
    complete = _trace()
    partial = complete.model_copy(
        update={
            "events": complete.events[:-1],
            "highest_sequence": complete.events[-2].sequence,
        }
    )
    snapshot = SimpleNamespace(
        created_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc) + timedelta(milliseconds=1),
    )

    DescriptorHandle = type(
        "DescriptorHandle",
        (),
        {
            "snapshot": snapshot,
            attribute: property(lambda _self: partial),
        },
    )
    subject = DescriptorHandle()
    if attribute == "trace":
        with pytest.raises(AssertionError, match="finalized trace unavailable"):
            expect(subject).to_have_duration(min_ms=0)
    else:
        with pytest.raises(AssertionError, match="finalized trace unavailable"):
            expect(subject).to_have_tool_call("lookup")


def test_descriptor_unavailable_trace_does_not_use_snapshot_fallback() -> None:
    class DescriptorHandle:
        snapshot = SimpleNamespace(
            created_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc) + timedelta(milliseconds=1),
        )

        @property
        def trace(self) -> None:
            return None

    with pytest.raises(AssertionError, match="finalized trace unavailable"):
        expect(DescriptorHandle()).to_have_duration(min_ms=0)


def test_invalid_direct_trace_value_does_not_use_snapshot_fallback() -> None:
    subject = SimpleNamespace(
        trace="not a trace",
        snapshot=SimpleNamespace(
            created_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc) + timedelta(milliseconds=1),
        ),
    )
    with pytest.raises(AssertionError, match="finalized trace unavailable"):
        expect(subject).to_have_duration(min_ms=0)


def test_invalid_direct_trace_does_not_fall_back_to_session_turns() -> None:
    valid = _trace()
    partial = valid.model_copy(
        update={
            "events": valid.events[:-1],
            "highest_sequence": valid.events[-2].sequence,
        }
    )
    subject = SimpleNamespace(trace=partial, turns=(SimpleNamespace(trace=valid),))
    with pytest.raises(AssertionError, match="finalized trace unavailable"):
        expect(subject).to_have_tool_call("lookup")


def test_typed_capability_status_requires_explicit_status_data() -> None:
    view = _trace().view()
    initialization = InitializationValue(
        capabilities=Observation(
            state=ObservationState.OBSERVED,
            value={"tools": {"status": "ready"}},
        )
    )
    runtime = view.runtime.model_copy(
        update={
            "initialization": Observation(
                state=ObservationState.OBSERVED, value=initialization
            )
        }
    )
    capability_view = view.model_copy(update={"runtime": runtime})
    expect(capability_view).to_have_capability("tools", status=CapabilityStatus.READY)
    with pytest.raises(AssertionError):
        expect(capability_view).to_have_capability(
            "tools", status=CapabilityStatus.UNSUPPORTED
        )
    no_status = view.model_copy(
        update={
            "runtime": view.runtime.model_copy(
                update={
                    "initialization": Observation(
                        state=ObservationState.OBSERVED,
                        value=InitializationValue(
                            capabilities=Observation(
                                state=ObservationState.OBSERVED, value={"tools": {}}
                            )
                        ),
                    )
                }
            )
        }
    )
    with pytest.raises(AssertionError):
        expect(no_status).to_have_capability("tools", status=CapabilityStatus.READY)


def test_discovery_ambiguity_isolated_by_requested_evidence() -> None:
    view = _trace().view()
    harness_init = InitializationEntry(
        entry_id="harness-init",
        execution_id=view.execution_id,
        server_binding="other-server",
        sequence_start=4,
        sequence_end=4,
        provenance=(EventSource(origin=EventOrigin.HARNESS_REPORTED, source="test"),),
        tools=Observation(
            state=ObservationState.OBSERVED,
            value=(ToolInfo(name="lookup"),),
        ),
    )
    wire_init = harness_init.model_copy(
        update={
            "entry_id": "wire-init",
            "server_binding": "server-a",
            "sequence_start": 5,
            "sequence_end": 5,
            "provenance": (
                EventSource(origin=EventOrigin.WIRE_OBSERVED, source="test"),
            ),
        }
    )
    isolated = view.model_copy(
        update={
            "timeline": (
                *view.timeline[:-1],
                harness_init,
                wire_init,
                view.timeline[-1],
            )
        }
    )
    expect(isolated).to_have_tool_call("lookup", evidence="wire")
    with pytest.raises(AssertionError, match="ambiguous"):
        expect(isolated).to_have_tool_call("lookup", evidence="any")


def test_any_evidence_selects_one_correlated_call_without_duplication() -> None:
    view = _trace().view()
    entry = view.tool_calls[0]
    reported = ReportedToolCall(
        server=Observation(state=ObservationState.OBSERVED, value="server-a"),
        tool=Observation(state=ObservationState.OBSERVED, value="lookup"),
        arguments=Observation(
            state=ObservationState.OBSERVED, value={"query": "top-secret"}
        ),
        result=Observation(
            state=ObservationState.OBSERVED,
            value={"content": [{"type": "text", "text": "ok"}]},
        ),
        status=Observation(state=ObservationState.OBSERVED, value="success"),
    )
    patched = entry.model_copy(
        update={
            "reported": Observation(state=ObservationState.OBSERVED, value=reported)
        }
    )
    timeline = tuple(
        patched if item.entry_id == entry.entry_id else item for item in view.timeline
    )
    patched_view = view.model_copy(update={"timeline": timeline})
    expect(patched_view).to_have_tool_call("lookup", evidence="wire", count=1)
    expect(patched_view).to_have_tool_call("lookup", evidence="reported", count=1)
    expect(patched_view).to_have_tool_call(
        "lookup",
        evidence="reported",
        result={"content": [{"type": "text", "text": "ok"}]},
    )
    expect(patched_view).to_have_tool_call("lookup", evidence="any", count=1)


def test_result_matching_supports_typed_and_mapping_projections_and_null_state() -> (
    None
):
    trace = _trace()
    view = trace.view()
    typed_result = view.tool_calls[0].result.value
    assert typed_result is not None
    expect(view).to_have_tool_call("lookup", result=typed_result)
    expect(view).to_have_tool_call("lookup", result={"content": [{"text": "ok"}]})

    null_response = trace.events[2].model_copy(
        update={"payload": {"result": {"structuredContent": None}}}
    )
    null_view = trace.model_copy(
        update={"events": (*trace.events[:2], null_response, *trace.events[3:])}
    ).view()
    expect(null_view).to_have_tool_call(
        "lookup", result={"structured_content": None}, result_partial=True
    )
    with pytest.raises(AssertionError):
        expect(null_view).to_have_tool_call(
            "lookup", result={"structured_content": None}, result_partial=False
        )
    expect(null_view).to_have_tool_call(
        "lookup", result={"structured_content": None}, result_partial=True
    )


def test_observed_null_selector_is_distinct_from_omitted_and_unavailable() -> None:
    trace = _trace()
    null_arguments = trace.events[1].model_copy(
        update={"payload": {"params": {"name": "lookup", "arguments": None}}}
    )
    null_trace = trace.model_copy(
        update={"events": (trace.events[0], null_arguments, *trace.events[2:])}
    )
    expect(null_trace.view()).to_have_tool_call("lookup", arguments_observed_null=True)
    # None retains its historical meaning: no argument filter.
    expect(null_trace.view()).to_have_tool_call("lookup", arguments=None)

    unavailable_arguments = (
        null_trace.view()
        .tool_calls[0]
        .model_copy(
            update={
                "arguments": Observation(
                    state=ObservationState.UNAVAILABLE,
                    reason="malformed_source",
                )
            }
        )
    )
    unavailable_wire = unavailable_arguments.wire.value
    assert unavailable_wire is not None
    unavailable_arguments = unavailable_arguments.model_copy(
        update={
            "wire": Observation(
                state=ObservationState.OBSERVED,
                value=unavailable_wire.model_copy(
                    update={
                        "arguments": Observation(
                            state=ObservationState.UNAVAILABLE,
                            reason="malformed_source",
                        )
                    }
                ),
            )
        }
    )
    unavailable_view = null_trace.view().model_copy(
        update={
            "timeline": tuple(
                unavailable_arguments
                if item.entry_id == unavailable_arguments.entry_id
                else item
                for item in null_trace.view().timeline
            )
        }
    )
    with pytest.raises(AssertionError):
        expect(unavailable_view).to_have_tool_call(
            "lookup", arguments_observed_null=True
        )

    null_result_entry = (
        trace.view()
        .tool_calls[0]
        .model_copy(
            update={
                "result": Observation(state=ObservationState.OBSERVED, value=None),
            }
        )
    )
    null_result_wire = null_result_entry.wire.value
    assert null_result_wire is not None
    null_result_entry = null_result_entry.model_copy(
        update={
            "wire": Observation(
                state=ObservationState.OBSERVED,
                value=null_result_wire.model_copy(
                    update={
                        "result": Observation(
                            state=ObservationState.OBSERVED, value=None
                        )
                    }
                ),
            )
        }
    )
    null_result_view = trace.view().model_copy(
        update={
            "timeline": tuple(
                null_result_entry
                if item.entry_id == null_result_entry.entry_id
                else item
                for item in trace.view().timeline
            )
        }
    )
    expect(null_result_view).to_have_tool_call("lookup", result_observed_null=True)
    with pytest.raises(AssertionError):
        expect(_trace(include_result=False).view()).to_have_tool_call(
            "lookup", result_observed_null=True
        )


def test_event_matcher_uses_explicit_public_kind_mapping() -> None:
    view = _trace().view()
    expect(view).to_have_event("tool_call", count=1)
    with pytest.raises(AssertionError, match="stable EventKind"):
        expect(view).to_have_event(EventKind.TOOL_CALL_REQUESTED, count=1)
    with pytest.raises(AssertionError, match="stable EventKind"):
        expect(view).to_have_event("mcp.response", count=0)


def test_generic_mcp_request_response_use_typed_public_entries() -> None:
    trace = _trace()
    request, response = trace.events[1:3]
    generic_request = request.model_copy(
        update={
            "kind": EventKind.MCP_REQUEST,
            "payload": {
                "method": "tools/call",
                "params": {"name": "lookup", "arguments": {"query": "x"}},
            },
        }
    )
    generic_response = response.model_copy(update={"kind": EventKind.MCP_RESPONSE})
    generic_trace = trace.model_copy(
        update={
            "events": (
                trace.events[0],
                generic_request,
                generic_response,
                trace.events[-1],
            ),
        }
    )
    generic_view = generic_trace.view()
    expect(generic_view).to_have_event("tool_call", count=1)
    expect(generic_view).to_have_tool_call("lookup")
    expect(generic_view).to_have_event("protocol", count=0)

    protocol_request = request.model_copy(
        update={"kind": EventKind.MCP_REQUEST, "payload": {"method": "ping"}}
    )
    protocol_response = response.model_copy(
        update={"kind": EventKind.MCP_RESPONSE, "payload": {"result": {}}}
    )
    protocol_trace = trace.model_copy(
        update={
            "events": (
                trace.events[0],
                protocol_request,
                protocol_response,
                trace.events[-1],
            ),
        }
    )
    expect(protocol_trace.view()).to_have_event("protocol", count=1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"count": -1},
        {"min_count": -1},
        {"max_count": -1},
        {"count": 1, "min_count": 1},
        {"min_count": 2, "max_count": 1},
        {"arguments_tolerance": -0.1},
        {"arguments_tolerance": float("nan")},
        {"min_latency_ms": -1},
        {"max_latency_ms": float("inf")},
        {"min_latency_ms": 3, "max_latency_ms": 2},
    ],
)
def test_tool_call_criteria_reject_invalid_bounds(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        expect(_trace()).to_have_tool_call("lookup", **kwargs)


def test_source_specific_status_and_result_do_not_fallback_between_sources() -> None:
    view = _trace().view()
    entry = view.tool_calls[0]
    reported = ReportedToolCall(
        server=Observation(state=ObservationState.OBSERVED, value="server-a"),
        tool=Observation(state=ObservationState.OBSERVED, value="lookup"),
        result=Observation(
            state=ObservationState.OBSERVED,
            value={
                "content": [{"type": "text", "text": "reported"}],
                "isError": True,
            },
        ),
        status=Observation(state=ObservationState.OBSERVED, value="tool_error"),
    )
    patched = entry.model_copy(
        update={
            "reported": Observation(state=ObservationState.OBSERVED, value=reported)
        }
    )
    patched_view = view.model_copy(
        update={
            "timeline": tuple(
                patched if item.entry_id == entry.entry_id else item
                for item in view.timeline
            )
        }
    )
    expect(patched_view).to_have_tool_call("lookup", evidence="wire", status="success")
    expect(patched_view).to_have_tool_call(
        "lookup",
        evidence="reported",
        status="tool_error",
        result={"content": [{"type": "text", "text": "reported"}], "isError": True},
    )
    expect(patched_view).to_have_tool_call("lookup", evidence="any", status="success")


def test_tool_call_predicate_receives_bounded_typed_projection() -> None:
    seen: dict[str, object] = {}

    def predicate(call: dict[str, object]) -> bool:
        seen.update(call)
        return call["tool"] == "lookup" and call["status"] == "success"

    expect(_trace().view()).to_have_tool_call("lookup", predicate=predicate)
    assert seen["evidence"] == "wire"
    assert seen["latency_ms"] == 1.0
    assert "entry" in seen


def test_unavailable_wire_observations_do_not_satisfy_latency() -> None:
    view = _trace().view()
    entry = view.tool_calls[0]
    wire = entry.wire.value
    assert wire is not None
    unavailable = entry.model_copy(
        update={
            "wire": Observation(
                state=ObservationState.OBSERVED,
                value=wire.model_copy(
                    update={
                        "latency_ms": Observation(
                            state=ObservationState.UNAVAILABLE,
                            reason="capture_failed",
                        )
                    }
                ),
            )
        }
    )
    unavailable_view = view.model_copy(
        update={
            "timeline": tuple(
                unavailable if item.entry_id == entry.entry_id else item
                for item in view.timeline
            )
        }
    )
    with pytest.raises(AssertionError):
        expect(unavailable_view).to_have_tool_call("lookup", min_latency_ms=0)


def test_unavailable_reported_status_does_not_match_a_status_filter() -> None:
    view = _trace().view()
    entry = view.tool_calls[0]
    reported = ReportedToolCall(
        server=Observation(state=ObservationState.OBSERVED, value="server-a"),
        tool=Observation(state=ObservationState.OBSERVED, value="lookup"),
        status=Observation(state=ObservationState.UNAVAILABLE, reason="capture_failed"),
    )
    patched = entry.model_copy(
        update={
            "reported": Observation(state=ObservationState.OBSERVED, value=reported)
        }
    )
    patched_view = view.model_copy(
        update={
            "timeline": tuple(
                patched if item.entry_id == entry.entry_id else item
                for item in view.timeline
            )
        }
    )
    with pytest.raises(AssertionError):
        expect(patched_view).to_have_tool_call(
            "lookup", evidence="reported", status="success"
        )
