"""Stable execution trace recording and immutable projections.

Redaction is applied inside this recorder before an event reaches storage.
The configured redaction policy is fail-closed; original provider values are
never retained by the recorder.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from time import perf_counter_ns
from typing import Any, Literal, cast
from uuid import uuid4

from ._types.agent_identity import project_agent_identity
from .storage import ExecutionStore, StorageConflict
from .trace.counts import tool_call_count
from .trace.redaction import RedactionConfig, redact_for_persistence, redact_model_json
from .types import (
    ConnectionId,
    Event,
    EventId,
    EventKind,
    EventOrigin,
    EventSource,
    EvidenceRef,
    ExecutionId,
    ExecutionOutcome,
    ExecutionState,
    ExecutionStatus,
    LifecyclePhase,
    ProjectId,
    RequestLink,
    RunId,
    SessionId,
    TraceId,
    TraceResult,
    TurnId,
    TurnOutcome,
    TurnState,
    TurnStatus,
)


class TraceRecorderError(Exception):
    """Base class for safe trace-recorder failures."""


class TraceFinalizationConflict(TraceRecorderError):
    """A terminal execution was finalized again with another outcome."""


# Recorder-authored lifecycle events that hold no provider-observed values.
_PRE_CAPTURE_EVENT_KINDS: frozenset[EventKind] = frozenset(
    {EventKind.EXECUTION_CREATED, EventKind.EXECUTION_STATE_CHANGED}
)

_EXECUTION_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.CREATED: frozenset(
        {ExecutionStatus.QUEUED, ExecutionStatus.STARTING, ExecutionStatus.FINISHED}
    ),
    ExecutionStatus.QUEUED: frozenset(
        {ExecutionStatus.STARTING, ExecutionStatus.FINISHED}
    ),
    ExecutionStatus.STARTING: frozenset(
        {
            ExecutionStatus.IDLE,
            ExecutionStatus.RUNNING_TURN,
            ExecutionStatus.CLOSING,
            ExecutionStatus.FINISHED,
        }
    ),
    ExecutionStatus.IDLE: frozenset(
        {
            ExecutionStatus.RUNNING_TURN,
            ExecutionStatus.CLOSING,
            ExecutionStatus.FINISHED,
        }
    ),
    ExecutionStatus.RUNNING_TURN: frozenset(
        {
            ExecutionStatus.IDLE,
            ExecutionStatus.WAITING_FOR_INPUT,
            ExecutionStatus.CLOSING,
            ExecutionStatus.FINISHED,
        }
    ),
    ExecutionStatus.WAITING_FOR_INPUT: frozenset(
        {
            ExecutionStatus.RUNNING_TURN,
            ExecutionStatus.CLOSING,
            ExecutionStatus.FINISHED,
        }
    ),
    ExecutionStatus.CLOSING: frozenset({ExecutionStatus.FINISHED}),
    ExecutionStatus.FINISHED: frozenset(),
}

_TURN_TRANSITIONS: dict[TurnStatus, frozenset[TurnStatus]] = {
    TurnStatus.QUEUED: frozenset({TurnStatus.RUNNING, TurnStatus.FINISHED}),
    TurnStatus.RUNNING: frozenset({TurnStatus.FINISHED}),
    TurnStatus.FINISHED: frozenset(),
}


class ExecutionTraceRecorder:
    """Record committed stable events and derive immutable projections.

    Construction creates the execution metadata and commits an
    ``execution.created`` event. Both ``emit`` and ``record`` redact payloads
    before the store sees them.
    """

    _ALLOWED_LIMITATIONS = frozenset(
        {
            "cleanup_failed",
            "persistence_failed",
            "capture_incomplete",
            "capture_disabled",
            "partial_trace",
        }
    )

    def __init__(
        self,
        store: ExecutionStore,
        execution_id: ExecutionId | str,
        *,
        trace_id: TraceId | str | None = None,
        redaction_config: RedactionConfig | None = None,
        specification: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        suite_name: str | None = None,
        project_id: ProjectId | str | None = None,
        server_bindings: Sequence[Mapping[str, Any]] = (),
        harness_binding: Mapping[str, Any] | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        self._store = store
        self._execution_id = (
            execution_id
            if isinstance(execution_id, ExecutionId)
            else ExecutionId(str(execution_id))
        )
        requested_trace_id = (
            trace_id
            if isinstance(trace_id, TraceId)
            else TraceId(str(trace_id))
            if trace_id is not None
            else None
        )
        self._redaction_config = (
            redaction_config
            if redaction_config is not None
            else RedactionConfig.from_environment()
        )
        # One recorder lock covers reservation, validation, commit, and
        # terminal projection. Storage remains the commit authority, while
        # this lock prevents this recorder's producers from reserving or
        # attempting to append out of order.
        self._record_lock = threading.RLock()
        self._clock_lock = threading.RLock()
        self._started_monotonic_ns = perf_counter_ns()
        self._last_offset_ms = 0.0
        self._final: TraceResult | None = None
        self._runtime_limitations: list[str] = []
        if store.get_snapshot(self._execution_id) is None:
            self._trace_id = requested_trace_id or TraceId(f"trace-{uuid4().hex}")
            suite_name = suite_name or (
                str(specification.get("suite_name"))
                if isinstance(specification, Mapping)
                and specification.get("suite_name")
                else None
            )
            effective_run_id = run_id or (
                str(specification.get("run_id"))
                if isinstance(specification, Mapping) and specification.get("run_id")
                else None
            )
            effective_project_id = (
                project_id.root
                if isinstance(project_id, ProjectId)
                else str(project_id)
                if project_id is not None
                else (
                    str(specification.get("project_id"))
                    if isinstance(specification, Mapping)
                    and specification.get("project_id")
                    else None
                )
            )
            if effective_project_id:
                register_project = getattr(store, "ensure_project", None)
                get_project = getattr(store, "get_project", None)
                if callable(register_project) and (
                    not callable(get_project)
                    or get_project(effective_project_id) is None
                ):
                    project_name = (
                        str(specification.get("project_name"))
                        if isinstance(specification, Mapping)
                        and specification.get("project_name")
                        else effective_project_id
                    )
                    register_project(effective_project_id, project_name)
            store.create(
                ExecutionState(
                    execution_id=self._execution_id,
                    project_id=ProjectId(effective_project_id)
                    if effective_project_id
                    else None,
                    run_id=RunId(effective_run_id) if effective_run_id else None,
                    suite_name=suite_name,
                ),
                specification=specification,
                provenance=provenance,
                server_bindings=server_bindings,
                harness_binding=harness_binding,
                run_id=effective_run_id,
            )
            self.emit(
                EventKind.EXECUTION_CREATED,
                payload={
                    "lifecycle": ExecutionStatus.CREATED.value,
                    "trace_id": self._trace_id.root,
                },
            )
        else:
            # A persistent execution may be reopened by another process.  Its
            # perf-counter origin is different, so continue from the committed
            # trace offset rather than allowing the next event to move time
            # backwards in the stable sequence.
            existing_events = self._committed_events()
            if not existing_events:
                self._trace_id = requested_trace_id or TraceId(f"trace-{uuid4().hex}")
                self.emit(
                    EventKind.EXECUTION_CREATED,
                    payload={
                        "lifecycle": ExecutionStatus.CREATED.value,
                        "trace_id": self._trace_id.root,
                    },
                )
                existing_events = self._committed_events()
            else:
                created_events = [
                    event
                    for event in existing_events
                    if event.kind is EventKind.EXECUTION_CREATED
                ]
                if (
                    existing_events[0].sequence != 0
                    or existing_events[0].kind is not EventKind.EXECUTION_CREATED
                    or len(created_events) != 1
                ):
                    raise TraceRecorderError(
                        "persisted execution.created evidence is malformed"
                    )
                persisted_trace_id = existing_events[0].payload.get("trace_id")
                if not isinstance(persisted_trace_id, str) or not persisted_trace_id:
                    raise TraceRecorderError("persisted trace ID is unavailable")
                try:
                    self._trace_id = TraceId(persisted_trace_id)
                except ValueError:
                    raise TraceRecorderError("persisted trace ID is invalid") from None
                if self._trace_id.root != persisted_trace_id:
                    raise TraceRecorderError("persisted trace ID is not stable")
                if (
                    requested_trace_id is not None
                    and requested_trace_id != self._trace_id
                ):
                    raise TraceRecorderError(
                        "trace ID conflicts with persisted execution"
                    )
                self._runtime_limitations.extend(
                    limitation
                    for limitation in self._limitations_from_events(existing_events)
                    if limitation not in self._runtime_limitations
                )
            if existing_events:
                self._last_offset_ms = max(
                    event.monotonic_offset_ms for event in existing_events
                )
            if self._has_committed_terminal():
                existing = self._project_trace()
                existing.view()
                self._final = existing

    @property
    def execution_id(self) -> ExecutionId:
        return self._execution_id

    @property
    def trace_id(self) -> TraceId:
        return self._trace_id

    def add_limitation(self, limitation: str) -> None:
        """Register capture metadata to be merged into terminal evidence."""
        if limitation not in self._ALLOWED_LIMITATIONS:
            raise TraceRecorderError("execution limitation is invalid")
        with self._record_lock:
            if self._final is not None or self._has_committed_terminal():
                raise TraceFinalizationConflict("execution is already terminal")
            if limitation not in self._runtime_limitations:
                self._runtime_limitations.append(limitation)
                # Runtime capture metadata must survive a recorder reopen
                # before finalization.  A bounded diagnostic is stable
                # evidence, not an in-memory side channel.
                try:
                    self.emit(
                        EventKind.DIAGNOSTIC,
                        payload={
                            "code": "capture_limitation",
                            "limitation": limitation,
                            "message": "capture limitation recorded",
                        },
                        provenance=EventSource(
                            origin=EventOrigin.DERIVED, source="m3.recorder"
                        ),
                    )
                except Exception:
                    # The caller-facing sink remains failure-safe. The
                    # in-memory marker is retained so this limitation still
                    # reaches a same-process terminal trace when possible.
                    pass

    def bind_redaction_config(
        self,
        config: RedactionConfig,
        *,
        allow_after_events: bool = False,
    ) -> None:
        """Freeze a redaction policy before provider values are observed.

        Execution lifecycle events carry only recorder-validated state, so a
        managed execution may bind once it is queued or running. Any other
        committed event may already hold unredacted provider data.
        """

        with self._record_lock:
            if not allow_after_events and any(
                event.kind not in _PRE_CAPTURE_EVENT_KINDS
                or event.connection_id is not None
                for event in self._store.iter_events(self._execution_id)
            ):
                raise TraceRecorderError(
                    "redaction policy must be bound before trace capture"
                )
            self._redaction_config = config

    def record(self, event: Event) -> Event:
        """Redact, validate, and commit one stable event."""
        with self._record_lock:
            if event.execution_id != self._execution_id:
                raise StorageConflict("event belongs to another execution")
            if self._final is not None or self._has_committed_terminal():
                raise TraceFinalizationConflict("execution is already terminal")
            self._validate_event(event)
            safe_event = self._redacted_event(event)
            timestamp, offset = self._clock()
            safe_event = Event.model_validate(
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
        correlation: RequestLink | None = None,
        lifecycle_phase: LifecyclePhase = LifecyclePhase.UNKNOWN,
        provenance: EventSource | None = None,
        raw_evidence_ref: EvidenceRef | None = None,
        reasoning: Any | None = None,
        raw_evidence_content: bytes | None = None,
        raw_evidence_media_type: str | None = None,
    ) -> Event:
        """Allocate, build, and commit a stable event."""
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
                event_id = EventId(f"event-{uuid4().hex}")
                event = Event(
                    event_id=event_id,
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
                    reasoning=reasoning,
                    raw_evidence_ref=raw_evidence_ref,
                    provenance=provenance
                    or EventSource(origin=EventOrigin.NORMALIZED, source="m3"),
                )
                if raw_evidence_content is not None:
                    if raw_evidence_media_type is None:
                        raise TraceRecorderError("raw evidence media type is required")
                    self._validate_event(event)
                    timestamp, offset = self._clock()
                    event = event.model_copy(
                        update={"timestamp": timestamp, "monotonic_offset_ms": offset}
                    )
                    append_atomic = getattr(self._store, "append_event", None)
                    if not callable(append_atomic):
                        raise TraceRecorderError(
                            "execution store does not support atomic raw evidence"
                        )
                    return cast(
                        Event,
                        append_atomic(
                            event,
                            raw_evidence_content,
                            media_type=raw_evidence_media_type,
                        ),
                    )
                return self.record(event)
            except Exception:
                try:
                    self._store.release(self._execution_id, (sequence,))
                except Exception:
                    pass
                raise

    def snapshot(self) -> ExecutionState:
        """Return a fresh snapshot derived solely from committed events."""
        with self._record_lock:
            return self._project_snapshot()

    def turn_snapshots(self) -> tuple[TurnState, ...]:
        """Return fresh immutable turn projections ordered by turn number."""
        with self._record_lock:
            return tuple(self._project_turns().values())

    def events(self) -> tuple[Event, ...]:
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
        error: Mapping[str, Any] | None = None,
    ) -> TraceResult:
        """Commit terminal evidence and return an idempotent terminal trace."""
        with self._record_lock:
            existing = self._final or self._terminal_trace()
            if existing is not None:
                self._final = existing
                if outcome.value == self._terminal_outcome(existing):
                    return self._fresh_trace(existing)
                raise TraceFinalizationConflict(
                    "execution was finalized with another outcome"
                )
            safe_limitations = self._safe_limitations(
                cleanup_succeeded=cleanup_succeeded,
                persistence_succeeded=persistence_succeeded,
                limitations=tuple(limitations) + tuple(self._runtime_limitations),
            )
            terminal_payload: dict[str, Any] = {
                "outcome": outcome.value,
                "completeness": "complete"
                if cleanup_succeeded and persistence_succeeded and not safe_limitations
                else "partial",
                "limitations": list(safe_limitations),
            }
            if direct_result is not None:
                terminal_payload["direct_result"] = dict(direct_result)
            if error is not None:
                terminal_payload["error"] = dict(error)
            terminal_event = self.emit(
                EventKind.EXECUTION_FINISHED,
                payload=terminal_payload,
            )
            completeness: Literal["complete", "partial"] = (
                "complete"
                if cleanup_succeeded and persistence_succeeded and not safe_limitations
                else "partial"
            )
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
            raise TraceRecorderError(
                "execution store does not support sequence allocation"
            )
        return int(allocator(self._execution_id))

    def _committed_events(self) -> tuple[Event, ...]:
        return tuple(self._store.iter_events(self._execution_id))

    def _has_committed_terminal(self) -> bool:
        return any(
            event.kind is EventKind.EXECUTION_FINISHED
            for event in self._committed_events()
        )

    def _terminal_trace(self) -> TraceResult | None:
        if not self._has_committed_terminal():
            return None
        return self._project_trace()

    def _redacted_event(self, event: Event) -> Event:
        # The helper projects the model through python values first, then
        # redacts and validates JSON-compatible output.  Comparing typed
        # fields prevents representation changes from being mistaken for a
        # semantic identity change.
        projected = redact_model_json(
            event, config=self._redaction_config, path="$.event"
        )
        if not isinstance(projected, Mapping):
            raise TraceRecorderError("event projection is invalid")
        immutable_fields = (
            "schema_id",
            "schema_version",
            "event_id",
            "execution_id",
            "sequence",
            "kind",
            "session_id",
            "turn_id",
            "server_binding",
            "connection_id",
            "correlation",
            "lifecycle_phase",
            "payload_ref",
            "raw_evidence_ref",
            "reasoning",
        )
        try:
            safe_event = Event.model_validate(projected)
        except Exception:
            # Do not retain a validation exception as a cause: its rendered
            # context may include hostile provider values.
            raise TraceRecorderError("event projection is invalid") from None
        if any(
            getattr(safe_event, field) != getattr(event, field)
            for field in immutable_fields
        ):
            raise TraceRecorderError("event identity changed during redaction")
        return safe_event

    def _clock(self) -> tuple[datetime, float]:
        with self._clock_lock:
            timestamp = datetime.now(timezone.utc)
            offset = (perf_counter_ns() - self._started_monotonic_ns) / 1_000_000
            self._last_offset_ms = max(self._last_offset_ms, offset)
            return timestamp, self._last_offset_ms

    def _validate_event(self, event: Event) -> None:
        payload = event.payload
        if event.kind is EventKind.EXECUTION_CREATED:
            if (
                ExecutionTraceRecorder._required_lifecycle(payload)
                is not ExecutionStatus.CREATED
            ):
                raise TraceRecorderError("execution creation payload is invalid")
            if any(
                item.kind is EventKind.EXECUTION_CREATED
                for item in self._committed_events()
            ):
                raise TraceRecorderError("execution already exists")
        elif event.kind is EventKind.EXECUTION_STATE_CHANGED:
            lifecycle = ExecutionTraceRecorder._required_lifecycle(payload)
            current = self._project_snapshot().lifecycle
            if (
                lifecycle is ExecutionStatus.FINISHED
                or "outcome" in payload
                or lifecycle not in _EXECUTION_TRANSITIONS[current]
            ):
                raise TraceRecorderError("execution state payload is invalid")
        elif event.kind is EventKind.EXECUTION_FINISHED:
            outcome = _string(payload.get("outcome"))
            if outcome is None or _enum_or_none(ExecutionOutcome, outcome) is None:
                raise TraceRecorderError("execution terminal payload is invalid")
            if self._project_snapshot().lifecycle is ExecutionStatus.FINISHED:
                raise TraceRecorderError("execution already finished")
            lifecycle_value = _string(payload.get("lifecycle"))
            if (
                lifecycle_value is not None
                and lifecycle_value != ExecutionStatus.FINISHED.value
            ):
                raise TraceRecorderError("execution terminal payload is invalid")
        elif (
            event.kind is EventKind.SESSION_CREATED
            or event.kind is EventKind.SESSION_STATE_CHANGED
        ):
            if event.session_id is None:
                raise TraceRecorderError("session event requires a session")
            if event.kind is EventKind.SESSION_CREATED and event.session_id in {
                item.session_id
                for item in self._committed_events()
                if item.kind is EventKind.SESSION_CREATED
                and item.session_id is not None
            }:
                raise TraceRecorderError("session already exists")
        elif event.kind is EventKind.TURN_CREATED:
            if (
                event.session_id is None
                or event.turn_id is None
                or _positive_int(payload.get("number"), 0) < 1
            ):
                raise TraceRecorderError("turn creation payload is invalid")
            existing_turns = self._project_turns()
            if event.session_id not in {
                item.session_id
                for item in self._committed_events()
                if item.kind is EventKind.SESSION_CREATED
                and item.session_id is not None
            }:
                raise TraceRecorderError("session does not exist")
            if event.turn_id in existing_turns or any(
                item.session_id == event.session_id
                and _positive_int(item.payload.get("number"), 0)
                == _positive_int(payload.get("number"), 0)
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
            if turn_lifecycle is TurnStatus.FINISHED:
                if outcome is None or _enum_or_none(TurnOutcome, outcome) is None:
                    raise TraceRecorderError("turn terminal payload is invalid")
            elif outcome is not None:
                raise TraceRecorderError("turn state payload is invalid")
            if turn_lifecycle not in _TURN_TRANSITIONS[current_turn.lifecycle]:
                raise TraceRecorderError("turn state transition is invalid")

    @staticmethod
    def _required_lifecycle(payload: Mapping[str, Any]) -> ExecutionStatus:
        value = _string(payload.get("lifecycle")) or _string(payload.get("state"))
        result = _enum_or_none(ExecutionStatus, value) if value is not None else None
        if result is None:
            raise TraceRecorderError("execution state payload is invalid")
        return cast(ExecutionStatus, result)

    @staticmethod
    def _required_turn_lifecycle(payload: Mapping[str, Any]) -> TurnStatus:
        value = _string(payload.get("lifecycle")) or _string(payload.get("state"))
        result = _enum_or_none(TurnStatus, value) if value is not None else None
        if result is None:
            raise TraceRecorderError("turn state payload is invalid")
        return cast(TurnStatus, result)

    def _project_snapshot(self) -> ExecutionState:
        events = self._committed_events()
        saved = self._store.get_snapshot(self._execution_id)
        lifecycle = ExecutionStatus.CREATED
        outcome: ExecutionOutcome | None = None
        created_at = events[0].timestamp if events else datetime.now(timezone.utc)
        finished_at: datetime | None = None
        highest = events[-1].sequence if events else 0
        # Stores maintain this derived field at commit time. Reuse it here so
        # snapshot reads do not repeatedly project the full event history.
        tool_call_total = (
            saved.tool_call_count if saved is not None else tool_call_count(events)
        )
        for event in events:
            if event.kind is EventKind.EXECUTION_CREATED:
                created_at = event.timestamp
            elif event.kind is EventKind.EXECUTION_STATE_CHANGED:
                lifecycle = _lifecycle(event.payload, lifecycle)
            elif event.kind is EventKind.EXECUTION_FINISHED:
                value = _string(event.payload.get("outcome"))
                if value is not None:
                    outcome = _enum_or_none(ExecutionOutcome, value)
                lifecycle = ExecutionStatus.FINISHED
                finished_at = event.timestamp
        return ExecutionState(
            execution_id=self._execution_id,
            project_id=saved.project_id if saved is not None else None,
            suite_id=saved.suite_id if saved is not None else None,
            suite_name=saved.suite_name if saved is not None else None,
            lifecycle=lifecycle,
            outcome=outcome,
            sequence=highest,
            tool_call_count=tool_call_total,
            created_at=created_at,
            finished_at=finished_at,
            agent=project_agent_identity(
                events, saved.agent if saved is not None else None
            ),
        )

    def _project_turns(self) -> dict[TurnId, TurnState]:
        turns: dict[TurnId, TurnState] = {}
        for event in self._committed_events():
            if event.turn_id is None or event.session_id is None:
                continue
            turn_id = event.turn_id
            if event.kind is EventKind.TURN_CREATED:
                number = _positive_int(event.payload.get("number"), len(turns) + 1)
                turns[turn_id] = TurnState(
                    turn_id=turn_id,
                    session_id=event.session_id,
                    number=number,
                    created_at=event.timestamp,
                )
            elif event.kind is EventKind.TURN_STATE_CHANGED and turn_id in turns:
                current = turns[turn_id]
                lifecycle = _turn_lifecycle(event.payload, current.lifecycle)
                outcome_value = _string(event.payload.get("outcome"))
                turn_outcome = (
                    _enum_or_none(TurnOutcome, outcome_value)
                    if outcome_value is not None
                    else None
                )
                finished_at = (
                    event.timestamp if lifecycle is TurnStatus.FINISHED else None
                )
                if lifecycle is TurnStatus.FINISHED and turn_outcome is None:
                    continue
                turns[turn_id] = TurnState(
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
        if not events:
            raise TraceRecorderError("execution has no committed trace evidence")
        created = [
            event for event in events if event.kind is EventKind.EXECUTION_CREATED
        ]
        if (
            events[0].sequence != 0
            or events[0].kind is not EventKind.EXECUTION_CREATED
            or len(created) != 1
            or events[0].payload.get("trace_id") != self._trace_id.root
        ):
            raise TraceRecorderError("execution creation evidence is malformed")
        terminals = [
            event for event in events if event.kind is EventKind.EXECUTION_FINISHED
        ]
        if len(terminals) != 1 or terminals[0] is not events[-1]:
            raise TraceRecorderError("execution terminal evidence is malformed")
        terminal = terminals[0]
        outcome_value = _string(terminal.payload.get("outcome"))
        outcome = (
            _enum_or_none(ExecutionOutcome, outcome_value)
            if outcome_value is not None
            else None
        )
        completeness_value = _string(terminal.payload.get("completeness"))
        raw_limitations = terminal.payload.get("limitations")
        if (
            outcome is None
            or completeness_value not in {"complete", "partial"}
            or not isinstance(raw_limitations, (list, tuple))
            or any(
                not isinstance(item, str) or not item.strip()
                for item in raw_limitations
            )
        ):
            raise TraceRecorderError("execution terminal evidence is malformed")
        snapshot = self._project_snapshot()
        if snapshot.outcome is not outcome:
            raise TraceRecorderError(
                "execution terminal outcome conflicts with snapshot"
            )
        completeness = cast(Literal["complete", "partial"], completeness_value)
        limitations = tuple(raw_limitations)
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
        *,
        cleanup_succeeded: bool,
        persistence_succeeded: bool,
        limitations: Sequence[str],
    ) -> tuple[str, ...]:
        values: list[str] = []
        for item in limitations:
            if (
                not isinstance(item, str)
                or not item.strip()
                or item not in ExecutionTraceRecorder._ALLOWED_LIMITATIONS
            ):
                raise TraceRecorderError("execution limitation is invalid")
            if item not in values:
                values.append(item)
        if not cleanup_succeeded and "cleanup_failed" not in values:
            values.append("cleanup_failed")
        if not persistence_succeeded and "persistence_failed" not in values:
            values.append("persistence_failed")
        return tuple(values)

    @classmethod
    def _limitations_from_events(cls, events: Sequence[Event]) -> tuple[str, ...]:
        values: list[str] = []
        for event in events:
            if event.kind is not EventKind.DIAGNOSTIC:
                continue
            if event.payload.get("code") != "capture_limitation":
                continue
            limitation = event.payload.get("limitation")
            if limitation in cls._ALLOWED_LIMITATIONS and limitation not in values:
                values.append(cast(str, limitation))
        return tuple(values)

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


def _lifecycle(payload: Mapping[str, Any], default: ExecutionStatus) -> ExecutionStatus:
    value = _string(payload.get("lifecycle")) or _string(payload.get("state"))
    result = _enum_or_none(ExecutionStatus, value) if value is not None else None
    return result if result is not None else default


def _turn_lifecycle(payload: Mapping[str, Any], default: TurnStatus) -> TurnStatus:
    value = _string(payload.get("lifecycle")) or _string(payload.get("state"))
    result = _enum_or_none(TurnStatus, value) if value is not None else None
    return result if result is not None else default


__all__ = [
    "ExecutionTraceRecorder",
    "TraceFinalizationConflict",
    "TraceRecorderError",
]
