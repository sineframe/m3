"""Contract tests for canonical trace projection and finalization."""

from __future__ import annotations

import threading
from datetime import datetime

import pytest

from mcp_pal.execution_trace import (
    ExecutionTraceRecorder,
    TraceFinalizationConflict,
    TraceRecorderError,
)
from mcp_pal.storage import InMemoryExecutionStore, StorageConflict
from mcp_pal.trace.redaction import REDACTED, RedactionConfig, RedactionError
from mcp_pal.types import (
    CanonicalEvent,
    EventId,
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    LifecycleState,
    SessionId,
    TraceId,
    TurnId,
    TurnLifecycle,
    TurnOutcome,
)


def test_snapshot_is_derived_from_committed_events_and_previous_value_stays_immutable() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-snapshot")
    before = recorder.snapshot()
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": LifecycleState.STARTING.value})
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": LifecycleState.IDLE.value})
    after = recorder.snapshot()
    assert before.lifecycle is LifecycleState.CREATED
    assert before.sequence == 0
    assert after.lifecycle is LifecycleState.IDLE
    assert after.sequence == 2
    assert before.lifecycle is LifecycleState.CREATED


def test_typed_root_model_identifiers_are_preserved() -> None:
    store = InMemoryExecutionStore()
    execution_id = ExecutionId("typed-execution")
    trace_id = TraceId("typed-trace")
    recorder = ExecutionTraceRecorder(store, execution_id, trace_id=trace_id)
    assert recorder.execution_id == execution_id
    assert recorder.trace_id == trace_id
    assert recorder.snapshot().execution_id == execution_id


def test_execution_scoped_clock_has_utc_timestamps_and_nondecreasing_offsets() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-clock")
    emitted = [recorder.emit(EventKind.DIAGNOSTIC, payload={"index": index}) for index in range(4)]
    assert all(event.timestamp.tzinfo is not None for event in emitted)
    assert all(
        left.monotonic_offset_ms <= right.monotonic_offset_ms
        for left, right in zip(emitted, emitted[1:])
    )
def test_turn_and_session_attribution_is_projected_from_committed_events() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-turn")
    recorder.emit(EventKind.SESSION_CREATED, session_id="session-1", payload={})
    recorder.emit(
        EventKind.TURN_CREATED,
        session_id="session-1",
        turn_id="turn-1",
        payload={"number": 1},
    )
    recorder.emit(
        EventKind.TURN_STATE_CHANGED,
        session_id="session-1",
        turn_id="turn-1",
        payload={"lifecycle": TurnLifecycle.RUNNING.value},
    )
    recorder.emit(
        EventKind.TURN_STATE_CHANGED,
        session_id="session-1",
        turn_id="turn-1",
        payload={"lifecycle": TurnLifecycle.FINISHED.value, "outcome": TurnOutcome.COMPLETED.value},
    )
    turn = recorder.turn_snapshots()[0]
    assert turn.session_id.root == "session-1"
    assert turn.number == 1
    assert turn.lifecycle is TurnLifecycle.FINISHED
    assert turn.outcome is TurnOutcome.COMPLETED


def test_typed_session_and_turn_ids_are_preserved_on_emitted_events() -> None:
    recorder = ExecutionTraceRecorder(InMemoryExecutionStore(), "execution-typed-turn")
    session_id = SessionId("typed-session")
    turn_id = TurnId("typed-turn")
    recorder.emit(EventKind.SESSION_CREATED, session_id=session_id, payload={})
    event = recorder.emit(
        EventKind.TURN_CREATED,
        session_id=session_id,
        turn_id=turn_id,
        payload={"number": 1},
    )
    assert event.session_id == session_id
    assert event.turn_id == turn_id


@pytest.mark.parametrize("outcome", tuple(ExecutionOutcome))
def test_every_terminal_outcome_has_a_terminal_trace_and_snapshot(outcome: ExecutionOutcome) -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, f"execution-{outcome.value}")
    trace = recorder.finalize(outcome)
    assert trace.completeness == "complete"
    assert trace.highest_sequence == trace.events[-1].sequence
    assert trace.events[-1].kind is EventKind.EXECUTION_FINISHED
    assert recorder.snapshot().lifecycle is LifecycleState.FINISHED
    assert recorder.snapshot().outcome is outcome


def test_cleanup_or_final_persistence_failure_makes_trace_partial_with_safe_limitations() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-partial")
    trace = recorder.finalize(
        ExecutionOutcome.TIMED_OUT,
        cleanup_succeeded=False,
        persistence_succeeded=True,
        limitations=("caller-secret-must-not-be-stored", "capture_incomplete"),
    )
    assert trace.completeness == "partial"
    assert trace.limitations == ("capture_incomplete", "cleanup_failed")
    assert "caller-secret" not in repr(trace)
    persistence = ExecutionTraceRecorder(InMemoryExecutionStore(), "execution-persistence-failure")
    persistence_trace = persistence.finalize(ExecutionOutcome.FAILED, persistence_succeeded=False)
    assert persistence_trace.completeness == "partial"
    assert persistence_trace.limitations == ("persistence_failed",)


