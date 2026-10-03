"""Evidence ingested after the fact is placed on the trace clock by its source."""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from m3.events import EventFactory, EventSequence
from m3.execution_trace import ExecutionTraceRecorder
from m3.harness.observation_sink import HarnessObservationSink
from m3.harness.observations import ToolCallObservedObservation
from m3.observability import TimingClock
from m3.storage import InMemoryExecutionStore
from m3.types import (
    EventDirection,
    EventKind,
    EventOrigin,
    EventSource,
    ExecutionOutcome,
    RequestLink,
    TraceResult,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
CONNECTION = "clock-connection"


def _at(offset_ms: float) -> dict[str, Any]:
    return {
        "monotonic_offset_ms": offset_ms,
        "timestamp": START + timedelta(milliseconds=offset_ms),
    }


def _trace(*middle: tuple[EventKind, dict[str, Any]]) -> TraceResult:
    factory = EventFactory(
        "clock-execution", allocator=EventSequence(start=0), source="test"
    )
    events = [
        factory.create(
            EventKind.EXECUTION_CREATED,
            payload={"trace_id": "trace-clock"},
            lifecycle_phase="startup",
            **_at(0.0),
        )
    ]
    events.extend(factory.create(kind, **fields) for kind, fields in middle)
    last = max(event.monotonic_offset_ms for event in events)
    events.append(
        factory.create(
            EventKind.EXECUTION_FINISHED,
            payload={
                "outcome": "completed",
                "completeness": "complete",
                "limitations": [],
            },
            **_at(last + 1.0),
        )
    )
    return TraceResult(
        trace_id="trace-clock",
        execution_id="clock-execution",
        highest_sequence=events[-1].sequence,
        events=tuple(events),
    )


def _wire_call(
    *,
    ingested_ms: float,
    request_wire_ms: float,
    response_wire_ms: float,
    origin_ms: float | None,
) -> tuple[tuple[EventKind, dict[str, Any]], ...]:
    """One tools/call exchange replayed into the trace after the turn ended."""

    provenance = EventSource(origin=EventOrigin.WIRE_OBSERVED, source="m3.capture")
    clock = {} if origin_ms is None else {"wire_clock_origin_ms": origin_ms}
    request = {
        "evidence_mode": "wire_observed",
        "method": "tools/call",
        "tool": "echo",
        "params": {"name": "echo", "arguments": {"text": "hi"}},
        "wire_offset_ms": request_wire_ms,
        **clock,
    }
    response = {
        "evidence_mode": "wire_observed",
        "method": "tools/call",
        "tool": "echo",
        "result": {"content": [{"type": "text", "text": "hi"}]},
        "latency_ms": response_wire_ms - request_wire_ms,
        "wire_offset_ms": response_wire_ms,
        **clock,
    }
    link = {"jsonrpc_id": 4, "request_sequence": 4}
    return (
        (
            EventKind.TOOL_CALL_REQUESTED,
            {
                "connection_id": CONNECTION,
                "correlation": RequestLink(
                    direction=EventDirection.CLIENT_TO_SERVER, **link
                ),
                "payload": request,
                "provenance": provenance,
                **_at(ingested_ms),
            },
        ),
        (
            EventKind.TOOL_RESULT_RECEIVED,
            {
                "connection_id": CONNECTION,
                "correlation": RequestLink(
                    direction=EventDirection.SERVER_TO_CLIENT, **link
                ),
                "payload": response,
                "provenance": provenance,
                # Replay emits consecutive events a few ms apart.
                **_at(ingested_ms + 3.0),
            },
        ),
    )


def test_replayed_wire_call_uses_wire_time_and_latency() -> None:
    trace = _trace(
        *_wire_call(
            ingested_ms=20_000.0,
            request_wire_ms=11_000.0,
            response_wire_ms=11_005.0,
            origin_ms=250.0,
        )
    )

    (call,) = trace.view().tool_calls

    assert call.timing.clock is TimingClock.WIRE
    assert call.timing.start_offset_ms == pytest.approx(11_250.0)
    assert call.timing.end_offset_ms == pytest.approx(11_255.0)
    assert call.timing.started_at == START + timedelta(milliseconds=11_250)
    assert call.server_latency_ms.value == pytest.approx(5.0)


def test_wire_call_without_clock_origin_is_labelled_ingested() -> None:
    trace = _trace(
        *_wire_call(
            ingested_ms=20_000.0,
            request_wire_ms=11_000.0,
            response_wire_ms=11_005.0,
            origin_ms=None,
        )
    )

    (call,) = trace.view().tool_calls

    assert call.timing.clock is TimingClock.INGESTED
    assert call.timing.start_offset_ms == pytest.approx(20_000.0)
    # The proxy's own latency measurement does not depend on the anchor.
    assert call.server_latency_ms.value == pytest.approx(5.0)


@pytest.mark.parametrize("origin_ms", [9_000.0, -12_000.0])
def test_wire_origin_outside_ingestion_bounds_is_rejected(origin_ms: float) -> None:
    # 9 s origin places the call after it was ingested; -12 s before the trace.
    trace = _trace(
        *_wire_call(
            ingested_ms=20_000.0,
            request_wire_ms=11_000.0,
            response_wire_ms=11_005.0,
            origin_ms=origin_ms,
        )
    )

    (call,) = trace.view().tool_calls

    assert call.timing.clock is TimingClock.INGESTED
    assert call.timing.start_offset_ms == pytest.approx(20_000.0)


def _harness_call(origin_ms: float | None) -> tuple[EventKind, dict[str, Any]]:
    payload: dict[str, Any] = {
        "observation_id": "reported-call",
        "harness_kind": "fixture",
        "provider": "fixture",
        "turn_sequence": 1,
        "wall_time": START.isoformat(),
        "monotonic_offset_ms": 9_300.0,
        "call_id": "reported-1",
        "tool": "echo",
        "server": "fixture",
        "arguments": {"text": "hi"},
    }
    if origin_ms is not None:
        payload["harness_clock_origin_ms"] = origin_ms
    return (
        EventKind.TOOL_CALL_REQUESTED,
        {
            "payload": payload,
            "provenance": EventSource(
                origin=EventOrigin.HARNESS_REPORTED,
                source="fixture",
                provider_kind="fixture",
            ),
            **_at(20_000.0),
        },
    )


def test_harness_offsets_are_placed_from_the_turn_origin() -> None:
    (call,) = _trace(_harness_call(1_000.0)).view().tool_calls

    assert call.timing.clock is TimingClock.HARNESS
    assert call.timing.start_offset_ms == pytest.approx(10_300.0)


def test_harness_offsets_without_turn_origin_are_labelled_ingested() -> None:
    (call,) = _trace(_harness_call(None)).view().tool_calls

    assert call.timing.clock is TimingClock.INGESTED
    assert call.timing.start_offset_ms == pytest.approx(20_000.0)


def test_sink_expresses_the_turn_origin_on_the_trace_clock() -> None:
    recorder = ExecutionTraceRecorder(InMemoryExecutionStore(), "clock-sink")
    turn_origin = time.monotonic()
    expected = recorder.offset_for_perf_counter_ns(time.perf_counter_ns())
    sink = HarnessObservationSink(recorder, monotonic_origin=turn_origin)
    sink.emit(
        ToolCallObservedObservation(
            observation_id="call",
            harness_kind="fixture",
            turn_sequence=1,
            wall_time=START,
            monotonic_offset_ms=0.0,
            tool="echo",
            call_id="call-1",
            arguments={},
        )
    )
    trace = recorder.finalize(ExecutionOutcome.COMPLETED)

    (call,) = trace.view().tool_calls

    assert expected is not None
    assert call.timing.clock is TimingClock.HARNESS
    # Both clocks were read within microseconds of each other.
    assert call.timing.start_offset_ms == pytest.approx(expected, abs=1.0)
