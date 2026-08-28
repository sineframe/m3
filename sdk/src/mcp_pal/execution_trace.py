"""Canonical execution trace recording and immutable projections.

Redaction is applied inside this recorder before an event reaches storage.
The configured redaction policy is fail-closed; original provider values are
never retained by the recorder.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import threading
from time import perf_counter_ns
from typing import Any, Literal, cast
from uuid import uuid4

from .storage import ExecutionStore, StorageConflict
from .trace.redaction import RedactionConfig, redact_for_persistence, redact_model_json
from .types import (
    CanonicalEvent,
    ConnectionId,
    EventId,
    EventKind,
    EventOrigin,
    EventProvenance,
    ExecutionId,
    ExecutionOutcome,
    ExecutionSnapshot,
    LifecycleState,
    LifecyclePhase,
    RawEvidenceRef,
    RequestCorrelation,
    SessionId,
    TraceId,
    TraceResult,
    TurnId,
    TurnLifecycle,
    TurnOutcome,
    TurnSnapshot,
)


class TraceRecorderError(Exception):
    """Base class for safe trace-recorder failures."""


class TraceFinalizationConflict(TraceRecorderError):
    """A terminal execution was finalized again with another outcome."""


_EXECUTION_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.CREATED: frozenset({LifecycleState.QUEUED, LifecycleState.STARTING, LifecycleState.FINISHED}),
    LifecycleState.QUEUED: frozenset({LifecycleState.STARTING, LifecycleState.FINISHED}),
    LifecycleState.STARTING: frozenset({LifecycleState.IDLE, LifecycleState.RUNNING_TURN, LifecycleState.CLOSING, LifecycleState.FINISHED}),
    LifecycleState.IDLE: frozenset({LifecycleState.RUNNING_TURN, LifecycleState.CLOSING, LifecycleState.FINISHED}),
    LifecycleState.RUNNING_TURN: frozenset({LifecycleState.IDLE, LifecycleState.CLOSING, LifecycleState.FINISHED}),
    LifecycleState.CLOSING: frozenset({LifecycleState.FINISHED}),
    LifecycleState.FINISHED: frozenset(),
}

_TURN_TRANSITIONS: dict[TurnLifecycle, frozenset[TurnLifecycle]] = {
    TurnLifecycle.QUEUED: frozenset({TurnLifecycle.RUNNING, TurnLifecycle.FINISHED}),
    TurnLifecycle.RUNNING: frozenset({TurnLifecycle.FINISHED}),
    TurnLifecycle.FINISHED: frozenset(),
}


class ExecutionTraceRecorder:
    """Record committed canonical events and derive immutable projections.

    Construction creates the execution metadata and commits an
    ``execution.created`` event. Both ``emit`` and ``record`` redact payloads
    before the store sees them.
    """

    _ALLOWED_LIMITATIONS = frozenset(
        {"cleanup_failed", "persistence_failed", "capture_incomplete", "partial_trace"}
    )

    def __init__(
        self,
        store: ExecutionStore,
        execution_id: ExecutionId | str,
        *,
        trace_id: TraceId | str | None = None,
        redaction_config: RedactionConfig | None = None,
        specification: Mapping[str, Any] | None = None,
    ) -> None:
        self._store = store
        self._execution_id = execution_id if isinstance(execution_id, ExecutionId) else ExecutionId(str(execution_id))
        self._trace_id = trace_id if isinstance(trace_id, TraceId) else TraceId(
            str(trace_id) if trace_id is not None else f"trace-{uuid4().hex}"
        )
        self._redaction_config = redaction_config if redaction_config is not None else RedactionConfig.from_environment()
        # One recorder lock covers reservation, validation, commit, and
        # terminal projection. Storage remains the commit authority, while
        # this lock prevents this recorder's producers from reserving or
        # attempting to append out of order.
        self._record_lock = threading.RLock()
        self._clock_lock = threading.RLock()
        self._started_monotonic_ns = perf_counter_ns()
        self._last_offset_ms = 0.0
        self._final: TraceResult | None = None
        if store.get_snapshot(self._execution_id) is None:
            store.create(
                ExecutionSnapshot(execution_id=self._execution_id),
                specification=specification,
            )
            self.emit(EventKind.EXECUTION_CREATED, payload={"lifecycle": LifecycleState.CREATED.value})
        else:
            # A persistent execution may be reopened by another process.  Its
            # perf-counter origin is different, so continue from the committed
            # trace offset rather than allowing the next event to move time
            # backwards in the canonical sequence.
            existing_events = self._committed_events()
            if existing_events:
                self._last_offset_ms = max(event.monotonic_offset_ms for event in existing_events)
            existing = self._project_trace()
            if existing.completeness in {"complete", "partial"} and self._is_terminal(existing):
                self._final = existing

    @property
    def execution_id(self) -> ExecutionId:
        return self._execution_id

    @property
    def trace_id(self) -> TraceId:
        return self._trace_id

    def bind_redaction_config(
        self,
        config: RedactionConfig,
        *,
        allow_after_events: bool = False,
    ) -> None:
        """Freeze a redaction policy before provider values are observed."""

        with self._record_lock:
            if not allow_after_events and len(tuple(self._store.iter_events(self._execution_id))) > 1:
                raise TraceRecorderError("redaction policy must be bound before trace capture")
            self._redaction_config = config

    def record(self, event: CanonicalEvent) -> CanonicalEvent:
        """Redact, validate, and commit one canonical event."""
        with self._record_lock:
            if event.execution_id != self._execution_id:
                raise StorageConflict("event belongs to another execution")
            if self._final is not None or self._has_committed_terminal():
                raise TraceFinalizationConflict("execution is already terminal")
            self._validate_event(event)
            safe_event = self._redacted_event(event)
            timestamp, offset = self._clock()
            safe_event = CanonicalEvent.model_validate(
                {
                    **safe_event.model_dump(mode="python"),
                    "timestamp": timestamp,
                    "monotonic_offset_ms": offset,
                }
            )
            self._store.append_events((safe_event,))
            return safe_event

    def emit(
        self,
        kind: EventKind,
        *,
        payload: Mapping[str, Any] | None = None,
        session_id: SessionId | str | None = None,
        turn_id: TurnId | str | None = None,
        server_binding: str | None = None,
        connection_id: ConnectionId | str | None = None,
        correlation: RequestCorrelation | None = None,
        lifecycle_phase: LifecyclePhase = LifecyclePhase.UNKNOWN,
        provenance: EventProvenance | None = None,
        raw_evidence_ref: RawEvidenceRef | None = None,
    ) -> CanonicalEvent:
        """Allocate, build, and commit a canonical event."""
        with self._record_lock:
            if self._final is not None or self._has_committed_terminal():
                raise TraceFinalizationConflict("execution is already terminal")
            safe_payload = redact_for_persistence(
                dict(payload or {}),
                config=self._redaction_config,
                path="$.payload",
            )
            sequence = self._allocate_sequence()
            try:
                event = CanonicalEvent(
                    event_id=EventId(f"event-{uuid4().hex}"),
                    execution_id=self._execution_id,
                    sequence=sequence,
                    kind=kind,
                    session_id=_session_id(session_id),
                    turn_id=_turn_id(turn_id),
                    server_binding=server_binding,
                    connection_id=(
                        connection_id
                        if isinstance(connection_id, ConnectionId)
                        else ConnectionId(connection_id)
                        if connection_id is not None
                        else None
                    ),
                    correlation=correlation,
                    lifecycle_phase=lifecycle_phase,
                    monotonic_offset_ms=0.0,
                    payload=safe_payload,
                    raw_evidence_ref=raw_evidence_ref,
                    provenance=provenance
                    or EventProvenance(origin=EventOrigin.NORMALIZED, source="mcp_pal"),
                )
                return self.record(event)
            except Exception:
                try:
                    self._store.release(self._execution_id, (sequence,))
                except Exception:
                    pass
                raise

    def snapshot(self) -> ExecutionSnapshot:
        """Return a fresh snapshot derived solely from committed events."""
        with self._record_lock:
            return self._project_snapshot()

    def turn_snapshots(self) -> tuple[TurnSnapshot, ...]:
        """Return fresh immutable turn projections ordered by turn number."""
        with self._record_lock:
            return tuple(self._project_turns().values())

    def events(self) -> tuple[CanonicalEvent, ...]:
        with self._record_lock:
            return tuple(self._store.iter_events(self._execution_id))

    def finalize(
        self,
        outcome: ExecutionOutcome,
        *,
        cleanup_succeeded: bool = True,
        persistence_succeeded: bool = True,
        limitations: Sequence[str] = (),
        direct_result: Mapping[str, Any] | None = None,
    ) -> TraceResult:
        """Commit terminal evidence and return an idempotent terminal trace."""
        with self._record_lock:
            existing = self._final or self._terminal_trace()
            if existing is not None:
                self._final = existing
                if outcome.value == self._terminal_outcome(existing):
                    return self._fresh_trace(existing)
                raise TraceFinalizationConflict("execution was finalized with another outcome")
            safe_limitations = self._safe_limitations(
                cleanup_succeeded=cleanup_succeeded,
                persistence_succeeded=persistence_succeeded,
                limitations=limitations,
            )
            terminal_payload: dict[str, Any] = {
                "outcome": outcome.value,
                "completeness": "complete" if cleanup_succeeded and persistence_succeeded and not safe_limitations else "partial",
                "limitations": list(safe_limitations),
            }
            if direct_result is not None:
                terminal_payload["direct_result"] = dict(direct_result)
            terminal_event = self.emit(
                EventKind.EXECUTION_FINISHED,
                payload=terminal_payload,
            )
            completeness: Literal["complete", "partial"] = "complete" if cleanup_succeeded and persistence_succeeded and not safe_limitations else "partial"
            trace = TraceResult(
                trace_id=self._trace_id,
                execution_id=self._execution_id,
                completeness=completeness,
                highest_sequence=terminal_event.sequence,
                events=tuple(self._store.iter_events(self._execution_id)),
                limitations=safe_limitations,
            )
            # The execution store's snapshot is metadata for ownership/lifecycle;
            # this recorder projection is derived solely from committed events.
            self._final = trace
            return self._fresh_trace(trace)

    def _allocate_sequence(self) -> int:
        allocator = getattr(self._store, "allocate_sequence", None)
        if not callable(allocator):
            raise TraceRecorderError("execution store does not support sequence allocation")
        return int(allocator(self._execution_id))

    def _committed_events(self) -> tuple[CanonicalEvent, ...]:
        return tuple(self._store.iter_events(self._execution_id))

    def _has_committed_terminal(self) -> bool:
        return any(event.kind is EventKind.EXECUTION_FINISHED for event in self._committed_events())

    def _terminal_trace(self) -> TraceResult | None:
        if not self._has_committed_terminal():
            return None
        return self._project_trace()

    def _redacted_event(self, event: CanonicalEvent) -> CanonicalEvent:
        # The helper projects the model through python values first, then
        # redacts and validates JSON-compatible output.  Comparing typed
        # fields prevents representation changes from being mistaken for a
        # semantic identity change.
        projected = redact_model_json(event, config=self._redaction_config, path="$.event")
        if not isinstance(projected, Mapping):
            raise TraceRecorderError("event projection is invalid")
        immutable_fields = (
            "schema_id", "schema_version", "event_id", "execution_id", "sequence", "kind",
            "session_id", "turn_id", "server_binding", "connection_id", "correlation",
            "lifecycle_phase", "payload_ref", "raw_evidence_ref", "reasoning",
        )
        try:
            safe_event = CanonicalEvent.model_validate(projected)
        except Exception:
            # Do not retain a validation exception as a cause: its rendered
            # context may include hostile provider values.
            raise TraceRecorderError("event projection is invalid") from None
        if any(getattr(safe_event, field) != getattr(event, field) for field in immutable_fields):
            raise TraceRecorderError("event identity changed during redaction")
        return safe_event

    def _clock(self) -> tuple[datetime, float]:
        with self._clock_lock:
            timestamp = datetime.now(timezone.utc)
            offset = (perf_counter_ns() - self._started_monotonic_ns) / 1_000_000
            self._last_offset_ms = max(self._last_offset_ms, offset)
            return timestamp, self._last_offset_ms

    def _validate_event(self, event: CanonicalEvent) -> None:
        payload = event.payload
        if event.kind is EventKind.EXECUTION_CREATED:
            if ExecutionTraceRecorder._required_lifecycle(payload) is not LifecycleState.CREATED:
                raise TraceRecorderError("execution creation payload is invalid")
            if any(item.kind is EventKind.EXECUTION_CREATED for item in self._committed_events()):
                raise TraceRecorderError("execution already exists")
        elif event.kind is EventKind.EXECUTION_STATE_CHANGED:
            lifecycle = ExecutionTraceRecorder._required_lifecycle(payload)
            current = self._project_snapshot().lifecycle
            if lifecycle is LifecycleState.FINISHED or "outcome" in payload or lifecycle not in _EXECUTION_TRANSITIONS[current]:
                raise TraceRecorderError("execution state payload is invalid")
        elif event.kind is EventKind.EXECUTION_FINISHED:
            outcome = _string(payload.get("outcome"))
            if outcome is None or _enum_or_none(ExecutionOutcome, outcome) is None:
                raise TraceRecorderError("execution terminal payload is invalid")
            if self._project_snapshot().lifecycle is LifecycleState.FINISHED:
                raise TraceRecorderError("execution already finished")
            lifecycle_value = _string(payload.get("lifecycle"))
            if lifecycle_value is not None and lifecycle_value != LifecycleState.FINISHED.value:
                raise TraceRecorderError("execution terminal payload is invalid")
        elif event.kind is EventKind.SESSION_CREATED or event.kind is EventKind.SESSION_STATE_CHANGED:
            if event.session_id is None:
                raise TraceRecorderError("session event requires a session")
            if event.kind is EventKind.SESSION_CREATED and event.session_id in {
                item.session_id
                for item in self._committed_events()
                if item.kind is EventKind.SESSION_CREATED and item.session_id is not None
            }:
                raise TraceRecorderError("session already exists")
        elif event.kind is EventKind.TURN_CREATED:
            if event.session_id is None or event.turn_id is None or _positive_int(payload.get("number"), 0) < 1:
                raise TraceRecorderError("turn creation payload is invalid")
            existing_turns = self._project_turns()
            if event.session_id not in {
                item.session_id
                for item in self._committed_events()
                if item.kind is EventKind.SESSION_CREATED and item.session_id is not None
            }:
                raise TraceRecorderError("session does not exist")
            if event.turn_id in existing_turns or any(
                item.session_id == event.session_id and _positive_int(item.payload.get("number"), 0) == _positive_int(payload.get("number"), 0)
                for item in self._committed_events()
                if item.kind is EventKind.TURN_CREATED
            ):
                raise TraceRecorderError("turn already exists")
        elif event.kind is EventKind.TURN_STATE_CHANGED:
            if event.session_id is None or event.turn_id is None:
                raise TraceRecorderError("turn state payload is invalid")
            current_turn = self._project_turns().get(event.turn_id)
            if current_turn is None or current_turn.session_id != event.session_id:
                raise TraceRecorderError("turn does not exist")
            turn_lifecycle = ExecutionTraceRecorder._required_turn_lifecycle(payload)
            outcome = _string(payload.get("outcome"))
            if turn_lifecycle is TurnLifecycle.FINISHED:
                if outcome is None or _enum_or_none(TurnOutcome, outcome) is None:
                    raise TraceRecorderError("turn terminal payload is invalid")
            elif outcome is not None:
                raise TraceRecorderError("turn state payload is invalid")
            if turn_lifecycle not in _TURN_TRANSITIONS[current_turn.lifecycle]:
                raise TraceRecorderError("turn state transition is invalid")

    @staticmethod
    def _required_lifecycle(payload: Mapping[str, Any]) -> LifecycleState:
        value = _string(payload.get("lifecycle")) or _string(payload.get("state"))
        result = _enum_or_none(LifecycleState, value) if value is not None else None
        if result is None:
            raise TraceRecorderError("execution state payload is invalid")
        return cast(LifecycleState, result)

    @staticmethod
    def _required_turn_lifecycle(payload: Mapping[str, Any]) -> TurnLifecycle:
        value = _string(payload.get("lifecycle")) or _string(payload.get("state"))
        result = _enum_or_none(TurnLifecycle, value) if value is not None else None
        if result is None:
            raise TraceRecorderError("turn state payload is invalid")
        return cast(TurnLifecycle, result)

    def _project_snapshot(self) -> ExecutionSnapshot:
        events = self._committed_events()
        lifecycle = LifecycleState.CREATED
        outcome: ExecutionOutcome | None = None
        created_at = events[0].timestamp if events else datetime.now(timezone.utc)
        finished_at: datetime | None = None
        highest = events[-1].sequence if events else 0
        for event in events:
            if event.kind is EventKind.EXECUTION_CREATED:
                created_at = event.timestamp
            elif event.kind is EventKind.EXECUTION_STATE_CHANGED:
                lifecycle = _lifecycle(event.payload, lifecycle)
            elif event.kind is EventKind.EXECUTION_FINISHED:
                value = _string(event.payload.get("outcome"))
                if value is not None:
                    outcome = _enum_or_none(ExecutionOutcome, value)
                lifecycle = LifecycleState.FINISHED
                finished_at = event.timestamp
        return ExecutionSnapshot(
            execution_id=self._execution_id,
            lifecycle=lifecycle,
            outcome=outcome,
            sequence=highest,
            created_at=created_at,
            finished_at=finished_at,
        )

    def _project_turns(self) -> dict[TurnId, TurnSnapshot]:
        turns: dict[TurnId, TurnSnapshot] = {}
        for event in self._committed_events():
            if event.turn_id is None or event.session_id is None:
                continue
            turn_id = event.turn_id
            if event.kind is EventKind.TURN_CREATED:
                number = _positive_int(event.payload.get("number"), len(turns) + 1)
                turns[turn_id] = TurnSnapshot(
                    turn_id=turn_id,
                    session_id=event.session_id,
                    number=number,
                    created_at=event.timestamp,
                )
            elif event.kind is EventKind.TURN_STATE_CHANGED and turn_id in turns:
                current = turns[turn_id]
                lifecycle = _turn_lifecycle(event.payload, current.lifecycle)
                outcome_value = _string(event.payload.get("outcome"))
                turn_outcome = _enum_or_none(TurnOutcome, outcome_value) if outcome_value is not None else None
                finished_at = event.timestamp if lifecycle is TurnLifecycle.FINISHED else None
                if lifecycle is TurnLifecycle.FINISHED and turn_outcome is None:
                    continue
                turns[turn_id] = TurnSnapshot(
                    turn_id=current.turn_id,
                    session_id=current.session_id,
                    number=current.number,
                    lifecycle=lifecycle,
                    outcome=turn_outcome,
                    created_at=current.created_at,
                    finished_at=finished_at,
                )
        return dict(sorted(turns.items(), key=lambda item: item[1].number))

    def _project_trace(self) -> TraceResult:
        events = self._committed_events()
        outcome = self._project_snapshot().outcome
        terminal = next((event for event in reversed(events) if event.kind is EventKind.EXECUTION_FINISHED), None)
        completeness: Literal["complete", "partial"] = "partial"
        limitations: tuple[str, ...] = ("capture_incomplete",) if outcome is None else ("partial_trace",)
        if terminal is not None:
            completeness_value = _string(terminal.payload.get("completeness"))
            if completeness_value in {"complete", "partial"}:
                completeness = cast(Literal["complete", "partial"], completeness_value)
            raw_limitations = terminal.payload.get("limitations")
            if isinstance(raw_limitations, (list, tuple)):
                limitations = tuple(item for item in raw_limitations if isinstance(item, str))
        return TraceResult(
            trace_id=self._trace_id,
            execution_id=self._execution_id,
            completeness=completeness,
            highest_sequence=events[-1].sequence if events else 0,
            events=events,
            limitations=limitations,
        )

    @staticmethod
    def _safe_limitations(
        *, cleanup_succeeded: bool, persistence_succeeded: bool, limitations: Sequence[str]
    ) -> tuple[str, ...]:
        values = set(item for item in limitations if item in ExecutionTraceRecorder._ALLOWED_LIMITATIONS)
        if not cleanup_succeeded:
            values.add("cleanup_failed")
        if not persistence_succeeded:
            values.add("persistence_failed")
        return tuple(sorted(values))

    @staticmethod
    def _terminal_outcome(trace: TraceResult) -> str | None:
        for event in reversed(trace.events):
            if event.kind is EventKind.EXECUTION_FINISHED:
                return _string(event.payload.get("outcome"))
        return None

    @staticmethod
    def _is_terminal(trace: TraceResult) -> bool:
        return any(event.kind is EventKind.EXECUTION_FINISHED for event in trace.events)

    @staticmethod
    def _fresh_trace(trace: TraceResult) -> TraceResult:
        return trace.model_copy()


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _session_id(value: SessionId | str | None) -> SessionId | None:
    if value is None:
        return None
    return value if isinstance(value, SessionId) else SessionId(str(value))


def _turn_id(value: TurnId | str | None) -> TurnId | None:
    if value is None:
        return None
    return value if isinstance(value, TurnId) else TurnId(str(value))


def _positive_int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


def _enum_or_none(enum_type: Any, value: str) -> Any:
    try:
        return enum_type(value)
    except ValueError:
        return None


def _lifecycle(payload: Mapping[str, Any], default: LifecycleState) -> LifecycleState:
    value = _string(payload.get("lifecycle")) or _string(payload.get("state"))
    result = _enum_or_none(LifecycleState, value) if value is not None else None
    return result if result is not None else default


def _turn_lifecycle(payload: Mapping[str, Any], default: TurnLifecycle) -> TurnLifecycle:
    value = _string(payload.get("lifecycle")) or _string(payload.get("state"))
    result = _enum_or_none(TurnLifecycle, value) if value is not None else None
    return result if result is not None else default


__all__ = [
    "ExecutionTraceRecorder",
    "TraceFinalizationConflict",
    "TraceRecorderError",
]
