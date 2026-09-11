"""Adversarial contract tests for ephemeral Phase 4 storage."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from mcp_pal.storage import (
    BlobIntegrityError,
    InMemoryArtifactStore,
    InMemoryExecutionStore,
    StorageConflict,
    TemporaryArtifactStore,
)
from mcp_pal.trace.redaction import RedactionConfig, RedactionError
from mcp_pal.types import (
    ArtifactRef,
    Event,
    EventId,
    EventKind,
    ExecutionId,
    ExecutionState,
    SessionId,
)


def _snapshot() -> ExecutionState:
    return ExecutionState(execution_id=ExecutionId("execution-1"))


def _event(sequence: int, *, payload: dict[str, object] | None = None) -> Event:
    return Event(
        event_id=EventId(f"event-{sequence}"),
        execution_id=ExecutionId("execution-1"),
        sequence=sequence,
        kind=EventKind.DIAGNOSTIC,
        monotonic_offset_ms=float(sequence),
        payload=payload or {"sequence": sequence},
    )


def test_append_is_contiguous_and_rejects_duplicate_or_out_of_order_sequences() -> None:
    store = InMemoryExecutionStore()
    store.create(_snapshot())
    store.append_events((_event(0), _event(1)))
    with pytest.raises(StorageConflict):
        store.append_events((_event(1),))
    with pytest.raises(StorageConflict):
        store.append_events((_event(3),))
    assert [event.sequence for event in store.events("execution-1")] == [0, 1]


def test_direct_store_writes_are_redacted_before_commit_and_callback_delivery() -> None:
    config = RedactionConfig(
        secrets=frozenset({"raw-secret"}), include_environment=False
    )
    store = InMemoryExecutionStore(config=config)
    store.create(_snapshot())
    observed: list[str] = []
    store.subscribe(
        "execution-1", lambda event: observed.append(str(event.payload["message"]))
    )
    store.append_events((_event(0, payload={"message": "raw-secret"}),))
    with store.transaction("execution-1") as transaction:
        transaction.append((_event(1, payload={"message": "raw-secret-2"}),))
    assert store.events("execution-1")[0].payload["message"] == "[REDACTED]"
    assert store.events("execution-1")[1].payload["message"] == "[REDACTED]-2"
    assert observed == ["[REDACTED]", "[REDACTED]-2"]


def test_direct_store_redaction_failure_has_zero_visibility() -> None:
    config = RedactionConfig(
        secrets=frozenset({"raw-secret"}), include_environment=False
    )
    store = InMemoryExecutionStore(config=config)
    store.create(_snapshot())
    callbacks: list[Event] = []
    store.subscribe("execution-1", callbacks.append)
    malformed = Event.model_construct(
        event_id=EventId("malformed"),
        execution_id=ExecutionId("execution-1"),
        sequence=0,
        kind=EventKind.DIAGNOSTIC,
        monotonic_offset_ms=0.0,
        payload={"unsupported": object()},
    )
    with pytest.raises(RedactionError):
        store.append_events((malformed,))
    assert store.events("execution-1") == ()
    assert callbacks == []


def test_store_snapshot_is_atomically_derived_from_terminal_events() -> None:
    store = InMemoryExecutionStore()
    store.create(_snapshot())
    terminal = Event(
        event_id=EventId("terminal-snapshot"),
        execution_id=ExecutionId("execution-1"),
        sequence=0,
        kind=EventKind.EXECUTION_FINISHED,
        monotonic_offset_ms=0.0,
        payload={"outcome": "timed_out"},
    )
    store.append_events((terminal,))
    snapshot = store.get_snapshot("execution-1")
    assert snapshot is not None
    assert snapshot.lifecycle.value == "finished"
    assert snapshot.outcome is not None and snapshot.outcome.value == "timed_out"
    assert snapshot.sequence == terminal.sequence
    assert snapshot.finished_at == store.events("execution-1")[0].timestamp


def test_failed_append_releases_only_its_reservation_and_preserves_other_reservations() -> (
    None
):
    store = InMemoryExecutionStore()
    store.create(_snapshot())
    reserved = store.allocate("execution-1", count=2)
    invalid = Event(
        event_id=EventId("invalid-reservation"),
        execution_id=ExecutionId("execution-1"),
        sequence=reserved[0],
        kind=EventKind.SESSION_STATE_CHANGED,
        session_id=SessionId("missing"),
        monotonic_offset_ms=0.0,
        payload={"state": "active"},
    )
    with pytest.raises(StorageConflict):
        store.append_events((invalid,))
    assert store.allocate("execution-1", count=2) == (reserved[0], reserved[1] + 1)


def test_redaction_failure_releases_reservation_without_visibility() -> None:
    config = RedactionConfig(secrets=frozenset({"secret"}), include_environment=False)
    store = InMemoryExecutionStore(config=config)
    store.create(_snapshot())
    sequence = store.allocate_sequence("execution-1")
    malformed = Event.model_construct(
        event_id=EventId("unsupported-reservation"),
        execution_id=ExecutionId("execution-1"),
        sequence=sequence,
        kind=EventKind.DIAGNOSTIC,
        monotonic_offset_ms=0.0,
        payload={"unsupported": object()},
    )
    with pytest.raises(RedactionError):
        store.append_events((malformed,))
    assert store.events("execution-1") == ()
    assert store.allocate_sequence("execution-1") == sequence


def test_session_state_requires_a_previously_committed_session_creation() -> None:
    store = InMemoryExecutionStore()
    store.create(_snapshot())
    state = Event(
        event_id=EventId("session-state"),
        execution_id=ExecutionId("execution-1"),
        sequence=0,
        kind=EventKind.SESSION_STATE_CHANGED,
        session_id=SessionId("missing-session"),
        monotonic_offset_ms=0.0,
        payload={"state": "active"},
    )
    with pytest.raises(StorageConflict):
        store.append_events((state,))
    assert store.events("execution-1") == ()


def _session_event(sequence: int, kind: EventKind, session: str = "session-1") -> Event:
    return Event(
        event_id=EventId(f"session-event-{sequence}"),
        execution_id=ExecutionId("execution-1"),
        sequence=sequence,
        kind=kind,
        session_id=SessionId(session),
        monotonic_offset_ms=float(sequence),
        payload={"state": "active"} if kind is EventKind.SESSION_STATE_CHANGED else {},
    )


def test_session_create_then_state_in_one_atomic_batch_is_visible_after_commit() -> (
    None
):
    store = InMemoryExecutionStore()
    store.create(_snapshot())
    store.append_events(
        (
            _session_event(0, EventKind.SESSION_CREATED),
            _session_event(1, EventKind.SESSION_STATE_CHANGED),
        )
    )
    assert [event.kind for event in store.events("execution-1")] == [
        EventKind.SESSION_CREATED,
        EventKind.SESSION_STATE_CHANGED,
    ]


@pytest.mark.parametrize(
    "events",
    (
        (
            _session_event(0, EventKind.SESSION_STATE_CHANGED),
            _session_event(1, EventKind.SESSION_CREATED),
        ),
        (
            _session_event(0, EventKind.SESSION_CREATED),
            _session_event(1, EventKind.SESSION_STATE_CHANGED, "missing"),
        ),
        (
            _session_event(0, EventKind.SESSION_CREATED),
            _session_event(1, EventKind.SESSION_CREATED),
        ),
    ),
)
def test_invalid_session_batch_has_no_visibility(events: tuple[Event, Event]) -> None:
    store = InMemoryExecutionStore()
    store.create(_snapshot())
    with pytest.raises(StorageConflict):
        store.append_events(events)
    assert store.events("execution-1") == ()


def test_transaction_rollback_has_no_visibility_and_callbacks_are_after_commit() -> (
    None
):
    store = InMemoryExecutionStore()
    store.create(_snapshot())
    observed: list[tuple[int, tuple[int, ...]]] = []

    def callback(event: Event) -> None:
        observed.append(
            (
                event.sequence,
                tuple(item.sequence for item in store.events("execution-1")),
            )
        )

    store.subscribe("execution-1", callback)
    with pytest.raises(RuntimeError):
        with store.transaction("execution-1") as transaction:
            transaction.append((_event(0), _event(1)))
            assert store.events("execution-1") == ()
            raise RuntimeError("rollback")
    assert store.events("execution-1") == ()
    assert observed == []
    with store.transaction("execution-1") as transaction:
        transaction.append((_event(0), _event(1)))
        assert store.events("execution-1") == ()
    assert observed == [(0, (0, 1)), (1, (0, 1))]


def test_reads_are_isolated_and_snapshot_is_immutable() -> None:
    store = InMemoryExecutionStore()
    snapshot = _snapshot()
    store.create(snapshot)
    store.append_events((_event(0, payload={"nested": {"value": 1}}),))
    first = store.events("execution-1")[0]
    with pytest.raises(TypeError):
        first.payload["nested"]["value"] = 2
    assert store.events("execution-1")[0].payload["nested"]["value"] == 1
    assert store.get_snapshot("execution-1") == snapshot


def test_concurrent_single_event_appends_have_unique_ordered_sequences() -> None:
    store = InMemoryExecutionStore()
    store.create(_snapshot())
    failures: list[BaseException] = []
    next_event = 0
    next_event_lock = threading.Lock()

    def append_one() -> None:
        nonlocal next_event
        with next_event_lock:
            sequence = next_event
            next_event += 1
        # Producers must use the allocator for a race-free sequence in real
        # integrations; this test also confirms conflicting stale appends do
        # not corrupt committed state.
        try:
            with store._lock:
                committed = store.events("execution-1")
                expected = committed[-1].sequence + 1 if committed else 0
                event = _event(expected, payload={"producer": sequence})
                store.append_events((event,))
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=append_one) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert [event.sequence for event in store.events("execution-1")] == list(range(12))


def test_sequence_allocator_reserves_unique_numbers_without_visibility() -> None:
    store = InMemoryExecutionStore()
    store.create(_snapshot())
    first = store.allocate("execution-1", count=2)
    second = store.allocate("execution-1", count=2)
    assert first == (0, 1)
    assert second == (2, 3)
    assert store.events("execution-1") == ()
    store.append_events((_event(0), _event(1), _event(2), _event(3)))
    assert [event.sequence for event in store.events("execution-1")] == [0, 1, 2, 3]


def test_abandoned_sequence_reservation_can_be_released() -> None:
    store = InMemoryExecutionStore()
    store.create(_snapshot())
    reserved = store.allocate("execution-1", count=2)
    store.release("execution-1", reserved)
    assert store.allocate_sequence("execution-1") == 0


@pytest.mark.parametrize("factory", (InMemoryArtifactStore, TemporaryArtifactStore))
def test_artifact_ref_is_resolved_and_forged_metadata_cannot_read_or_delete(
    factory: type[InMemoryArtifactStore] | type[TemporaryArtifactStore],
) -> None:
    store = factory()
    ref = store.put("execution-1", "safe.txt", b"secret-free")
    forged = ArtifactRef(
        artifact_id=ref.artifact_id,
        execution_id=ExecutionId("other-execution"),
        name=ref.name,
        size_bytes=ref.size_bytes,
        sha256=ref.sha256,
    )
    with pytest.raises(StorageConflict) as read_error:
        store.get(forged)
    assert "other-execution" not in str(read_error.value)
    with pytest.raises(StorageConflict):
        store.delete(forged)
    assert store.get(ref) == b"secret-free"
    store.cleanup()


@pytest.mark.parametrize("factory", (InMemoryArtifactStore, TemporaryArtifactStore))
def test_artifact_stores_redact_before_persistence(
    factory: type[InMemoryArtifactStore] | type[TemporaryArtifactStore],
) -> None:
    config = RedactionConfig(secrets=frozenset({"original"}), include_environment=False)
    store = factory(config=config)
    ref = store.put("execution-1", "raw.txt", b"original")
    assert store.get(ref) == b"[REDACTED]"
    assert b"original" not in store.get(ref)
    store.cleanup()


@pytest.mark.parametrize("factory", (InMemoryArtifactStore, TemporaryArtifactStore))
def test_artifact_redacts_metadata_and_cannot_accept_per_write_policy_override(
    factory: type[InMemoryArtifactStore] | type[TemporaryArtifactStore],
) -> None:
    config = RedactionConfig(
        secrets=frozenset({"private-secret"}), include_environment=False
    )
    store = factory(config=config)
    ref = store.put(
        "execution-1", "private-secret.txt", b"safe", media_type="private-secret/type"
    )
    assert ref.name == "[REDACTED].txt"
    assert ref.media_type == "[REDACTED]/type"
    with pytest.raises(TypeError):
        store.put("execution-1", "other.txt", b"safe", config=RedactionConfig())  # type: ignore[call-arg]
    store.cleanup()


def test_temporary_store_concurrent_same_content_puts_share_one_blob(
    tmp_path: Path,
) -> None:
    store = TemporaryArtifactStore(tmp_path / "artifacts")
    refs = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def put_one() -> None:
        try:
            ref = store.put("execution-1", "same.txt", b"same-content")
            with lock:
                refs.append(ref)
        except BaseException as exc:
            with lock:
                failures.append(exc)

    threads = [threading.Thread(target=put_one) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert len(refs) == 16
    assert {ref.sha256 for ref in refs} == {hashlib.sha256(b"same-content").hexdigest()}
    assert all(store.get(ref) == b"same-content" for ref in refs)
    store.cleanup()


@pytest.mark.parametrize("factory", (InMemoryArtifactStore, TemporaryArtifactStore))
def test_large_blob_roundtrip_is_content_addressed_and_validated(
    factory: type[InMemoryArtifactStore] | type[TemporaryArtifactStore],
) -> None:
    store = factory()
    content = (b"mcp-pal-large-blob-" * 200_000) + b"!"
    ref = store.put(
        "execution-1", "large.bin", content, media_type="application/octet-stream"
    )
    assert ref.size_bytes == len(content)
    assert ref.sha256 == hashlib.sha256(content).hexdigest()
    assert store.get(ref) == content
    # A second reference reuses the same content-addressed blob.
    second = store.put("execution-1", "copy.bin", content)
    assert second.sha256 == ref.sha256
    assert store.get(second) == content
    if type(store) is InMemoryArtifactStore:
        store._blobs[ref.sha256] = b"tampered"
        with pytest.raises(BlobIntegrityError):
            store.get(ref)
    store.cleanup()
    store.cleanup()  # idempotent


def test_temporary_store_uses_contained_atomic_paths_and_cleans_owned_root() -> None:
    store = TemporaryArtifactStore()
    root = store.root
    ref = store.put("execution-1", "trace.json", b"{}")
    path = root / ref.sha256[:2] / ref.sha256[2:4] / f"{ref.sha256}.gz"
    assert path.exists()
    assert path.resolve().is_relative_to(root.resolve())
    store.cleanup()
    assert not root.exists()
    store.cleanup()


def test_temporary_store_detects_tampering_and_does_not_follow_metadata_path(
    tmp_path: Path,
) -> None:
    store = TemporaryArtifactStore(tmp_path / "owned")
    content = b"stable"
    ref = store.put("execution-1", "trace", content)
    path = store.root / ref.sha256[:2] / ref.sha256[2:4] / f"{ref.sha256}.gz"
    path.write_bytes(b"not gzip")
    with pytest.raises(BlobIntegrityError):
        store.get(ref)
    assert (store.root / "owned").resolve().is_relative_to(store.root.resolve())
    store.cleanup()
