"""Contract tests for stable trace projection and finalization."""

from __future__ import annotations

import itertools
import threading
from datetime import datetime
from pathlib import Path

import pytest

from m3.events import EventFactory
from m3.execution_trace import (
    ExecutionTraceRecorder,
    TraceFinalizationConflict,
    TraceRecorderError,
)
from m3.storage import (
    InMemoryExecutionStore,
    SequenceConflict,
    SQLiteExecutionStore,
    StorageConflict,
    TerminalConflict,
)
from m3.trace.redaction import REDACTED, RedactionConfig, RedactionError
from m3.types import (
    Event,
    EventId,
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    ExecutionStatus,
    SessionId,
    TraceId,
    TurnId,
    TurnOutcome,
    TurnStatus,
)


def test_recorder_does_not_swallow_store_create_type_errors() -> None:
    class BrokenStore:
        def get_snapshot(self, _execution_id: object) -> None:
            return None

        def create(self, _snapshot: object, **_kwargs: object) -> None:
            raise TypeError("internal create failure")

    with pytest.raises(TypeError, match="internal create failure"):
        ExecutionTraceRecorder(BrokenStore(), "execution-type-error")  # type: ignore[arg-type]


def test_snapshot_is_derived_from_committed_events_and_previous_value_stays_immutable() -> (
    None
):
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-snapshot")
    before = recorder.snapshot()
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED,
        payload={"lifecycle": ExecutionStatus.STARTING.value},
    )
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED,
        payload={"lifecycle": ExecutionStatus.IDLE.value},
    )
    after = recorder.snapshot()
    assert before.lifecycle is ExecutionStatus.CREATED
    assert before.sequence == 0
    assert after.lifecycle is ExecutionStatus.IDLE
    assert after.sequence == 2
    assert before.lifecycle is ExecutionStatus.CREATED


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
    emitted = [
        recorder.emit(EventKind.DIAGNOSTIC, payload={"index": index})
        for index in range(4)
    ]
    assert all(event.timestamp.tzinfo is not None for event in emitted)
    assert all(
        left.monotonic_offset_ms <= right.monotonic_offset_ms
        for left, right in itertools.pairwise(emitted)
    )


def test_attached_recorder_continues_persistent_clock_offset(tmp_path: Path) -> None:
    execution_id = ExecutionId("execution-attached-clock")
    store = SQLiteExecutionStore(tmp_path / "attached-clock.sqlite")
    initial = ExecutionTraceRecorder(store, execution_id)
    initial_offset = initial.events()[0].monotonic_offset_ms
    factory = EventFactory(execution_id)
    prior = factory.create(
        EventKind.DIAGNOSTIC,
        monotonic_offset_ms=initial_offset + 42.0,
        payload={"source": "prior-process"},
    ).model_copy(update={"sequence": 1})
    store.append_events((prior,))

    attached = ExecutionTraceRecorder(store, execution_id, trace_id=initial.trace_id)
    emitted = attached.emit(
        EventKind.DIAGNOSTIC, payload={"source": "attached-process"}
    )
    trace = attached.finalize(ExecutionOutcome.COMPLETED)
    assert emitted.monotonic_offset_ms >= prior.monotonic_offset_ms
    assert trace.events[-1].monotonic_offset_ms >= emitted.monotonic_offset_ms
    assert trace.highest_sequence == trace.events[-1].sequence


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
        payload={"lifecycle": TurnStatus.RUNNING.value},
    )
    recorder.emit(
        EventKind.TURN_STATE_CHANGED,
        session_id="session-1",
        turn_id="turn-1",
        payload={
            "lifecycle": TurnStatus.FINISHED.value,
            "outcome": TurnOutcome.COMPLETED.value,
        },
    )
    turn = recorder.turn_snapshots()[0]
    assert turn.session_id.root == "session-1"
    assert turn.number == 1
    assert turn.lifecycle is TurnStatus.FINISHED
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
def test_every_terminal_outcome_has_a_terminal_trace_and_snapshot(
    outcome: ExecutionOutcome,
) -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, f"execution-{outcome.value}")
    trace = recorder.finalize(outcome)
    assert trace.completeness == "complete"
    assert trace.highest_sequence == trace.events[-1].sequence
    assert trace.events[-1].kind is EventKind.EXECUTION_FINISHED
    assert recorder.snapshot().lifecycle is ExecutionStatus.FINISHED
    assert recorder.snapshot().outcome is outcome


