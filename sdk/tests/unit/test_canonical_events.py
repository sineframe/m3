"""Contracts for the canonical event envelope and sequence factory."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib
import json
from importlib import resources

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from mcp_pal.execution_trace import ExecutionTraceRecorder
from mcp_pal.events import (
    EVENT_SCHEMA_ID,
    EVENT_SCHEMA_VERSION,
    CanonicalEvent,
    EventDirection,
    EventFactory,
    EventKind,
    EventOrigin,
    EventPayloadRef,
    EventProvenance,
    LifecyclePhase,
    PerExecutionSequenceAllocator,
    PerConnectionRequestSequenceAllocator,
    RawEvidenceRef,
    ReasoningState,
    ReasoningVisibility,
    RequestCorrelation,
)
from mcp_pal.storage import InMemoryExecutionStore
from mcp_pal.types import ExecutionId, ExecutionOutcome, TraceId, TraceResult


def _provenance() -> EventProvenance:
    return EventProvenance(origin=EventOrigin.WIRE_OBSERVED, source="fixture")


def test_event_public_types_have_runtime_schemas() -> None:
    types_module = importlib.import_module("mcp_pal.types")
    names = (
        "CanonicalEvent",
        "EventDirection",
        "EventKind",
        "EventOrigin",
        "EventPayloadRef",
        "EventProvenance",
        "LifecyclePhase",
        "RawEvidenceRef",
        "ReasoningState",
        "ReasoningVisibility",
        "RequestCorrelation",
    )
    for name in names:
        value = getattr(types_module, name)
        if isinstance(value, type) and issubclass(value, BaseModel):
            assert value.model_json_schema()
    assert TypeAdapter(types_module.JsonRpcId).json_schema()


def test_packaged_event_schema_matches_authoritative_model() -> None:
    resource = resources.files("mcp_pal").joinpath("schemas/mcp-pal.event.v0.2.schema.json")
    with resource.open("r", encoding="utf-8") as handle:
        packaged = json.load(handle)
    expected = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://mcp-pal.local/schemas/mcp-pal.event.v0.2.schema.json",
        **CanonicalEvent.model_json_schema(by_alias=True),
    }
    assert packaged == expected
    assert packaged["$id"].endswith("mcp-pal.event.v0.2.schema.json")
    assert packaged["properties"]["schema"]["const"] == EVENT_SCHEMA_ID
    assert packaged["properties"]["schema_version"]["const"] == EVENT_SCHEMA_VERSION
    assert packaged["additionalProperties"] is False
    correlation = packaged["$defs"]["RequestCorrelation"]
    assert {item.get("type") for item in correlation["properties"]["jsonrpc_id"]["anyOf"]} == {
        "integer",
        "string",
        "null",
    }
    assert "raw_evidence_ref" in packaged["properties"]
    assert "payload" in packaged["properties"]


def _event(**overrides: object) -> CanonicalEvent:
    values: dict[str, object] = {
        "event_id": "event-1",
        "execution_id": "execution-1",
        "sequence": 0,
        "kind": EventKind.MCP_REQUEST,
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "monotonic_offset_ms": 1.5,
        "connection_id": "connection-1",
        "correlation": RequestCorrelation(
            jsonrpc_id=7,
            direction=EventDirection.CLIENT_TO_SERVER,
            request_sequence=1,
        ),
        "lifecycle_phase": LifecyclePhase.MCP_CALL,
        "payload": {"method": "tools/call"},
        "provenance": _provenance(),
    }
    values.update(overrides)
    return CanonicalEvent.model_validate(values)


def test_schema_envelope_and_raw_evidence_are_distinct() -> None:
    event = _event(raw_evidence_ref=RawEvidenceRef(evidence_id="wire-1"))
    encoded = event.model_dump(mode="json", by_alias=True)
    assert encoded["schema"] == EVENT_SCHEMA_ID
    assert encoded["schema_version"] == EVENT_SCHEMA_VERSION
    assert encoded["payload"] == {"method": "tools/call"}
    assert encoded["raw_evidence_ref"]["evidence_id"] == "wire-1"
    assert "wire-1" not in encoded["payload"]
    assert CanonicalEvent.model_validate(encoded) == event


def test_taxonomy_is_closed_and_provider_events_are_explicit() -> None:
    with pytest.raises(ValidationError):
        _event(kind="vendor.new_event")
    provider = _event(
        kind=EventKind.PROVIDER_EVENT,
        provenance=EventProvenance(
            origin=EventOrigin.HARNESS_REPORTED,
            source="opencode",
            provider_kind="vendor.new_event",
        ),
    )
    assert provider.kind is EventKind.PROVIDER_EVENT


def test_timestamp_must_be_aware_and_is_normalized_to_utc() -> None:
    local = _event(timestamp=datetime(2026, 1, 1, 2, tzinfo=timezone.utc))
    assert local.timestamp.tzinfo == timezone.utc
    with pytest.raises(ValidationError):
        _event(timestamp=datetime(2026, 1, 1, 2))


def test_json_rpc_id_preserves_integer_and_string_identity() -> None:
    integer = RequestCorrelation(jsonrpc_id=7, direction=EventDirection.CLIENT_TO_SERVER)
    string = RequestCorrelation(jsonrpc_id="7", direction=EventDirection.CLIENT_TO_SERVER)
    assert type(integer.jsonrpc_id) is int
    assert type(string.jsonrpc_id) is str
    with pytest.raises(ValidationError):
        RequestCorrelation(jsonrpc_id=True, direction=EventDirection.CLIENT_TO_SERVER)


def test_request_sequence_requires_connection_identity() -> None:
    with pytest.raises(ValidationError):
        _event(connection_id=None)


def test_reasoning_requires_honest_explicit_visibility() -> None:
    visible = ReasoningState(visibility=ReasoningVisibility.VISIBLE, explicit=True)
    assert _event(kind=EventKind.REASONING, reasoning=visible).reasoning == visible
    for visibility in (ReasoningVisibility.UNAVAILABLE, ReasoningVisibility.ENCRYPTED, ReasoningVisibility.PROVIDER_HIDDEN):
        state = ReasoningState(visibility=visibility)
        assert _event(kind=EventKind.REASONING, reasoning=state).reasoning == state
    with pytest.raises(ValidationError):
        ReasoningState(visibility=ReasoningVisibility.VISIBLE)
    with pytest.raises(ValidationError):
        _event(kind=EventKind.REASONING)
    encrypted = ReasoningState(
        visibility=ReasoningVisibility.ENCRYPTED,
        payload_ref=EventPayloadRef(blob_id="reasoning-1", sha256="0" * 64, size_bytes=10),
    )
    assert "reasoning-1" in encrypted.model_dump_json()
    for visibility in (ReasoningVisibility.UNAVAILABLE, ReasoningVisibility.PROVIDER_HIDDEN):
        with pytest.raises(ValidationError):
            ReasoningState(
                visibility=visibility,
                payload_ref=EventPayloadRef(blob_id="must-not-be-inferred", sha256="0" * 64, size_bytes=10),
            )
        state = ReasoningState(visibility=visibility)
        assert "must-not-be-inferred" not in state.model_dump_json()


def test_allocator_is_thread_safe_and_monotonic() -> None:
    allocator = PerExecutionSequenceAllocator()
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: allocator.next(), range(200)))
    assert sorted(values) == list(range(200))


def test_request_sequences_are_independent_per_connection() -> None:
    allocator = PerConnectionRequestSequenceAllocator()
    assert [allocator.next("a"), allocator.next("a"), allocator.next("b")] == [1, 2, 1]


def test_factory_assigns_outbound_request_sequence_per_connection() -> None:
    factory = EventFactory("execution-1")
    first = factory.create(
        EventKind.MCP_REQUEST,
        connection_id="connection-a",
        correlation=RequestCorrelation(jsonrpc_id="7", direction=EventDirection.CLIENT_TO_SERVER),
        lifecycle_phase=LifecyclePhase.MCP_CALL,
    )
    second = factory.create(
        EventKind.MCP_REQUEST,
        connection_id="connection-a",
        correlation=RequestCorrelation(jsonrpc_id=8, direction=EventDirection.CLIENT_TO_SERVER),
        lifecycle_phase=LifecyclePhase.MCP_CALL,
    )
    other = factory.create(
        EventKind.MCP_REQUEST,
        connection_id="connection-b",
        correlation=RequestCorrelation(jsonrpc_id=9, direction=EventDirection.CLIENT_TO_SERVER),
        lifecycle_phase=LifecyclePhase.MCP_CALL,
    )
    assert first.correlation is not None and first.correlation.request_sequence == 1
    assert second.correlation is not None and second.correlation.request_sequence == 2
    assert other.correlation is not None and other.correlation.request_sequence == 1


def test_failed_factory_construction_does_not_consume_execution_sequence() -> None:
    factory = EventFactory("execution-1")
    with pytest.raises(ValidationError):
        factory.create(
            EventKind.DIAGNOSTIC,
            lifecycle_phase=LifecyclePhase.IDLE,
            monotonic_offset_ms=-1,
        )
    successful = factory.create(EventKind.DIAGNOSTIC, lifecycle_phase=LifecyclePhase.IDLE)
    assert successful.sequence == 0


def test_failed_outbound_request_rolls_back_both_sequences() -> None:
    factory = EventFactory("execution-1")
    with pytest.raises(ValidationError):
        factory.create(
            EventKind.MCP_REQUEST,
            connection_id="connection-a",
            correlation=RequestCorrelation(jsonrpc_id=7, direction=EventDirection.CLIENT_TO_SERVER),
            lifecycle_phase=LifecyclePhase.MCP_CALL,
            monotonic_offset_ms=-1,
        )
    successful = factory.create(
        EventKind.MCP_REQUEST,
        connection_id="connection-a",
        correlation=RequestCorrelation(jsonrpc_id=7, direction=EventDirection.CLIENT_TO_SERVER),
        lifecycle_phase=LifecyclePhase.MCP_CALL,
    )
    assert successful.sequence == 0
    assert successful.correlation is not None and successful.correlation.request_sequence == 1


def test_same_typed_request_id_can_correlate_on_different_connections() -> None:
    first = _event(
        event_id="request-a",
        connection_id="connection-a",
        correlation=RequestCorrelation(
            jsonrpc_id=7,
            direction=EventDirection.CLIENT_TO_SERVER,
            request_sequence=1,
        ),
    )
    response = _event(
        event_id="response-a",
        connection_id="connection-a",
        correlation=RequestCorrelation(
            jsonrpc_id=7,
            direction=EventDirection.SERVER_TO_CLIENT,
            request_sequence=1,
        ),
        kind=EventKind.MCP_RESPONSE,
    )
    other = _event(
        event_id="request-b",
        connection_id="connection-b",
        correlation=RequestCorrelation(
            jsonrpc_id="7",
            direction=EventDirection.CLIENT_TO_SERVER,
            request_sequence=1,
        ),
    )
    assert first.correlation is not None and response.correlation is not None
    assert other.correlation is not None
    assert first.correlation.jsonrpc_id == response.correlation.jsonrpc_id
    assert first.connection_id != other.connection_id
    assert first.correlation.jsonrpc_id != other.correlation.jsonrpc_id
    assert type(first.correlation.jsonrpc_id) is int
    assert type(other.correlation.jsonrpc_id) is str


def test_factory_assigns_unique_ordered_events_for_concurrent_callers() -> None:
    factory = EventFactory("execution-1")
    with ThreadPoolExecutor(max_workers=8) as pool:
        events = list(
            pool.map(
                lambda _: factory.create(
                    EventKind.DIAGNOSTIC,
                    lifecycle_phase=LifecyclePhase.IDLE,
                ),
                range(200),
            )
        )
    assert sorted(event.sequence for event in events) == list(range(200))
    assert len({event.event_id for event in events}) == 200
    assert {str(event.execution_id.root) for event in events} == {"execution-1"}


def test_factory_monotonic_offsets_are_local_to_execution() -> None:
    ticks = iter((1_000_000_000, 1_002_500_000))
    factory = EventFactory("execution-1", monotonic_clock_ns=lambda: next(ticks))
    event = factory.create(EventKind.EXECUTION_CREATED, lifecycle_phase=LifecyclePhase.STARTUP)
    assert event.monotonic_offset_ms == 2.5


def test_nonempty_trace_enforces_execution_sequence_and_order_invariants() -> None:
    events = (
        _event(event_id="event-0", sequence=0, monotonic_offset_ms=1.0),
        _event(event_id="event-1", sequence=1, monotonic_offset_ms=1.0),
    )
    trace = TraceResult(
        trace_id=TraceId("trace-1"),
        execution_id=ExecutionId("execution-1"),
        events=events,
        highest_sequence=1,
    )
    assert TraceResult.model_validate(trace.model_dump(mode="json")) == trace
    invalid_traces = (
        (events[0].model_copy(update={"execution_id": "other"}), events[1]),
        (events[0], events[1].model_copy(update={"sequence": 2})),
        (events[0], events[0].model_copy(update={"sequence": 1})),
        (events[0].model_copy(update={"monotonic_offset_ms": 2.0}), events[1]),
    )
    for invalid_events in invalid_traces:
        with pytest.raises(ValidationError):
            TraceResult(
                trace_id=TraceId("trace-invalid"),
                execution_id=ExecutionId("execution-1"),
                events=invalid_events,
                highest_sequence=1,
            )


def test_trace_completeness_requires_truthful_limitations() -> None:
    event = _event(event_id="event-0", sequence=0)
    with pytest.raises(ValidationError):
        TraceResult(
            trace_id=TraceId("trace-complete-limited"),
            execution_id=ExecutionId("execution-1"),
            events=(event,),
            highest_sequence=0,
            limitations=("capture_incomplete",),
        )
    with pytest.raises(ValidationError):
        TraceResult(
            trace_id=TraceId("trace-partial-empty"),
            execution_id=ExecutionId("execution-1"),
            completeness="partial",
            events=(event,),
            highest_sequence=0,
        )
    with pytest.raises(ValidationError):
        TraceResult(
            trace_id=TraceId("trace-partial-blank"),
            execution_id=ExecutionId("execution-1"),
            completeness="partial",
            events=(event,),
            highest_sequence=0,
            limitations=("   ",),
        )


def test_recorder_produced_trace_round_trips_through_public_model() -> None:
    recorder = ExecutionTraceRecorder(InMemoryExecutionStore(), "execution-recorder-trace")
    recorder.emit(EventKind.DIAGNOSTIC, payload={"source": "test"})
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)
    restored = TraceResult.model_validate(trace.model_dump(mode="json"))
    assert restored == trace
    assert [event.sequence for event in restored.events] == [0, 1, 2]
