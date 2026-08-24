"""Canonical event factories and the public event contract re-export.

The value models live in :mod:`mcp_pal.types`, the single public model
boundary. This module owns construction helpers only; in particular,
``mcp_pal.events.CanonicalEvent is mcp_pal.types.CanonicalEvent``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

from .types import (
    CanonicalEvent,
    ConnectionId,
    EventDirection,
    EventId,
    EventKind,
    EventOrigin,
    EventPayloadRef,
    EventProvenance,
    EVENT_SCHEMA_ID,
    EVENT_SCHEMA_VERSION,
    ExecutionId,
    LifecyclePhase,
    RawEvidenceRef,
    ReasoningState,
    ReasoningVisibility,
    RequestCorrelation,
    SessionId,
    TurnId,
)


CanonicalEventEnvelope = CanonicalEvent


class PerExecutionSequenceAllocator:
    """Thread-safe monotonically increasing allocator for one execution."""

    def __init__(self, *, start: int = 0) -> None:
        if start < 0:
            raise ValueError("sequence start must be non-negative")
        self._next_sequence = start
        self._lock = Lock()

    def next(self) -> int:
        with self._lock:
            sequence = self._next_sequence
            self._next_sequence += 1
            return sequence

    def rollback(self, sequence: int) -> None:
        """Return the most recently reserved sequence after failed construction."""

        with self._lock:
            if self._next_sequence != sequence + 1:
                raise RuntimeError("cannot roll back a sequence that is no longer reserved")
            self._next_sequence = sequence

    def peek(self) -> int:
        with self._lock:
            return self._next_sequence


class PerConnectionRequestSequenceAllocator:
    """Thread-safe request sequence counters scoped per connection."""

    def __init__(self, *, start: int = 1) -> None:
        if start < 1:
            raise ValueError("request sequence start must be positive")
        self._next_by_connection: dict[str, int] = {}
        self._start = start
        self._lock = Lock()

    def next(self, connection_id: ConnectionId | str) -> int:
        key = connection_id.root if isinstance(connection_id, ConnectionId) else connection_id
        with self._lock:
            sequence = self._next_by_connection.get(key, self._start)
            self._next_by_connection[key] = sequence + 1
            return sequence

    def rollback(self, connection_id: ConnectionId | str, sequence: int) -> None:
        """Return the most recently reserved request sequence after failure."""

        key = connection_id.root if isinstance(connection_id, ConnectionId) else connection_id
        with self._lock:
            if self._next_by_connection.get(key) != sequence + 1:
                raise RuntimeError("cannot roll back a request sequence that is no longer reserved")
            if sequence == self._start:
                self._next_by_connection.pop(key)
            else:
                self._next_by_connection[key] = sequence


class EventFactory:
    """Concurrency-safe event constructor bound to one execution."""

    def __init__(
        self,
        execution_id: ExecutionId | str,
        *,
        allocator: PerExecutionSequenceAllocator | None = None,
        request_allocator: PerConnectionRequestSequenceAllocator | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock_ns: Callable[[], int] | None = None,
        source: str = "mcp_pal",
    ) -> None:
        self.execution_id = execution_id if isinstance(execution_id, ExecutionId) else ExecutionId(execution_id)
        self._allocator = allocator or PerExecutionSequenceAllocator()
        self._request_allocator = request_allocator or PerConnectionRequestSequenceAllocator()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_clock_ns = monotonic_clock_ns or time.perf_counter_ns
        self._baseline_ns = self._monotonic_clock_ns()
        self._source = source
        self._lock = Lock()

    @property
    def next_sequence(self) -> int:
        return self._allocator.peek()

    def next_request_sequence(self, connection_id: ConnectionId | str) -> int:
        return self._request_allocator.next(connection_id)

    def create(
        self,
        kind: EventKind,
        *,
        event_id: EventId | str | None = None,
        timestamp: datetime | None = None,
        monotonic_offset_ms: float | None = None,
        session_id: SessionId | str | None = None,
        turn_id: TurnId | str | None = None,
        server_binding: str | None = None,
        connection_id: ConnectionId | str | None = None,
        correlation: RequestCorrelation | None = None,
        lifecycle_phase: LifecyclePhase = LifecyclePhase.UNKNOWN,
        payload: Mapping[str, Any] | None = None,
        payload_ref: EventPayloadRef | None = None,
        provenance: EventProvenance | None = None,
        raw_evidence_ref: RawEvidenceRef | None = None,
        reasoning: ReasoningState | None = None,
    ) -> CanonicalEvent:
        """Allocate a sequence and construct one immutable event atomically."""

        with self._lock:
            sequence = self._allocator.next()
            allocated_request: tuple[ConnectionId, int] | None = None
            try:
                now = timestamp or self._clock()
                offset = monotonic_offset_ms
                if offset is None:
                    offset = (self._monotonic_clock_ns() - self._baseline_ns) / 1_000_000
                normalized_event_id = event_id if isinstance(event_id, EventId) else EventId(event_id) if event_id is not None else EventId(str(uuid4()))
                normalized_session_id = session_id if isinstance(session_id, SessionId) else SessionId(session_id) if session_id is not None else None
                normalized_turn_id = turn_id if isinstance(turn_id, TurnId) else TurnId(turn_id) if turn_id is not None else None
                normalized_connection_id = connection_id if isinstance(connection_id, ConnectionId) else ConnectionId(connection_id) if connection_id is not None else None
                if (
                    correlation is not None
                    and correlation.request_sequence is None
                    and normalized_connection_id is not None
                    and correlation.direction in {EventDirection.CLIENT_TO_SERVER, EventDirection.SDK_TO_HARNESS}
                ):
                    allocated_connection = normalized_connection_id
                    allocated_sequence = self._request_allocator.next(allocated_connection)
                    allocated_request = (allocated_connection, allocated_sequence)
                    correlation = RequestCorrelation(
                        jsonrpc_id=correlation.jsonrpc_id,
                        direction=correlation.direction,
                        request_sequence=allocated_sequence,
                    )
                return CanonicalEvent(
                    event_id=normalized_event_id,
                    execution_id=self.execution_id,
                    sequence=sequence,
                    kind=kind,
                    timestamp=now,
                    monotonic_offset_ms=offset,
                    session_id=normalized_session_id,
                    turn_id=normalized_turn_id,
                    server_binding=server_binding,
                    connection_id=normalized_connection_id,
                    correlation=correlation,
                    lifecycle_phase=lifecycle_phase,
                    payload=payload or {},
                    payload_ref=payload_ref,
                    provenance=provenance or EventProvenance(origin=EventOrigin.NORMALIZED, source=self._source),
                    raw_evidence_ref=raw_evidence_ref,
                    reasoning=reasoning,
                )
            except Exception:
                self._allocator.rollback(sequence)
                if allocated_request is not None:
                    self._request_allocator.rollback(*allocated_request)
                raise


__all__ = [
    "EVENT_SCHEMA_ID",
    "EVENT_SCHEMA_VERSION",
    "CanonicalEvent",
    "CanonicalEventEnvelope",
    "ConnectionId",
    "EventDirection",
    "EventFactory",
    "EventId",
    "EventKind",
    "EventOrigin",
    "EventPayloadRef",
    "EventProvenance",
    "ExecutionId",
    "LifecyclePhase",
    "PerConnectionRequestSequenceAllocator",
    "PerExecutionSequenceAllocator",
    "RawEvidenceRef",
    "ReasoningState",
    "ReasoningVisibility",
    "RequestCorrelation",
]