def test_callbacks_see_the_whole_committed_batch_before_delivery() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-callback")
    observed: list[tuple[int, tuple[int, ...]]] = []

    def callback(event: CanonicalEvent) -> None:
        sequence = event.sequence
        observed.append((sequence, tuple(item.sequence for item in recorder.events())))

    store.subscribe("execution-callback", callback)
    recorder.emit(EventKind.DIAGNOSTIC, payload={"message": "redacted"})
    assert observed == [(1, (0, 1))]


def test_finalization_is_idempotent_but_conflicting_outcomes_are_rejected() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-idempotent")
    first = recorder.finalize(ExecutionOutcome.CANCELLED)
    second = recorder.finalize(ExecutionOutcome.CANCELLED)
    assert first == second
    assert len(recorder.events()) == 2
    with pytest.raises(TraceFinalizationConflict):
        recorder.finalize(ExecutionOutcome.FAILED)


def test_payloads_are_redacted_before_commit_and_fail_closed() -> None:
    store = InMemoryExecutionStore()
    config = RedactionConfig(secrets=frozenset({"secret"}), include_environment=False)
    recorder = ExecutionTraceRecorder(store, "execution-redaction", redaction_config=config)
    event = recorder.emit(EventKind.DIAGNOSTIC, payload={"raw": "secret"})
    assert event.payload["raw"] == REDACTED
    assert store.events("execution-redaction")[-1].payload["raw"] == REDACTED
    with pytest.raises(RedactionError):
        recorder.emit(EventKind.DIAGNOSTIC, payload={"raw": object()})
    assert len(recorder.events()) == 2
    assert recorder.emit(EventKind.DIAGNOSTIC, payload={"raw": "safe"}).sequence == 2


def test_redaction_covers_event_metadata_outside_payload_without_changing_identity() -> None:
    store = InMemoryExecutionStore()
    config = RedactionConfig(secrets=frozenset({"server-secret"}), include_environment=False)
    recorder = ExecutionTraceRecorder(store, "execution-metadata-redaction", redaction_config=config)
    event = CanonicalEvent(
        event_id=EventId("metadata-event"),
        execution_id=ExecutionId("execution-metadata-redaction"),
        sequence=1,
        kind=EventKind.DIAGNOSTIC,
        monotonic_offset_ms=0.0,
        server_binding="server-secret",
        payload={"message": "safe"},
    )
    with pytest.raises(TraceRecorderError):
        recorder.record(event)
    assert len(recorder.events()) == 1


def test_malformed_terminal_and_turn_payloads_are_rejected_before_commit() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-validation")
    malformed_terminal = CanonicalEvent(
        event_id=EventId("malformed-terminal"),
        execution_id=ExecutionId("execution-validation"),
        sequence=1,
        kind=EventKind.EXECUTION_FINISHED,
        monotonic_offset_ms=0.0,
        payload={},
    )
    with pytest.raises(TraceRecorderError):
        recorder.record(malformed_terminal)
    assert len(recorder.events()) == 1


def test_illegal_lifecycle_and_orphan_turn_events_are_rejected_without_commit() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-illegal")
    with pytest.raises(TraceRecorderError):
        recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": LifecycleState.IDLE.value})
    with pytest.raises(TraceRecorderError):
        recorder.emit(
            EventKind.TURN_STATE_CHANGED,
            session_id="missing-session",
            turn_id="missing-turn",
            payload={"lifecycle": TurnLifecycle.RUNNING.value},
        )
    assert len(recorder.events()) == 1
    with pytest.raises(TraceRecorderError):
        recorder.emit(
            EventKind.TURN_STATE_CHANGED,
            session_id="session-1",
            turn_id="turn-1",
            payload={"lifecycle": TurnLifecycle.FINISHED.value},
        )
    assert len(recorder.events()) == 1


def test_startup_failure_and_cleanup_failure_retain_terminal_evidence() -> None:
    startup_store = InMemoryExecutionStore()
    startup = ExecutionTraceRecorder(startup_store, "execution-startup-failure")
    startup.emit(EventKind.DIAGNOSTIC, payload={"phase": "startup", "status": "failed"})
    startup_trace = startup.finalize(ExecutionOutcome.FAILED)
    assert startup_trace.events[-2].payload["phase"] == "startup"
    assert startup.snapshot().outcome is ExecutionOutcome.FAILED

    cleanup_store = InMemoryExecutionStore()
    cleanup = ExecutionTraceRecorder(cleanup_store, "execution-cleanup-failure")
    cleanup_trace = cleanup.finalize(ExecutionOutcome.FAILED, cleanup_succeeded=False)
    assert cleanup_trace.completeness == "partial"
    assert "cleanup_failed" in cleanup_trace.limitations


