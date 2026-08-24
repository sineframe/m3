"""Ephemeral execution and artifact storage for the SDK.

The stores in this module deliberately have no knowledge of the application
database.  They are small, deterministic implementations of the contracts
used by an execution handle: metadata and events are committed atomically,
and an event is not observable until its append transaction has committed.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Protocol, TypeAlias

from ..trace.redaction import RedactionConfig, redact_artifact_bytes, redact_for_persistence, redact_model_json
from ..types import (
    ArtifactId,
    ArtifactRef,
    CanonicalEvent,
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    ExecutionSnapshot,
    LifecycleState,
)


class StorageError(Exception):
    """Base class for expected ephemeral storage failures."""

    code = "storage_error"


class StorageConflict(StorageError):
    """The append or snapshot operation conflicts with committed state."""

    code = "storage_conflict"


class BlobIntegrityError(StorageError):
    """A compressed blob does not match its recorded hash or length."""

    code = "blob_integrity_error"


class ArtifactNotFound(StorageError):
    """An artifact or content-addressed blob is not present."""

    code = "artifact_not_found"


EventCallback: TypeAlias = Callable[[CanonicalEvent], None]


class ExecutionTransaction(Protocol):
    """Uncommitted event batch used by :class:`ExecutionStore`."""

    def append(self, events: Sequence[CanonicalEvent]) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __enter__(self) -> "ExecutionTransaction": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class ExecutionStore(Protocol):
    """Store contract for immutable execution snapshots and event streams."""

    def create(self, snapshot: ExecutionSnapshot) -> None: ...

    def get_snapshot(self, execution_id: ExecutionId | str) -> ExecutionSnapshot | None: ...

    def save_snapshot(self, snapshot: ExecutionSnapshot) -> None: ...

    def append_events(self, events: Sequence[CanonicalEvent]) -> None: ...

    def iter_events(
        self, execution_id: ExecutionId | str, *, after_sequence: int = -1
    ) -> Iterator[CanonicalEvent]: ...

    def transaction(self, execution_id: ExecutionId | str) -> ExecutionTransaction: ...

    def release(self, execution_id: ExecutionId | str, sequences: Sequence[int]) -> None: ...

    def subscribe(self, execution_id: ExecutionId | str, callback: EventCallback) -> Callable[[], None]: ...


class ArtifactStore(Protocol):
    """Content-addressed artifact/blob store contract."""

    def put(
        self,
        execution_id: ExecutionId | str,
        name: str,
        content: bytes,
        *,
        media_type: str | None = None,
    ) -> ArtifactRef: ...

    def get(self, artifact: ArtifactRef | ArtifactId | str) -> bytes: ...

    def get_ref(self, artifact_id: ArtifactId | str) -> ArtifactRef: ...

    def iter_refs(self, execution_id: ExecutionId | str | None = None) -> Iterator[ArtifactRef]: ...

    def delete(self, artifact: ArtifactRef | ArtifactId | str) -> None: ...

    def cleanup(self) -> None: ...


def _execution_key(value: ExecutionId | str) -> str:
    return str(value.root if isinstance(value, ExecutionId) else value)


def _artifact_key(value: ArtifactId | str) -> str:
    return str(value.root if isinstance(value, ArtifactId) else value)


class _ExecutionBatch(AbstractContextManager["_ExecutionBatch"]):
    def __init__(self, store: "InMemoryExecutionStore", execution_id: str) -> None:
        self._store = store
        self._execution_id = execution_id
        self._events: list[CanonicalEvent] = []
        self._done = False

    def append(self, events: Sequence[CanonicalEvent]) -> None:
        if self._done:
            raise StorageConflict("transaction is already closed")
        for event in events:
            if _execution_key(event.execution_id) != self._execution_id:
                raise StorageConflict("all events in a transaction must belong to its execution")
        self._events.extend(events)

    def commit(self) -> None:
        if self._done:
            return
        self._store._commit(self._execution_id, tuple(self._events))
        self._done = True

    def rollback(self) -> None:
        self._events.clear()
        self._done = True

    def __enter__(self) -> "_ExecutionBatch":
        if self._done:
            raise StorageConflict("transaction is already closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return None


class InMemoryExecutionStore:
    """Thread-safe execution metadata store with commit-gated visibility."""

    def __init__(self, *, config: RedactionConfig | None = None) -> None:
        self._lock = threading.RLock()
        self._snapshots: dict[str, ExecutionSnapshot] = {}
        self._events: dict[str, tuple[CanonicalEvent, ...]] = {}
        self._callbacks: dict[str, list[EventCallback]] = {}
        self._reserved_sequences: dict[str, set[int]] = {}
        self._redaction_config = config if config is not None else RedactionConfig.from_environment()

    def create(self, snapshot: ExecutionSnapshot) -> None:
        key = _execution_key(snapshot.execution_id)
        with self._lock:
            if key in self._snapshots:
                raise StorageConflict("execution already exists")
            self._snapshots[key] = snapshot.model_copy()
            self._events[key] = ()
            self._reserved_sequences[key] = set()

    # Friendly aliases are intentionally kept on the concrete store while the
    # protocol stays small and framework-neutral.
    create_execution = create

    def get_snapshot(self, execution_id: ExecutionId | str) -> ExecutionSnapshot | None:
        key = _execution_key(execution_id)
        with self._lock:
            snapshot = self._snapshots.get(key)
            return snapshot.model_copy() if snapshot is not None else None

    def save_snapshot(self, snapshot: ExecutionSnapshot) -> None:
        key = _execution_key(snapshot.execution_id)
        with self._lock:
            if key not in self._snapshots:
                raise StorageConflict("execution does not exist")
            derived = self._derive_snapshot(key, self._events[key])
            if snapshot != derived:
                raise StorageConflict("snapshot is derived from committed events")
            self._snapshots[key] = derived

    update_snapshot = save_snapshot

    def append_events(self, events: Sequence[CanonicalEvent]) -> None:
        batch = tuple(events)
        if not batch:
            return
        execution_id = _execution_key(batch[0].execution_id)
        self._commit(execution_id, batch)

    append = append_events

    def _commit(self, execution_id: str, events: tuple[CanonicalEvent, ...]) -> None:
        try:
            self._commit_checked(execution_id, events)
        except Exception:
            with self._lock:
                reserved = self._reserved_sequences.get(execution_id)
                if reserved is not None:
                    failed_sequences = {event.sequence for event in events}
                    reserved.difference_update(failed_sequences)
            raise

    def _commit_checked(self, execution_id: str, events: tuple[CanonicalEvent, ...]) -> None:
        if not events:
            return
        safe_events = tuple(self._redact_event(event) for event in events)
        callbacks: tuple[EventCallback, ...]
        committed: tuple[CanonicalEvent, ...]
        with self._lock:
            if execution_id not in self._snapshots:
                raise StorageConflict("execution does not exist")
            current = self._events[execution_id]
            expected = current[-1].sequence + 1 if current else 0
            seen_sequences: set[int] = set()
            seen_ids: set[str] = set()
            candidate_sessions = {
                event.session_id
                for event in current
                if event.kind is EventKind.SESSION_CREATED and event.session_id is not None
            }
            for event in safe_events:
                if event.kind is EventKind.SESSION_STATE_CHANGED:
                    if event.session_id is None or event.session_id not in candidate_sessions:
                        raise StorageConflict("session does not exist")
                elif event.kind is EventKind.SESSION_CREATED:
                    if event.session_id is None:
                        raise StorageConflict("session event requires a session")
                    if event.session_id in candidate_sessions:
                        raise StorageConflict("session already exists")
                    candidate_sessions.add(event.session_id)
            for event in safe_events:
                if _execution_key(event.execution_id) != execution_id:
                    raise StorageConflict("all events in an append must belong to one execution")
                if event.sequence != expected:
                    raise StorageConflict("event sequence must be contiguous")
                if event.sequence in seen_sequences:
                    raise StorageConflict("duplicate event sequence in append")
                if str(event.event_id.root) in seen_ids:
                    raise StorageConflict("duplicate event id in append")
                seen_sequences.add(event.sequence)
                seen_ids.add(str(event.event_id.root))
                expected += 1
            existing_ids = {str(item.event_id.root) for item in current}
            if existing_ids.intersection(seen_ids):
                raise StorageConflict("event id is already committed")
            candidate_events = current + tuple(item.model_copy() for item in safe_events)
            candidate_snapshot = self._derive_snapshot(execution_id, candidate_events)
            # Tuple replacement is the commit point. Readers cannot observe
            # the candidate batch because it is never placed in _events earlier.
            # CanonicalEvent is recursively immutable, so a shallow model
            # copy preserves isolation without deepcopying its private frozen
            # mapping implementation.
            committed = tuple(item.model_copy() for item in safe_events)
            self._events[execution_id] = current + committed
            self._snapshots[execution_id] = candidate_snapshot
            for event in safe_events:
                self._reserved_sequences[execution_id].discard(event.sequence)
            callbacks = tuple(self._callbacks.get(execution_id, ()))
        # Callbacks run after releasing the lock and after the complete batch
        # is visible. A broken observer cannot roll back committed evidence.
        for event in committed:
            for callback in callbacks:
                try:
                    callback(event.model_copy())
                except Exception:
                    continue

    def _derive_snapshot(self, execution_id: str, events: tuple[CanonicalEvent, ...]) -> ExecutionSnapshot:
        previous = self._snapshots[execution_id]
        lifecycle = LifecycleState.CREATED
        outcome: ExecutionOutcome | None = None
        created_at = previous.created_at
        finished_at: datetime | None = None
        highest = events[-1].sequence if events else previous.sequence
        for event in events:
            if event.kind is EventKind.EXECUTION_CREATED:
                created_at = event.timestamp
                lifecycle = LifecycleState.CREATED
                outcome = None
                finished_at = None
            elif event.kind is EventKind.EXECUTION_STATE_CHANGED:
                value = event.payload.get("lifecycle", event.payload.get("state"))
                try:
                    lifecycle = LifecycleState(value)
                except (TypeError, ValueError) as exc:
                    raise StorageConflict("execution state payload is invalid") from exc
                if lifecycle is LifecycleState.FINISHED:
                    raise StorageConflict("execution state payload is invalid")
            elif event.kind is EventKind.EXECUTION_FINISHED:
                try:
                    outcome = ExecutionOutcome(event.payload["outcome"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise StorageConflict("execution terminal payload is invalid") from exc
                lifecycle = LifecycleState.FINISHED
                finished_at = event.timestamp
        return ExecutionSnapshot(
            execution_id=ExecutionId(execution_id),
            lifecycle=lifecycle,
            outcome=outcome,
            sequence=highest,
            created_at=created_at if created_at.tzinfo is not None else datetime.now(timezone.utc),
            finished_at=finished_at,
        )

    def _redact_event(self, event: CanonicalEvent) -> CanonicalEvent:
        projected = redact_model_json(event, config=self._redaction_config, path="$.event")
        if not isinstance(projected, Mapping):
            raise StorageError("event projection is invalid")
        immutable_fields = (
            "schema_id", "schema_version", "event_id", "execution_id", "sequence", "kind",
            "session_id", "turn_id", "server_binding", "connection_id", "correlation",
            "lifecycle_phase", "payload_ref", "raw_evidence_ref", "reasoning",
        )
        try:
            safe_event = CanonicalEvent.model_validate(projected)
        except Exception:
            # Pydantic's validation context can render projected input.  The
            # redaction boundary must not preserve it as an exception cause.
            raise StorageError("event projection is invalid") from None
        if any(getattr(safe_event, field) != getattr(event, field) for field in immutable_fields):
            raise StorageError("event identity changed during redaction")
        return safe_event

    def iter_events(self, execution_id: ExecutionId | str, *, after_sequence: int = -1) -> Iterator[CanonicalEvent]:
        if after_sequence < -1:
            raise ValueError("after_sequence must be >= -1")
        key = _execution_key(execution_id)
        with self._lock:
            events = tuple(event.model_copy() for event in self._events.get(key, ()))
        return iter(event for event in events if event.sequence > after_sequence)

    def events(self, execution_id: ExecutionId | str, *, after_sequence: int = -1) -> tuple[CanonicalEvent, ...]:
        return tuple(self.iter_events(execution_id, after_sequence=after_sequence))

    def transaction(self, execution_id: ExecutionId | str) -> ExecutionTransaction:
        key = _execution_key(execution_id)
        with self._lock:
            if key not in self._snapshots:
                raise StorageConflict("execution does not exist")
        return _ExecutionBatch(self, key)

    def subscribe(self, execution_id: ExecutionId | str, callback: EventCallback) -> Callable[[], None]:
        key = _execution_key(execution_id)
        with self._lock:
            if key not in self._snapshots:
                raise StorageConflict("execution does not exist")
            self._callbacks.setdefault(key, []).append(callback)

        def unsubscribe() -> None:
            with self._lock:
                callbacks = self._callbacks.get(key, [])
                if callback in callbacks:
                    callbacks.remove(callback)

        return unsubscribe

    def allocate(self, execution_id: ExecutionId | str, *, count: int = 1) -> tuple[int, ...]:
        """Reserve the next per-execution sequence numbers.

        Reservation is invisible to readers. A reservation is consumed when
        the corresponding event is committed; an abandoned reservation simply
        remains available as the expected next sequence for a later producer.
        """
        if count < 1:
            raise ValueError("count must be positive")
        key = _execution_key(execution_id)
        with self._lock:
            if key not in self._snapshots:
                raise StorageConflict("execution does not exist")
            current = self._events[key]
            committed_next = current[-1].sequence + 1 if current else 0
            reserved = self._reserved_sequences[key]
            candidate = committed_next
            values_list: list[int] = []
            while len(values_list) < count:
                if candidate not in reserved:
                    values_list.append(candidate)
                candidate += 1
            values = tuple(values_list)
            reserved.update(values)
            return values

    def allocate_sequence(self, execution_id: ExecutionId | str) -> int:
        return self.allocate(execution_id, count=1)[0]

    allocate_sequences = allocate

    def release(self, execution_id: ExecutionId | str, sequences: Sequence[int]) -> None:
        """Release uncommitted reservations after producer cancellation."""
        key = _execution_key(execution_id)
        with self._lock:
            if key not in self._snapshots:
                raise StorageConflict("execution does not exist")
            reserved = self._reserved_sequences[key]
            if any(sequence < 0 or sequence not in reserved for sequence in sequences):
                raise StorageConflict("sequence reservation is invalid")
            reserved.difference_update(sequences)

    release_sequences = release

    def close(self) -> None:
        with self._lock:
            self._callbacks.clear()


class InMemoryArtifactStore:
    """In-memory compressed, content-addressed artifact store."""

    def __init__(self, *, config: RedactionConfig | None = None) -> None:
        self._lock = threading.RLock()
        self._refs: dict[str, ArtifactRef] = {}
        self._blobs: dict[str, bytes] = {}
        self._redaction_config = config if config is not None else RedactionConfig.from_environment()

    def put(
        self,
        execution_id: ExecutionId | str,
        name: str,
        content: bytes,
        *,
        media_type: str | None = None,
    ) -> ArtifactRef:
        if not name:
            raise ValueError("artifact name must not be empty")
        safe_content = self._prepare_content(content)
        safe_name, safe_media_type = self._prepare_metadata(name, media_type)
        ref = self._new_ref(execution_id, safe_name, safe_content, media_type=safe_media_type)
        compressed = gzip.compress(safe_content, mtime=0)
        self._commit_ref(ref, compressed)
        return ref

    def _prepare_content(self, content: bytes) -> bytes:
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        return redact_artifact_bytes(
            content,
            config=self._redaction_config,
        ).data

    def _prepare_metadata(self, name: str, media_type: str | None) -> tuple[str, str | None]:
        safe_name = redact_for_persistence(name, config=self._redaction_config, path="$.artifact.name")
        safe_media_type = redact_for_persistence(media_type, config=self._redaction_config, path="$.artifact.media_type")
        if not isinstance(safe_name, str) or (safe_media_type is not None and not isinstance(safe_media_type, str)):
            raise StorageError("artifact metadata could not be redacted")
        if not safe_name:
            raise ValueError("artifact name must not be empty")
        return safe_name, safe_media_type

    def _new_ref(
        self,
        execution_id: ExecutionId | str,
        name: str,
        content: bytes,
        *,
        media_type: str | None,
    ) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = ArtifactId(f"artifact-{uuid.uuid4().hex}")
        return ArtifactRef(
            artifact_id=artifact_id,
            execution_id=ExecutionId(_execution_key(execution_id)),
            name=name,
            media_type=media_type,
            size_bytes=len(content),
            sha256=digest,
            redacted=True,
        )

    def _commit_ref(self, ref: ArtifactRef, compressed: bytes) -> None:
        with self._lock:
            self._blobs.setdefault(ref.sha256, compressed)
            self._refs[_artifact_key(ref.artifact_id)] = ref

    def get(self, artifact: ArtifactRef | ArtifactId | str) -> bytes:
        ref = self._resolve_ref(artifact)
        with self._lock:
            compressed = self._blobs.get(ref.sha256)
        if compressed is None:
            raise ArtifactNotFound("artifact blob not found")
        return _verify_blob(compressed, ref.sha256, ref.size_bytes)

    def get_ref(self, artifact_id: ArtifactId | str) -> ArtifactRef:
        key = _artifact_key(artifact_id)
        with self._lock:
            ref = self._refs.get(key)
            if ref is None:
                raise ArtifactNotFound("artifact does not exist")
            return ref.model_copy()

    def _resolve_ref(self, artifact: ArtifactRef | ArtifactId | str) -> ArtifactRef:
        stored = self.get_ref(artifact.artifact_id if isinstance(artifact, ArtifactRef) else artifact)
        if isinstance(artifact, ArtifactRef) and stored != artifact:
            raise StorageConflict("artifact reference does not match stored metadata")
        return stored

    def iter_refs(self, execution_id: ExecutionId | str | None = None) -> Iterator[ArtifactRef]:
        selected = None if execution_id is None else _execution_key(execution_id)
        with self._lock:
            refs = tuple(ref.model_copy() for ref in self._refs.values() if selected is None or _execution_key(ref.execution_id) == selected)
        return iter(refs)

    def delete(self, artifact: ArtifactRef | ArtifactId | str) -> None:
        ref = self._resolve_ref(artifact)
        key = _artifact_key(ref.artifact_id)
        with self._lock:
            self._refs.pop(key, None)
            if not any(item.sha256 == ref.sha256 for item in self._refs.values()):
                self._blobs.pop(ref.sha256, None)

    def cleanup(self) -> None:
        with self._lock:
            self._refs.clear()
            self._blobs.clear()

    close = cleanup


def _verify_blob(compressed: bytes, expected_sha256: str, expected_length: int) -> bytes:
    try:
        content = gzip.decompress(compressed)
    except (OSError, EOFError) as exc:
        raise BlobIntegrityError("compressed artifact cannot be decompressed") from exc
    if len(content) != expected_length or hashlib.sha256(content).hexdigest() != expected_sha256:
        raise BlobIntegrityError("artifact hash or uncompressed length does not match metadata")
    return content


class TemporaryArtifactStore(InMemoryArtifactStore):
    """Filesystem-backed temporary artifact store with atomic blob placement."""

    def __init__(self, root: str | os.PathLike[str] | None = None, *, config: RedactionConfig | None = None) -> None:
        super().__init__(config=config)
        self._owned_root = root is None
        self._root = Path(tempfile.mkdtemp(prefix="mcp-pal-artifacts-")) if root is None else Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._root = self._root.resolve()
        self._paths: set[Path] = set()

    @property
    def root(self) -> Path:
        return self._root

    def _blob_path(self, digest: str) -> Path:
        path = (self._root / digest[:2] / digest[2:4] / f"{digest}.gz").resolve()
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise StorageError("artifact path escaped store root") from exc
        return path

    def put(
        self,
        execution_id: ExecutionId | str,
        name: str,
        content: bytes,
        *,
        media_type: str | None = None,
    ) -> ArtifactRef:
        if not name:
            raise ValueError("artifact name must not be empty")
        safe_content = self._prepare_content(content)
        safe_name, safe_media_type = self._prepare_metadata(name, media_type)
        ref = self._new_ref(execution_id, safe_name, safe_content, media_type=safe_media_type)
        path = self._blob_path(ref.sha256)
        compressed = gzip.compress(safe_content, mtime=0)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                _verify_blob(path.read_bytes(), ref.sha256, ref.size_bytes)
            else:
                fd, temporary_name = tempfile.mkstemp(prefix=".mcp-pal-", suffix=".tmp", dir=str(path.parent))
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(compressed)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            self._paths.add(path)
            # Metadata is published only after the complete compressed blob has
            # been atomically placed and validated.
            self._commit_ref(ref, compressed)
        return ref

    def get(self, artifact: ArtifactRef | ArtifactId | str) -> bytes:
        ref = self._resolve_ref(artifact)
        path = self._blob_path(ref.sha256)
        with self._lock:
            try:
                compressed = path.read_bytes()
            except FileNotFoundError as exc:
                raise ArtifactNotFound("artifact blob not found") from exc
        return _verify_blob(compressed, ref.sha256, ref.size_bytes)

    def delete(self, artifact: ArtifactRef | ArtifactId | str) -> None:
        ref = self._resolve_ref(artifact)
        with self._lock:
            super().delete(ref)
            if not any(item.sha256 == ref.sha256 for item in self._refs.values()):
                path = self._blob_path(ref.sha256)
                path.unlink(missing_ok=True)
                self._paths.discard(path)

    def cleanup(self) -> None:
        with self._lock:
            super().cleanup()
            if self._owned_root:
                shutil.rmtree(self._root, ignore_errors=True)
            else:
                for path in tuple(self._paths):
                    path.unlink(missing_ok=True)
                self._root.mkdir(parents=True, exist_ok=True)
            self._paths.clear()


__all__ = [
    "ArtifactNotFound",
    "ArtifactStore",
    "BlobIntegrityError",
    "EventCallback",
    "ExecutionStore",
    "ExecutionTransaction",
    "InMemoryArtifactStore",
    "InMemoryExecutionStore",
    "StorageConflict",
    "StorageError",
    "TemporaryArtifactStore",
]
