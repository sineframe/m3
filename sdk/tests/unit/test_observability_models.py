"""Typed observability model contracts."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

import m3
from m3 import async_api, observability, sync_api
from m3.observability import (
    CorrelationState,
    DiagnosticEntry,
    DirectTrace,
    InitializationValue,
    InteractionEntry,
    MessageEntry,
    Observation,
    ObservationReason,
    ObservationState,
    OpenCodeTrace,
    ProcessEntry,
    ProtocolEntry,
    ProtocolKind,
    RawMessageEntry,
    ReasoningEntry,
    RuntimeTraceInfo,
    ToolCallEntry,
    ToolCallStatus,
    TraceEntry,
    TraceSummary,
    TraceTiming,
    TraceView,
    TransportEntry,
    UsageValue,
)
from m3.types import (
    EvidenceRef,
    TransportKind,
    TurnOutcome,
    TurnResult,
    TurnState,
    TurnStatus,
)


def test_observation_requires_explicit_availability() -> None:
    observed = Observation[str](state=ObservationState.OBSERVED, value="hello")
    assert observed.model_dump(mode="json")["value"] == "hello"

    with pytest.raises(ValidationError):
        Observation[str](state=ObservationState.OBSERVED)
    with pytest.raises(ValidationError):
        Observation[str](state=ObservationState.UNAVAILABLE)
    with pytest.raises(ValidationError):
        Observation[str](
            state=ObservationState.OBSERVED,
            value="hello",
            reason=ObservationReason.CAPTURE_FAILED,
        )
    with pytest.raises(ValidationError):
        Observation[str](
            state=ObservationState.PROVIDER_HIDDEN,
            value="must never be exposed",
            reason=ObservationReason.PROVIDER_HIDDEN,
        )


@pytest.mark.parametrize(
    "state",
    [
        ObservationState.NOT_EMITTED,
        ObservationState.UNSUPPORTED,
        ObservationState.UNAVAILABLE,
        ObservationState.PROVIDER_HIDDEN,
    ],
)
def test_unavailable_observations_require_reason_and_forbid_value(
    state: ObservationState,
) -> None:
    with pytest.raises(ValidationError):
        Observation[str](state=state)
    with pytest.raises(ValidationError):
        Observation[str](
            state=state, reason=ObservationReason.PROVIDER_HIDDEN, value="secret"
        )


@pytest.mark.parametrize(
    ("state", "valid_reasons"),
    [
        (ObservationState.NOT_EMITTED, (ObservationReason.PROVIDER_DID_NOT_EMIT,)),
        (ObservationState.PROVIDER_HIDDEN, (ObservationReason.PROVIDER_HIDDEN,)),
        (ObservationState.ENCRYPTED, (ObservationReason.PROVIDER_ENCRYPTED,)),
        (ObservationState.REDACTED, (ObservationReason.REDACTED_BY_POLICY,)),
        (ObservationState.TRUNCATED, (ObservationReason.EVIDENCE_TRUNCATED,)),
        (
            ObservationState.UNSUPPORTED,
            (
                ObservationReason.HARNESS_UNSUPPORTED,
                ObservationReason.TRANSPORT_NOT_APPLICABLE,
            ),
        ),
        (
            ObservationState.UNAVAILABLE,
            (
                ObservationReason.CAPTURE_DISABLED,
                ObservationReason.CAPTURE_FAILED,
                ObservationReason.CORRELATION_UNAVAILABLE,
                ObservationReason.MALFORMED_SOURCE,
            ),
        ),
    ],
)
def test_observation_reason_matrix(
    state: ObservationState, valid_reasons: tuple[ObservationReason, ...]
) -> None:
    for reason in valid_reasons:
        assert Observation[str](state=state, reason=reason).state is state
    invalid_reason = next(
        reason for reason in ObservationReason if reason not in valid_reasons
    )
    with pytest.raises(ValidationError):
        Observation[str](state=state, reason=invalid_reason)


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (ObservationState.TRUNCATED, ObservationReason.EVIDENCE_TRUNCATED),
        (ObservationState.REDACTED, ObservationReason.REDACTED_BY_POLICY),
    ],
)
def test_partial_observations_require_reason_but_may_keep_safe_value(
    state: ObservationState, reason: ObservationReason
) -> None:
    with pytest.raises(ValidationError):
        Observation[str](state=state, value="partial")
    partial = Observation[str](state=state, reason=reason, value="partial")
    empty = Observation[str](state=state, reason=reason)
    assert partial.value == "partial"
    assert empty.value is None


def test_encrypted_observation_has_no_value_and_optional_evidence_reference() -> None:
    ref = EvidenceRef(evidence_id="evidence-1")
    encrypted = Observation[str](
        state=ObservationState.ENCRYPTED,
        reason=ObservationReason.PROVIDER_ENCRYPTED,
        evidence_ref=ref,
    )
    assert encrypted.value is None
    assert encrypted.evidence_ref == ref
    with pytest.raises(ValidationError):
        Observation[str](
            state=ObservationState.ENCRYPTED,
            reason=ObservationReason.PROVIDER_ENCRYPTED,
            value="encrypted plaintext",
        )


def test_observation_json_round_trip_is_deeply_immutable() -> None:
    value = Observation[dict[str, object]](
        state="observed", value={"nested": {"items": [1, 2]}}
    )
    assert value == Observation.model_validate(value.model_dump(mode="json"))
    assert isinstance(value.value, object)
    with pytest.raises(TypeError):
        value.value["nested"] = "changed"  # type: ignore[index]
    nested = value.value["nested"]  # type: ignore[index]
    with pytest.raises(TypeError):
        nested["items"] = []  # type: ignore[index]


def test_all_observability_models_and_aliases_have_json_schemas() -> None:
    for name in observability.__all__:
        value = getattr(observability, name)
        if isinstance(value, type) and issubclass(value, BaseModel):
            assert value.model_json_schema()
    assert TypeAdapter(TraceEntry).json_schema()
    assert TypeAdapter(RuntimeTraceInfo).json_schema()


def test_trace_entry_is_discriminated_and_trace_round_trips() -> None:
    entry = TypeAdapter(TraceEntry).validate_python(
        {
            "kind": "message",
            "entry_id": "entry-1",
            "execution_id": "execution-1",
            "sequence_start": 0,
            "sequence_end": 0,
            "timing": {
                "started_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
                "start_offset_ms": 0,
                "end_offset_ms": 1,
                "duration_ms": 1,
            },
        }
    )
    assert isinstance(entry, MessageEntry)
    view = TraceView(trace_id="trace-1", execution_id="execution-1", timeline=(entry,))
    restored = TraceView.model_validate(view.model_dump(mode="json"))
    assert restored == view
    assert restored.messages == (entry,)
    assert restored.for_turn("missing").timeline == ()


def test_transport_entries_round_trip_and_are_indexed() -> None:
    for phase in ("connected", "disconnected"):
        entry = TransportEntry(
            entry_id=f"transport-{phase}",
            execution_id="execution-1",
            sequence_start=1,
            sequence_end=1,
            phase=phase,
            configured=Observation(state="observed", value=TransportKind.STDIO),
            instrumented=Observation(state="observed", value=TransportKind.STDIO),
        )
        restored = TypeAdapter(TraceEntry).validate_python(
            entry.model_dump(mode="json")
        )
        assert restored == entry
        view = TraceView(
            trace_id="trace-1", execution_id="execution-1", timeline=(entry,)
        )
        assert view.schema_version == "1.1"
        assert view.transports == (entry,)


def test_timing_normalizes_timezone_and_rejects_invalid_values() -> None:
    india = TraceTiming(
        started_at=datetime(
            2026, 1, 1, 5, tzinfo=timezone(timedelta(hours=5, minutes=30))
        ),
        finished_at=datetime(
            2026, 1, 1, 6, tzinfo=timezone(timedelta(hours=5, minutes=30))
        ),
        start_offset_ms=1,
        end_offset_ms=3,
        duration_ms=2,
    )
    assert india.started_at.tzinfo == timezone.utc
    with pytest.raises(ValidationError):
        TraceTiming(
            started_at=datetime.fromisoformat("2026-01-01"),
            end_offset_ms=1,
            duration_ms=1,
        )
    with pytest.raises(ValidationError):
        TraceTiming(
            start_offset_ms=float("inf"), end_offset_ms=float("inf"), duration_ms=0
        )
    with pytest.raises(ValidationError):
        TraceTiming(start_offset_ms=2, end_offset_ms=1, duration_ms=0)
    with pytest.raises(ValidationError):
        TraceTiming(start_offset_ms=0, end_offset_ms=2, duration_ms=1)
    with pytest.raises(ValidationError):
        TraceTiming(
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            finished_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )


def test_entry_sequence_and_view_completeness_invariants() -> None:
    with pytest.raises(ValidationError):
        MessageEntry(entry_id="bad", execution_id="e", sequence_start=2, sequence_end=1)
    with pytest.raises(ValidationError):
        TraceView(
            trace_id="t",
            execution_id="e",
            completeness="complete",
            limitations=("partial",),
        )
    with pytest.raises(ValidationError):
        TraceView(trace_id="t", execution_id="e", completeness="partial")


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("lifecycle", {"phase": "running"}),
        ("message", {}),
        ("reasoning", {}),
        ("tool_call", {"call_id": "call"}),
        ("protocol", {"protocol": "mcp"}),
        ("initialization", {}),
        ("usage", {}),
        ("interaction", {"interaction_kind": "permission"}),
        ("process", {}),
        ("workspace", {}),
        (
            "artifact",
            {
                "artifact": {
                    "artifact_id": "a",
                    "execution_id": "e",
                    "name": "x",
                    "size_bytes": 0,
                    "sha256": "0" * 64,
                }
            },
        ),
        (
            "evaluation",
            {"evaluation": {"evaluation_id": "v", "name": "check", "status": "passed"}},
        ),
        ("diagnostic", {"code": "capture_failed", "message": "safe"}),
        ("raw_message", {"source": "mcp", "media_type": "application/json"}),
        ("provider", {"provider": "provider", "category": "event"}),
    ],
)
def test_each_trace_entry_kind_is_discriminated(
    kind: str, extra: dict[str, object]
) -> None:
    value = TypeAdapter(TraceEntry).validate_python(
        {
            "kind": kind,
            "entry_id": f"entry-{kind}",
            "execution_id": "e",
            "sequence_start": 0,
            "sequence_end": 0,
            **extra,
        }
    )
    assert value.kind == kind


@pytest.mark.parametrize("kind", ["direct", "opencode", "claude_code", "acp"])
def test_each_runtime_info_kind_round_trips(kind: str) -> None:
    value = TypeAdapter(RuntimeTraceInfo).validate_python({"kind": kind})
    restored = TypeAdapter(RuntimeTraceInfo).validate_python(
        value.model_dump(mode="json")
    )
    assert restored == value
    if kind == "acp":
        assert value.usage.state is ObservationState.UNSUPPORTED
        assert value.usage.reason is ObservationReason.HARNESS_UNSUPPORTED


def test_summary_and_runtime_usage_are_value_only_models() -> None:
    usage = UsageValue(
        input_tokens=Observation(state="observed", value=3),
        output_tokens=Observation(state="observed", value=2),
        total_tokens=Observation(state="observed", value=5),
    )
    runtime = OpenCodeTrace(usage=Observation(state="observed", value=usage))
    summary = TraceSummary(usage=Observation(state="observed", value=usage))
    assert runtime.usage.value == usage
    assert summary.usage.value == usage
    assert "entry_id" not in runtime.usage.value.model_dump()
    assert "entry_id" not in summary.usage.value.model_dump()
    initialization = InitializationValue(
        protocol_version=Observation(state="observed", value="2025-11-25")
    )
    assert initialization.protocol_version.value == "2025-11-25"
    assert initialization.server_name.state is ObservationState.NOT_EMITTED


def test_partial_value_aggregates_retain_subfield_reasons() -> None:
    usage = UsageValue(
        input_tokens=Observation(state="observed", value=4),
        cost=Observation(state="unavailable", reason=ObservationReason.CAPTURE_FAILED),
    )
    assert usage.input_tokens.value == 4
    assert usage.output_tokens.state is ObservationState.NOT_EMITTED
    assert usage.cost.reason is ObservationReason.CAPTURE_FAILED

    direct = DirectTrace(
        initialization=Observation(
            state="observed",
            value=InitializationValue(
                protocol_version=Observation(state="observed", value="2025-11-25"),
                server_name=Observation(
                    state="unsupported", reason=ObservationReason.HARNESS_UNSUPPORTED
                ),
            ),
        )
    )
    assert direct.initialization.value is not None
    assert direct.initialization.value.protocol_version.value == "2025-11-25"
    assert direct.initialization.value.server_name.state is ObservationState.UNSUPPORTED


def test_value_aggregate_defaults_are_explicit_observations() -> None:
    usage = UsageValue()
    for field_name in UsageValue.model_fields:
        assert getattr(usage, field_name).state is ObservationState.NOT_EMITTED
    initialization = InitializationValue()
    for field_name in InitializationValue.model_fields:
        assert getattr(initialization, field_name).state is ObservationState.NOT_EMITTED


def test_trace_view_indexes_and_filters_use_one_timeline() -> None:
    timing = TraceTiming(start_offset_ms=1, end_offset_ms=5, duration_ms=4)
    call = ToolCallEntry(
        entry_id="call-1",
        execution_id="execution-1",
        session_id="session-1",
        turn_id="turn-1",
        server_binding="echo",
        sequence_start=1,
        sequence_end=2,
        timing=timing,
        call_id="provider-call-1",
        correlation=CorrelationState.CORRELATED,
        tool_status=ToolCallStatus.SUCCESS,
    )
    reasoning = ReasoningEntry(
        entry_id="reason-1",
        execution_id="execution-1",
        session_id="session-1",
        turn_id="turn-1",
        sequence_start=0,
        sequence_end=0,
        timing=TraceTiming(start_offset_ms=0, end_offset_ms=1, duration_ms=1),
    )
    message = MessageEntry(
        entry_id="message-1",
        execution_id="execution-1",
        session_id="session-1",
        turn_id="turn-1",
        sequence_start=0,
        sequence_end=0,
        timing=TraceTiming(start_offset_ms=0, end_offset_ms=1, duration_ms=1),
    )
    protocol = ProtocolEntry(
        entry_id="protocol-1",
        execution_id="execution-1",
        session_id="session-1",
        turn_id="turn-1",
        sequence_start=3,
        sequence_end=3,
        protocol=ProtocolKind.MCP,
        timing=TraceTiming(start_offset_ms=5, end_offset_ms=6, duration_ms=1),
    )
    raw = RawMessageEntry(
        entry_id="raw-1",
        execution_id="execution-1",
        session_id="session-1",
        sequence_start=4,
        sequence_end=4,
        source="mcp",
        media_type="application/json",
    )
    interaction = InteractionEntry(
        entry_id="interaction-1",
        execution_id="execution-1",
        session_id="session-1",
        sequence_start=5,
        sequence_end=5,
        interaction_kind="permission",
        timing=TraceTiming(start_offset_ms=6, end_offset_ms=7, duration_ms=1),
    )
    process = ProcessEntry(
        entry_id="process-1",
        execution_id="execution-1",
        sequence_start=6,
        sequence_end=6,
        timing=TraceTiming(start_offset_ms=7, end_offset_ms=8, duration_ms=1),
    )
    diagnostic = DiagnosticEntry(
        entry_id="diagnostic-1",
        execution_id="execution-1",
        sequence_start=7,
        sequence_end=7,
        code="capture_failed",
        message="safe",
        timing=TraceTiming(start_offset_ms=8, end_offset_ms=9, duration_ms=1),
    )
    view = TraceView(
        trace_id="trace-1",
        execution_id="execution-1",
        outcome="failed",
        completeness="partial",
        limitations=("capture failed",),
        timeline=(
            reasoning,
            message,
            call,
            protocol,
            raw,
            interaction,
            process,
            diagnostic,
        ),
    )
    assert view.tool_calls == (call,)
    assert view.reasoning == (reasoning,)
    assert view.messages == (message,)
    assert view.protocol == (protocol,)
    assert view.raw_messages == (raw,)
    assert view.interactions == (interaction,)
    assert view.processes == (process,)
    assert view.diagnostics == (diagnostic,)
    assert view.for_turn("turn-1").tool_calls == (call,)
    assert view.for_session("session-1").timeline == (
        reasoning,
        message,
        call,
        protocol,
        raw,
        interaction,
    )
    assert view.for_server("echo").tool_calls == (call,)
    assert view.between(2, 3).tool_calls == (call,)
    assert view.between(6, 6).timeline == (protocol, interaction)
    assert view.between(10, 11).timeline == ()
    filtered = view.between(0, 6)
    assert filtered.timeline == (reasoning, message, call, protocol, raw, interaction)
    assert filtered.trace_id == view.trace_id
    assert filtered.execution_id == view.execution_id
    assert filtered.outcome == view.outcome
    assert filtered.limitations == view.limitations
    with pytest.raises(ValueError):
        view.between(-1, 1)
    with pytest.raises(ValueError):
        view.between(2, 1)


def test_trace_view_for_turn_accepts_public_turn_selectors() -> None:
    turn = TurnState(turn_id="turn-1", session_id="session-1", number=1).transition(
        TurnStatus.FINISHED, TurnOutcome.COMPLETED
    )
    result = TurnResult(snapshot=turn)
    view = TraceView(
        trace_id="trace-1",
        execution_id="execution-1",
        timeline=(
            MessageEntry(
                entry_id="message-1",
                execution_id="execution-1",
                session_id="session-1",
                turn_id="turn-1",
                sequence_start=0,
                sequence_end=0,
            ),
            MessageEntry(
                entry_id="message-2",
                execution_id="execution-1",
                session_id="session-1",
                turn_id="turn-2",
                sequence_start=1,
                sequence_end=1,
            ),
        ),
    )
    for selector in ("turn-1", turn.turn_id, turn, result):
        assert [entry.entry_id for entry in view.for_turn(selector).timeline] == [
            "message-1"
        ]
    with pytest.raises(TypeError, match="turn selector"):
        view.for_turn(object())  # type: ignore[arg-type]


def test_sync_and_async_exports_share_turn_selector_contract() -> None:
    assert sync_api.TraceView.for_turn is TraceView.for_turn
    assert async_api.TraceView.for_turn is TraceView.for_turn


def test_observability_types_are_root_exports() -> None:
    assert m3.TraceView is TraceView
    assert m3.ObservationState is ObservationState
    assert m3.ToolCallEntry is ToolCallEntry
    assert sync_api.TraceView is TraceView
    assert async_api.TraceView is TraceView
    assert observability.TraceView is TraceView


def test_diagnostic_fields_are_additive_and_schema_compatible() -> None:
    base = {
        "kind": "diagnostic",
        "entry_id": "diagnostic-compat",
        "execution_id": "execution-compat",
        "sequence_start": 1,
        "sequence_end": 1,
        "code": "operation_timeout",
        "message": "execution timed out",
    }
    old = DiagnosticEntry.model_validate(base)
    current = DiagnosticEntry.model_validate(
        {
            **base,
            "stage": "waiting_for_harness_response",
            "operation": "harness.response",
            "elapsed_seconds": 1.0,
            "timeout_seconds": 0.5,
        }
    )
    assert old.stage is None
    assert current.operation == "harness.response"
    assert current.model_dump(mode="json")["timeout_seconds"] == 0.5
    legacy_view = TraceView.model_validate(
        {
            "trace_id": "trace-legacy",
            "execution_id": "execution-compat",
            "schema_version": "1.1",
            "timeline": [base],
        }
    )
    assert legacy_view.schema_version == "1.1"