def test_cleanup_or_final_persistence_failure_makes_trace_partial_with_safe_limitations() -> (
    None
):
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-partial")
    with pytest.raises(TraceRecorderError):
        recorder.finalize(
            ExecutionOutcome.TIMED_OUT,
            cleanup_succeeded=False,
            persistence_succeeded=True,
            limitations=("caller-secret-must-not-be-stored", "capture_incomplete"),
        )
    trace = recorder.finalize(
        ExecutionOutcome.TIMED_OUT,
        cleanup_succeeded=False,
        persistence_succeeded=True,
        limitations=("capture_incomplete",),
    )
    assert trace.completeness == "partial"
    assert trace.limitations == ("capture_incomplete", "cleanup_failed")
    assert "caller-secret" not in repr(trace)
    persistence = ExecutionTraceRecorder(
        InMemoryExecutionStore(), "execution-persistence-failure"
    )
    persistence_trace = persistence.finalize(
        ExecutionOutcome.FAILED, persistence_succeeded=False
    )
    assert persistence_trace.completeness == "partial"
    assert persistence_trace.limitations == ("persistence_failed",)

    explicit = ExecutionTraceRecorder(
        InMemoryExecutionStore(), "execution-explicit-limitation"
    )
    with pytest.raises(TraceRecorderError):
        explicit.finalize(
            ExecutionOutcome.COMPLETED,
            limitations=("partial_trace", "unsafe-provider-detail"),
        )
    explicit_trace = explicit.finalize(
        ExecutionOutcome.COMPLETED,
        limitations=("partial_trace",),
    )
    assert explicit_trace.completeness == "partial"
    assert explicit_trace.limitations == ("partial_trace",)


def test_callbacks_see_the_whole_committed_batch_before_delivery() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-callback")
    observed: list[tuple[int, tuple[int, ...]]] = []

    def callback(event: Event) -> None:
        sequence = event.sequence
        observed.append((sequence, tuple(item.sequence for item in recorder.events())))

    store.subscribe("execution-callback", callback)
    recorder.emit(EventKind.DIAGNOSTIC, payload={"message": "redacted"})
    assert observed == [(1, (0, 1))]


def test_finalization_is_idempotent_but_conflicting_outcomes_are_rejected() -> None:
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "execution-idempotent")
    first = recorder.finalize(
        ExecutionOutcome.CANCELLED, direct_result={"kind": "ping"}
    )
    second = recorder.finalize(
        ExecutionOutcome.CANCELLED, direct_result={"kind": "call_tool"}
    )
    assert first == second
    assert len(recorder.events()) == 2
    assert recorder.events()[-1].payload["direct_result"]["kind"] == "ping"
    with pytest.raises(TraceFinalizationConflict):
        recorder.finalize(ExecutionOutcome.FAILED)


def test_payloads_are_redacted_before_commit_and_fail_closed() -> None:
    store = InMemoryExecutionStore()
    config = RedactionConfig(secrets=frozenset({"secret"}), include_environment=False)
    recorder = ExecutionTraceRecorder(
        store, "execution-redaction", redaction_config=config
    )
    event = recorder.emit(EventKind.DIAGNOSTIC, payload={"raw": "secret"})
    assert event.payload["raw"] == REDACTED
    assert store.events("execution-redaction")[-1].payload["raw"] == REDACTED
    with pytest.raises(RedactionError):
        recorder.emit(EventKind.DIAGNOSTIC, payload={"raw": object()})
    assert len(recorder.events()) == 2
    assert recorder.emit(EventKind.DIAGNOSTIC, payload={"raw": "safe"}).sequence == 2