def test_concurrent_emits_are_serialized_into_one_contiguous_trace() -> None:
    recorder = ExecutionTraceRecorder(InMemoryExecutionStore(), "execution-concurrent-emits")
    barrier = threading.Barrier(12)
    results: list[CanonicalEvent] = []
    failures: list[BaseException] = []
    result_lock = threading.Lock()

    def emit(index: int) -> None:
        barrier.wait()
        try:
            event = recorder.emit(EventKind.DIAGNOSTIC, payload={"index": index})
            with result_lock:
                results.append(event)
        except BaseException as exc:
            with result_lock:
                failures.append(exc)

    threads = [threading.Thread(target=emit, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert sorted(event.sequence for event in results) == list(range(1, 13))
    assert [event.sequence for event in recorder.events()] == list(range(13))


def test_failed_emit_releases_its_reservation_for_the_next_producer() -> None:
    recorder = ExecutionTraceRecorder(InMemoryExecutionStore(), "execution-release")
    with pytest.raises(TraceRecorderError):
        recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": LifecycleState.IDLE.value})
    event = recorder.emit(EventKind.DIAGNOSTIC, payload={"after": "failure"})
    assert event.sequence == 1
    assert [item.sequence for item in recorder.events()] == [0, 1]


def test_terminal_race_rejects_producer_after_finished_without_postterminal_event() -> None:
    recorder = ExecutionTraceRecorder(InMemoryExecutionStore(), "execution-terminal-race")
    clock_entered = threading.Event()
    release_clock = threading.Event()
    emitter_started = threading.Event()
    finalization_errors: list[BaseException] = []
    producer_errors: list[BaseException] = []
    original_clock = recorder._clock

    def gated_clock() -> tuple[datetime, float]:
        clock_entered.set()
        assert release_clock.wait(timeout=5)
        return original_clock()

    recorder._clock = gated_clock  # type: ignore[method-assign]

    def finalize() -> None:
        try:
            recorder.finalize(ExecutionOutcome.COMPLETED)
        except BaseException as exc:
            finalization_errors.append(exc)

    def produce() -> None:
        emitter_started.set()
        try:
            recorder.emit(EventKind.DIAGNOSTIC, payload={"race": True})
        except BaseException as exc:
            producer_errors.append(exc)

    finalizer = threading.Thread(target=finalize)
    finalizer.start()
    assert clock_entered.wait(timeout=5)
    producer = threading.Thread(target=produce)
    producer.start()
    assert emitter_started.wait(timeout=5)
    release_clock.set()
    finalizer.join(timeout=5)
    producer.join(timeout=5)
    assert finalization_errors == []
    assert len(producer_errors) == 1
    assert isinstance(producer_errors[0], TraceFinalizationConflict)
    events = recorder.events()
    assert [event.sequence for event in events] == [0, 1]
    assert events[-1].kind is EventKind.EXECUTION_FINISHED


def test_concurrent_same_outcome_finalization_is_idempotent() -> None:
    recorder = ExecutionTraceRecorder(InMemoryExecutionStore(), "execution-finalize-race")
    barrier = threading.Barrier(8)
    traces: list[object] = []
    failures: list[BaseException] = []
    result_lock = threading.Lock()

    def finalize() -> None:
        barrier.wait()
        try:
            result = recorder.finalize(ExecutionOutcome.CANCELLED)
            with result_lock:
                traces.append(result)
        except BaseException as exc:
            with result_lock:
                failures.append(exc)

    threads = [threading.Thread(target=finalize) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert len(traces) == 8
    assert all(trace == traces[0] for trace in traces)
    assert [event.sequence for event in recorder.events()] == [0, 1]


def test_concurrent_session_creation_allows_one_lifecycle_transition() -> None:
    recorder = ExecutionTraceRecorder(InMemoryExecutionStore(), "execution-session-race")
    barrier = threading.Barrier(6)
    successes: list[CanonicalEvent] = []
    failures: list[BaseException] = []
    result_lock = threading.Lock()

    def create_session() -> None:
        barrier.wait()
        try:
            event = recorder.emit(EventKind.SESSION_CREATED, session_id="session-1", payload={})
            with result_lock:
                successes.append(event)
        except BaseException as exc:
            with result_lock:
                failures.append(exc)

    threads = [threading.Thread(target=create_session) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(successes) == 1
    assert all(isinstance(error, (TraceRecorderError, StorageConflict)) for error in failures)
    assert [event.sequence for event in recorder.events()] == [0, 1]