def test_sqlite_reopen_and_payload_blob_contain_no_canaries(tmp_path: Path) -> None:
    literal = "classified-literal-canary"
    reference = "resolved-reference-canary"
    config = RedactionConfig(
        secrets=frozenset({literal, reference}),
        include_environment=False,
    )
    database = tmp_path / "trace.sqlite"
    blobs = tmp_path / "blobs"
    first = SQLiteExecutionStore(
        database, blob_root=blobs, config=config, payload_blob_threshold=16
    )
    recorder = ExecutionTraceRecorder(
        first, "execution-cross-process", redaction_config=config
    )
    recorder.emit(
        EventKind.DIAGNOSTIC,
        payload={
            "assistant": literal,
            "result": reference,
            "error": f"{literal}|{reference}",
        },
    )
    first.close()

    raw_files = [
        database.read_bytes(),
        *(path.read_bytes() for path in blobs.rglob("*") if path.is_file()),
    ]
    assert all(
        canary.encode() not in raw
        for raw in raw_files
        for canary in (literal, reference)
    )

    second = SQLiteExecutionStore(
        database, blob_root=blobs, config=config, payload_blob_threshold=16
    )
    restored = second.events("execution-cross-process")
    second.close()
    assert any(event.payload.get("assistant") == REDACTED for event in restored)
    assert all(canary not in repr(restored) for canary in (literal, reference))


def test_redaction_covers_event_metadata_outside_payload_without_changing_identity() -> (
    None
):
    store = InMemoryExecutionStore()
    config = RedactionConfig(
        secrets=frozenset({"server-secret"}), include_environment=False
    )
    recorder = ExecutionTraceRecorder(
        store, "execution-metadata-redaction", redaction_config=config
    )
    event = Event(
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
    malformed_terminal = Event(
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
        recorder.emit(
            EventKind.EXECUTION_STATE_CHANGED,
            payload={"lifecycle": ExecutionStatus.IDLE.value},
        )
    with pytest.raises(TraceRecorderError):
        recorder.emit(
            EventKind.TURN_STATE_CHANGED,
            session_id="missing-session",
            turn_id="missing-turn",
            payload={"lifecycle": TurnStatus.RUNNING.value},
        )
    assert len(recorder.events()) == 1
    with pytest.raises(TraceRecorderError):
        recorder.emit(
            EventKind.TURN_STATE_CHANGED,
            session_id="session-1",
            turn_id="turn-1",
            payload={"lifecycle": TurnStatus.FINISHED.value},
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
    recorder = ExecutionTraceRecorder(
        InMemoryExecutionStore(), "execution-concurrent-emits"
    )
    barrier = threading.Barrier(12)
    results: list[Event] = []
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


def test_failed_emit_does_not_consume_a_sequence() -> None:
    recorder = ExecutionTraceRecorder(InMemoryExecutionStore(), "execution-release")
    with pytest.raises(TraceRecorderError):
        recorder.emit(
            EventKind.EXECUTION_STATE_CHANGED,
            payload={"lifecycle": ExecutionStatus.IDLE.value},
        )
    event = recorder.emit(EventKind.DIAGNOSTIC, payload={"after": "failure"})
    assert event.sequence == 1
    assert [item.sequence for item in recorder.events()] == [0, 1]


class _SpyStore:
    """Delegate to a real store and count the calls a hot path must not make."""

    _WATCHED = frozenset(
        {
            "allocate",
            "allocate_sequence",
            "allocate_sequences",
            "release",
            "iter_events",
            "events",
            "get_snapshot",
        }
    )

    def __init__(self, store: InMemoryExecutionStore | SQLiteExecutionStore) -> None:
        self._store = store
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        attribute = getattr(self._store, name)
        if name not in self._WATCHED:
            return attribute

        def spy(*args: object, **kwargs: object) -> object:
            self.calls.append(name)
            return attribute(*args, **kwargs)

        return spy


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_emit_does_not_reserve_sequences_or_reload_history(
    backend: str, tmp_path: Path
) -> None:
    store = (
        InMemoryExecutionStore()
        if backend == "memory"
        else SQLiteExecutionStore(tmp_path / "spy.sqlite")
    )
    spy = _SpyStore(store)
    recorder = ExecutionTraceRecorder(spy, "execution-spy")  # type: ignore[arg-type]
    spy.calls.clear()
    events = [
        recorder.emit(EventKind.DIAGNOSTIC, payload={"index": index})
        for index in range(5)
    ]
    recorder.add_limitation("capture_incomplete")
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert spy.calls == []
    assert [event.sequence for event in store.events("execution-spy")] == list(range(7))


def _backend_store(
    backend: str, tmp_path: Path
) -> InMemoryExecutionStore | SQLiteExecutionStore:
    if backend == "memory":
        return InMemoryExecutionStore()
    return SQLiteExecutionStore(tmp_path / "backend.sqlite")


def _diagnostic(execution_id: str, sequence: int, name: str) -> Event:
    return Event(
        event_id=EventId(name),
        execution_id=ExecutionId(execution_id),
        sequence=sequence,
        kind=EventKind.DIAGNOSTIC,
        monotonic_offset_ms=0.0,
        payload={"holder": name},
    )


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("raw_evidence", [False, True])
def test_emit_leaves_a_reserved_sequence_to_its_holder(
    backend: str, raw_evidence: bool, tmp_path: Path
) -> None:
    store = _backend_store(backend, tmp_path)
    recorder = ExecutionTraceRecorder(store, "execution-reserved")
    (reserved,) = store.allocate("execution-reserved")
    assert reserved == 1
    for _ in range(2):
        with pytest.raises(StorageConflict) as raised:
            if raw_evidence:
                recorder.emit(
                    EventKind.DIAGNOSTIC,
                    raw_evidence_content=b"raw",
                    raw_evidence_media_type="text/plain",
                )
            else:
                recorder.emit(EventKind.DIAGNOSTIC, payload={"name": "recorder"})
        assert not isinstance(
            raised.value, (SequenceConflict, TraceFinalizationConflict)
        )
    assert [event.sequence for event in store.events("execution-reserved")] == [0]
    # The reservation survived: the next reservation steps over it.
    (next_reserved,) = store.allocate("execution-reserved")
    assert next_reserved == 2
    store.release("execution-reserved", (next_reserved,))
    store.append_events((_diagnostic("execution-reserved", reserved, "holder"),))
    after = recorder.emit(EventKind.DIAGNOSTIC, payload={"name": "recorder"})
    assert after.sequence == 2
    assert [event.sequence for event in store.events("execution-reserved")] == [
        0,
        1,
        2,
    ]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_record_still_commits_at_a_reserved_sequence(
    backend: str, tmp_path: Path
) -> None:
    store = _backend_store(backend, tmp_path)
    recorder = ExecutionTraceRecorder(store, "execution-record-reserved")
    store.allocate("execution-record-reserved")
    committed = recorder.record(_diagnostic("execution-record-reserved", 1, "direct"))
    assert committed.sequence == 1


class _ReservingStore:
    """Expose only the public store protocol plus allocate and release."""

    def __init__(self, store: InMemoryExecutionStore) -> None:
        self._store = store
        self.allocated: list[int] = []
        self.released: list[int] = []

    def __getattr__(self, name: str) -> object:
        if name.startswith("_append"):
            raise AttributeError(name)
        return getattr(self._store, name)

    def allocate_sequence(self, execution_id: str) -> int:
        sequence = self._store.allocate_sequence(execution_id)
        self.allocated.append(sequence)
        return sequence

    def release(self, execution_id: str, sequences: tuple[int, ...]) -> None:
        self.released.extend(sequences)
        self._store.release(execution_id, sequences)


def test_store_without_reservation_check_keeps_the_reserve_then_append_protocol() -> (
    None
):
    inner = InMemoryExecutionStore()
    store = _ReservingStore(inner)
    recorder = ExecutionTraceRecorder(store, "execution-third-party")  # type: ignore[arg-type]
    first = recorder.emit(EventKind.DIAGNOSTIC, payload={"n": 1})
    assert first.sequence == 1
    (held,) = inner.allocate("execution-third-party")
    assert held == 2
    with pytest.raises(StorageConflict):
        recorder.emit(EventKind.DIAGNOSTIC, payload={"n": 2})
    # The holder kept its slot and the recorder released only its own.
    assert store.allocated[-1] == 3
    assert store.released[-1] == 3
    inner.append_events((_diagnostic("execution-third-party", held, "holder"),))
    assert recorder.emit(EventKind.DIAGNOSTIC, payload={"n": 3}).sequence == 3


def test_store_without_sequence_allocation_cannot_emit() -> None:
    class Bare:
        def __init__(self) -> None:
            self._store = InMemoryExecutionStore()

        def __getattr__(self, name: str) -> object:
            if name.startswith(("_append", "allocate", "release")):
                raise AttributeError(name)
            return getattr(self._store, name)

    with pytest.raises(TraceRecorderError, match="sequence allocation"):
        ExecutionTraceRecorder(Bare(), "execution-bare")  # type: ignore[arg-type]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("already_added", [False, True])
def test_add_limitation_reports_an_execution_another_writer_finished(
    backend: str, already_added: bool, tmp_path: Path
) -> None:
    store = _backend_store(backend, tmp_path)
    recorder = ExecutionTraceRecorder(store, "execution-limit-race")
    if already_added:
        recorder.add_limitation("capture_incomplete")
    before = list(recorder._runtime_limitations)
    other = ExecutionTraceRecorder(store, "execution-limit-race")
    other.finalize(ExecutionOutcome.CANCELLED)
    with pytest.raises(TraceFinalizationConflict):
        recorder.add_limitation(
            "capture_incomplete" if already_added else "partial_trace"
        )
    assert recorder._runtime_limitations == before
    events = store.events("execution-limit-race")
    assert events[-1].kind is EventKind.EXECUTION_FINISHED
    assert [event.sequence for event in events] == list(range(len(events)))


def test_recorder_continues_after_a_conflicting_writer_without_losing_events(
    tmp_path: Path,
) -> None:
    database = tmp_path / "shared.sqlite"
    first_store = SQLiteExecutionStore(database)
    first = ExecutionTraceRecorder(first_store, "execution-two-writers")
    second_store = SQLiteExecutionStore(database)
    second = ExecutionTraceRecorder(second_store, "execution-two-writers")
    emitted = [
        first.emit(EventKind.DIAGNOSTIC, payload={"writer": "first", "n": 0}),
        second.emit(EventKind.DIAGNOSTIC, payload={"writer": "second", "n": 0}),
        first.emit(EventKind.DIAGNOSTIC, payload={"writer": "first", "n": 1}),
        second.emit(EventKind.DIAGNOSTIC, payload={"writer": "second", "n": 1}),
        first.emit(EventKind.DIAGNOSTIC, payload={"writer": "first", "n": 2}),
    ]
    assert [event.sequence for event in emitted] == [1, 2, 3, 4, 5]
    committed = first_store.events("execution-two-writers")
    assert [event.sequence for event in committed] == list(range(6))
    assert [event.payload.get("writer") for event in committed[1:]] == [
        "first",
        "second",
        "first",
        "second",
        "first",
    ]


def test_store_written_terminal_stops_emits_without_a_trailing_event(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "cancel.sqlite")
    spy = _SpyStore(store)
    recorder = ExecutionTraceRecorder(spy, "execution-store-terminal")  # type: ignore[arg-type]
    recorder.emit(EventKind.DIAGNOSTIC, payload={"before": True})
    assert store.request_cancel("execution-store-terminal", reason="stop")
    with pytest.raises(TraceFinalizationConflict):
        recorder.emit(EventKind.DIAGNOSTIC, payload={"after": True})
    events = store.events("execution-store-terminal")
    assert events[-1].kind is EventKind.EXECUTION_FINISHED
    assert [event.sequence for event in events] == list(range(len(events)))
    spy.calls.clear()
    with pytest.raises(TraceFinalizationConflict):
        recorder.emit(EventKind.DIAGNOSTIC, payload={"later": True})
    with pytest.raises(TraceFinalizationConflict):
        recorder.add_limitation("capture_incomplete")
    assert spy.calls == []
    assert recorder.finalize(ExecutionOutcome.CANCELLED).events[-1].kind is (
        EventKind.EXECUTION_FINISHED
    )


def _finish_elsewhere(
    store: InMemoryExecutionStore | SQLiteExecutionStore, execution_id: str
) -> None:
    """Commit a terminal event the way a writer other than the recorder would."""
    if isinstance(store, SQLiteExecutionStore):
        assert store.request_cancel(execution_id, reason="stop")
    else:
        ExecutionTraceRecorder(store, execution_id).finalize(ExecutionOutcome.CANCELLED)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_append_after_a_terminal_reports_it_at_a_stale_or_gap_sequence(
    backend: str, tmp_path: Path
) -> None:
    store = _backend_store(backend, tmp_path)
    ExecutionTraceRecorder(store, "execution-terminal-append")
    _finish_elsewhere(store, "execution-terminal-append")
    committed = store.events("execution-terminal-append")
    used = committed[-1].sequence
    for sequence in (used, used + 3):
        with pytest.raises(TerminalConflict):
            store.append_events(
                (_diagnostic("execution-terminal-append", sequence, "late"),)
            )
    assert store.events("execution-terminal-append") == committed


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_record_at_a_stale_sequence_reports_a_store_written_terminal(
    backend: str, tmp_path: Path
) -> None:
    store = _backend_store(backend, tmp_path)
    recorder = ExecutionTraceRecorder(store, "execution-stale-terminal")
    stale = recorder._next_sequence
    _finish_elsewhere(store, "execution-stale-terminal")
    with pytest.raises(TraceFinalizationConflict):
        recorder.record(_diagnostic("execution-stale-terminal", stale, "late"))
    with pytest.raises(TraceFinalizationConflict):
        recorder.emit(EventKind.DIAGNOSTIC, payload={"after": True})
    events = store.events("execution-stale-terminal")
    assert events[-1].kind is EventKind.EXECUTION_FINISHED
    assert [event.sequence for event in events] == list(range(len(events)))


def test_terminal_race_rejects_producer_after_finished_without_postterminal_event() -> (
    None
):
    recorder = ExecutionTraceRecorder(
        InMemoryExecutionStore(), "execution-terminal-race"
    )
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
    recorder = ExecutionTraceRecorder(
        InMemoryExecutionStore(), "execution-finalize-race"
    )
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
    recorder = ExecutionTraceRecorder(
        InMemoryExecutionStore(), "execution-session-race"
    )
    barrier = threading.Barrier(6)
    successes: list[Event] = []
    failures: list[BaseException] = []
    result_lock = threading.Lock()

    def create_session() -> None:
        barrier.wait()
        try:
            event = recorder.emit(
                EventKind.SESSION_CREATED, session_id="session-1", payload={}
            )
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
    assert all(
        isinstance(error, (TraceRecorderError, StorageConflict)) for error in failures
    )
    assert [event.sequence for event in recorder.events()] == [0, 1]
