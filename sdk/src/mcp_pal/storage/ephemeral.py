"""Ephemeral execution and artifact storage for the SDK.

The stores in this module deliberately have no knowledge of the application
database.  They are small, deterministic implementations of the contracts
used by an execution handle: metadata and events are committed atomically,
and an event is not observable until its append transaction has committed.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
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
from typing import Any, Literal, Protocol, TypeAlias, cast

from pydantic import TypeAdapter, ValidationError

from ..aggregations import EvaluationQuery, EvaluationReport, aggregate_evaluations
from ..errors import RawEvidenceUnavailable, TraceNotFinalized, TraceUnavailable
from ..observability import (
    CaptureOptions,
    EvidenceCapture,
    RawEvidence,
    TraceView,
)
from ..services.acp_probes import ACPProbeDimension, ACPProbeResult
from ..trace.redaction import (
    RedactionConfig,
    redact_artifact_bytes,
    redact_for_persistence,
    redact_model_json,
)
from ..types import (
    ArtifactId,
    ArtifactRef,
    DirectResult,
    ErrorCode,
    ErrorInfo,
    EvaluationId,
    EvaluationRecord,
    EvaluationResult,
    EvaluationStatus,
    Event,
    EventId,
    EventKind,
    EvidenceRef,
    ExecutionEvidence,
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionReport,
    ExecutionSpec,
    ExecutionState,
    ExecutionStatus,
    RevisionSelection,
    RunId,
    TraceId,
    TraceResult,
    TurnId,
    TurnResult,
    TurnState,
)
from .evidence import (
    evidence_id_for as _evidence_id_for,
)
from .evidence import (
    make_capture as _make_evidence_capture,
)
from .evidence import (
    make_ref as _make_evidence_ref,
)
from .evidence import (
    make_result as _make_evidence_result,
)
from .evidence import (
    prepare_evidence as _prepare_evidence,
)
from .evidence import (
    validate_evidence_id as _validate_evidence_id,
)
from .evidence import (
    verify_reference as _verify_evidence_reference,
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


EventCallback: TypeAlias = Callable[[Event], None]


class ProfileResolver(Protocol):
    """Read-only saved-profile lookup used by execution runtimes."""

    def resolve_profile(
        self,
        profile_id: str,
        selection: RevisionSelection,
        *,
        kind: Literal["server", "harness"],
    ) -> tuple[Any, Any]: ...


class ExecutionTransaction(Protocol):
    """Uncommitted event batch used by :class:`ExecutionStore`."""

    def append(self, events: Sequence[Event]) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __enter__(self) -> ExecutionTransaction: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class ExecutionStore(Protocol):
    """Store contract for immutable execution snapshots and event streams."""

    def create(
        self,
        snapshot: ExecutionState,
        *,
        specification: Mapping[str, object] | None = None,
        provenance: Mapping[str, object] | None = None,
        server_bindings: Sequence[Mapping[str, object]] = (),
        harness_binding: Mapping[str, object] | None = None,
        parent_execution_id: ExecutionId | str | None = None,
        run_id: RunId | str | None = None,
    ) -> None: ...

    def get_snapshot(
        self, execution_id: ExecutionId | str
    ) -> ExecutionState | None: ...

    def get_execution_spec(
        self, execution_id: ExecutionId | str
    ) -> ExecutionSpec | None: ...

    def list_executions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        lifecycle: ExecutionStatus | str | None = None,
        outcome: ExecutionOutcome | str | None = None,
        run_id: RunId | str | None = None,
    ) -> ExecutionPage: ...

    def get_report(
        self,
        execution_id: ExecutionId | str,
        *,
        after_sequence: int = -1,
        event_limit: int | None = None,
        artifact_limit: int | None = None,
    ) -> ExecutionReport | None: ...

    def get_trace(self, execution_id: ExecutionId | str) -> TraceResult | None: ...

    def get_trace_view(self, execution_id: ExecutionId | str) -> TraceView | None: ...

    def save_evaluation(
        self,
        execution_id: ExecutionId | str,
        result: EvaluationResult | Mapping[str, object],
        *,
        evaluation_id: str | None = None,
        turn_id: TurnId | str | None = None,
    ) -> str: ...

    def evaluations(
        self, execution_id: ExecutionId | str, *, turn_id: TurnId | str | None = None
    ) -> tuple[EvaluationRecord, ...]: ...

    def aggregate_evaluations(self, query: EvaluationQuery) -> EvaluationReport: ...

    def turns(
        self, execution_id: ExecutionId | str
    ) -> tuple[tuple[TurnState, TurnResult | None], ...]: ...

    def save_snapshot(self, snapshot: ExecutionState) -> None: ...

    def append_events(self, events: Sequence[Event]) -> None: ...

    def append_event(
        self, event: Event, content: bytes, *, media_type: str
    ) -> Event: ...

    def iter_events(
        self, execution_id: ExecutionId | str, *, after_sequence: int = -1
    ) -> Iterator[Event]: ...

    def transaction(self, execution_id: ExecutionId | str) -> ExecutionTransaction: ...

    def release(
        self, execution_id: ExecutionId | str, sequences: Sequence[int]
    ) -> None: ...

    def subscribe(
        self, execution_id: ExecutionId | str, callback: EventCallback
    ) -> Callable[[], None]: ...

    def put_raw_evidence(
        self, event_id: EventId | str, content: bytes, *, media_type: str
    ) -> EvidenceCapture: ...

    def read_raw_evidence(
        self, reference: EvidenceRef, *, max_bytes: int = 1_048_576
    ) -> RawEvidence: ...

    def delete_execution(self, execution_id: ExecutionId | str) -> None: ...


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

    def iter_refs(
        self, execution_id: ExecutionId | str | None = None
    ) -> Iterator[ArtifactRef]: ...

    def delete(self, artifact: ArtifactRef | ArtifactId | str) -> None: ...

    def cleanup(self) -> None: ...


def _execution_key(value: ExecutionId | str) -> str:
    return str(value.root if isinstance(value, ExecutionId) else value)


_DIRECT_RESULT_ADAPTER: TypeAdapter[DirectResult] = TypeAdapter(DirectResult)
_ERROR_INFO_ADAPTER: TypeAdapter[ErrorInfo] = TypeAdapter(ErrorInfo)
_EXECUTION_SPEC_ADAPTER: TypeAdapter[ExecutionSpec] = TypeAdapter(ExecutionSpec)


def _copy_execution_spec(specification: ExecutionSpec) -> ExecutionSpec:
    """Re-validate JSON data to obtain a defensive copy.

    ``FrozenModel`` uses a lightweight immutable mapping implementation that
    Python's ``deepcopy`` cannot reconstruct.  Round-tripping the serialized
    representation is both defensive and the same representation used by the
    durable store.
    """

    return _EXECUTION_SPEC_ADAPTER.validate_python(
        specification.model_dump(mode="json")
    )


def _report_fields(
    events: Sequence[Event],
) -> tuple[DirectResult | None, ErrorInfo | None, ExecutionEvidence | None]:
    """Reconstruct only typed values explicitly committed in terminal events."""
    terminal = next(
        (
            event
            for event in reversed(events)
            if event.kind is EventKind.EXECUTION_FINISHED
        ),
        None,
    )
    if terminal is None:
        return None, None, None
    payload = terminal.payload
    direct_result = None
    raw_direct = payload.get("direct_result")
    if isinstance(raw_direct, Mapping):
        try:
            direct_result = _DIRECT_RESULT_ADAPTER.validate_python(raw_direct)
        except ValidationError:
            direct_result = None
    error = None
    raw_error = payload.get("error")
    if isinstance(raw_error, Mapping):
        try:
            error = _ERROR_INFO_ADAPTER.validate_python(raw_error)
        except ValidationError:
            error = None
    elif payload.get("outcome") == ExecutionOutcome.CANCELLED.value:
        error = ErrorInfo(code=ErrorCode.CANCELLED, message="execution cancelled")
    limitations = payload.get("limitations")
    completeness = payload.get("completeness")
    evidence = None
    if isinstance(completeness, str) and isinstance(limitations, (list, tuple)):
        try:
            evidence = ExecutionEvidence(
                completeness=cast(Literal["complete", "partial"], completeness),
                limitations=tuple(
                    item for item in limitations if isinstance(item, str)
                ),
                reason=payload.get("reason")
                if isinstance(payload.get("reason"), str)
                else None,
            )
        except ValueError:
            evidence = None
    return direct_result, error, evidence


def _artifact_key(value: ArtifactId | str) -> str:
    return str(value.root if isinstance(value, ArtifactId) else value)


class _ExecutionBatch(AbstractContextManager["_ExecutionBatch"]):
    def __init__(self, store: InMemoryExecutionStore, execution_id: str) -> None:
        self._store = store
        self._execution_id = execution_id
        self._events: list[Event] = []
        self._done = False

    def append(self, events: Sequence[Event]) -> None:
        if self._done:
            raise StorageConflict("transaction is already closed")
        for event in events:
            if _execution_key(event.execution_id) != self._execution_id:
                raise StorageConflict(
                    "all events in a transaction must belong to its execution"
                )
        self._events.extend(events)

    def commit(self) -> None:
        if self._done:
            return
        self._store._commit(self._execution_id, tuple(self._events))
        self._done = True

    def rollback(self) -> None:
        self._events.clear()
        self._done = True

    def __enter__(self) -> _ExecutionBatch:
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

    def __init__(
        self,
        *,
        config: RedactionConfig | None = None,
        capture_config: CaptureOptions | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._snapshots: dict[str, ExecutionState] = {}
        self._specifications: dict[str, ExecutionSpec] = {}
        self._events: dict[str, tuple[Event, ...]] = {}
        self._callbacks: dict[str, list[EventCallback]] = {}
        self._reserved_sequences: dict[str, set[int]] = {}
        self._redaction_config = (
            config if config is not None else RedactionConfig.from_environment()
        )
        self._capture_config = (
            capture_config if capture_config is not None else CaptureOptions()
        )
        self._raw_refs: dict[str, EvidenceRef] = {}
        self._raw_event_ids: dict[str, str] = {}
        self._raw_blobs: dict[str, bytes] = {}
        self._raw_refcounts: dict[str, int] = {}
        self._acp_probes: dict[str, ACPProbeResult] = {}
        self._evaluations: dict[str, list[EvaluationRecord]] = {}
        self._turns: dict[str, list[tuple[TurnState, TurnResult | None]]] = {}
        self._test_runs: dict[str, dict[str, Any]] = {}
        self._test_results: dict[str, dict[str, dict[str, Any]]] = {}

    # ACP probe persistence intentionally lives beside execution persistence,
    # but is kept as a small independent collection so tests and applications
    # can use the exact same service contract without SQLAlchemy/legacy rows.
    def save_acp_probe(self, result: ACPProbeResult) -> ACPProbeResult:
        from ..services.acp_probes import redact_probe

        safe = redact_probe(result, self._redaction_config)
        with self._lock:
            existing = self._acp_probes.get(safe.id)
            if existing is not None and (
                existing.stable_key != safe.stable_key
                or existing.created_at != safe.created_at
            ):
                raise StorageConflict(
                    "ACP probe id was already used for another dimension"
                )
            self._acp_probes[safe.id] = ACPProbeResult.model_validate(
                safe.model_dump(mode="json")
            )
        return ACPProbeResult.model_validate(safe.model_dump(mode="json"))

    def get_acp_probe(self, probe_id: str) -> ACPProbeResult | None:
        with self._lock:
            value = self._acp_probes.get(str(probe_id))
            return (
                ACPProbeResult.model_validate(value.model_dump(mode="json"))
                if value is not None
                else None
            )

    def list_acp_probes(
        self,
        dimension: ACPProbeDimension | None = None,
        *,
        include_inflight: bool = True,
    ) -> tuple[ACPProbeResult, ...]:
        with self._lock:
            values = list(self._acp_probes.values())
        key = getattr(dimension, "stable_key", None)
        if key is not None:
            values = [item for item in values if item.stable_key == key]
        if not include_inflight:
            values = [
                item
                for item in values
                if item.status.value not in {"queued", "running"}
            ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return tuple(
            ACPProbeResult.model_validate(item.model_dump(mode="json"))
            for item in values
        )

    def latest_acp_probe(self, dimension: ACPProbeDimension) -> ACPProbeResult | None:
        values = self.list_acp_probes(dimension, include_inflight=False)
        return values[0] if values else None

    def create(
        self,
        snapshot: ExecutionState,
        *,
        specification: Mapping[str, object] | None = None,
        provenance: Mapping[str, object] | None = None,
        server_bindings: Sequence[Mapping[str, object]] = (),
        harness_binding: Mapping[str, object] | None = None,
        parent_execution_id: ExecutionId | str | None = None,
        run_id: RunId | str | None = None,
    ) -> None:
        del provenance, server_bindings, harness_binding, parent_execution_id
        try:
            from .._test_runs import associate_execution

            associate_execution(snapshot.execution_id, run_id=run_id or snapshot.run_id)
        except Exception:
            # Recording must never change execution behavior.
            pass
        key = _execution_key(snapshot.execution_id)
        validated: ExecutionSpec | None = None
        if specification is not None:
            try:
                validated = _EXECUTION_SPEC_ADAPTER.validate_python(specification)
            except ValidationError as exc:
                raise StorageError("execution specification is invalid") from exc
        with self._lock:
            if key in self._snapshots:
                raise StorageConflict("execution already exists")
            effective_run_id = snapshot.run_id or (
                run_id
                if isinstance(run_id, RunId)
                else RunId(run_id)
                if run_id is not None
                else None
            )
            self._snapshots[key] = snapshot.model_copy(
                update={"run_id": effective_run_id}
            )
            self._events[key] = ()
            self._reserved_sequences[key] = set()
            if validated is not None:
                self._specifications[key] = _copy_execution_spec(validated)

    # Friendly aliases are intentionally kept on the concrete store while the
    # protocol stays small and framework-neutral.
    create_execution = create

    def save_test_run(self, run_id: str, value: Mapping[str, object]) -> None:
        key = str(run_id)
        safe = redact_for_persistence(
            dict(value), config=self._redaction_config, path="$.test_run"
        )
        with self._lock:
            self._test_runs[key] = (
                copy.deepcopy(dict(safe)) if isinstance(safe, Mapping) else {}
            )

    def get_test_run(self, run_id: str) -> Mapping[str, object] | None:
        with self._lock:
            value = self._test_runs.get(str(run_id))
            return copy.deepcopy(value) if value is not None else None

    def list_test_runs(self) -> tuple[Mapping[str, object], ...]:
        with self._lock:
            values = copy.deepcopy(list(self._test_runs.values()))
        values.sort(
            key=lambda item: (
                str(item.get("created_at", "")),
                str(item.get("run_id", "")),
            )
        )
        return tuple(dict(value) for value in values)

    def save_test_result(
        self, run_id: str, attempt_id: str, value: Mapping[str, object]
    ) -> None:
        safe = redact_for_persistence(
            dict(value), config=self._redaction_config, path="$.test_result"
        )
        with self._lock:
            self._test_results.setdefault(str(run_id), {})[str(attempt_id)] = (
                copy.deepcopy(dict(safe)) if isinstance(safe, Mapping) else {}
            )

    def list_test_results(self, run_id: str) -> tuple[Mapping[str, object], ...]:
        with self._lock:
            values = copy.deepcopy(
                list(self._test_results.get(str(run_id), {}).values())
            )
        values.sort(
            key=lambda item: (
                str(item.get("node_id", "")),
                str(item.get("attempt_id", "")),
            )
        )
        return tuple(dict(value) for value in values)

    def get_snapshot(self, execution_id: ExecutionId | str) -> ExecutionState | None:
        key = _execution_key(execution_id)
        with self._lock:
            snapshot = self._snapshots.get(key)
            return snapshot.model_copy() if snapshot is not None else None

    def get_execution_spec(
        self, execution_id: ExecutionId | str
    ) -> ExecutionSpec | None:
        key = _execution_key(execution_id)
        with self._lock:
            specification = self._specifications.get(key)
            return (
                _copy_execution_spec(specification)
                if specification is not None
                else None
            )

    def list_executions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        lifecycle: ExecutionStatus | str | None = None,
        outcome: ExecutionOutcome | str | None = None,
        run_id: RunId | str | None = None,
    ) -> ExecutionPage:
        page = ExecutionPage(limit=limit, offset=offset)
        lifecycle_value = ExecutionStatus(lifecycle) if lifecycle is not None else None
        outcome_value = ExecutionOutcome(outcome) if outcome is not None else None
        with self._lock:
            snapshots = list(self._snapshots.values())
        filtered = [
            snapshot
            for snapshot in snapshots
            if (lifecycle_value is None or snapshot.lifecycle is lifecycle_value)
            and (outcome_value is None or snapshot.outcome is outcome_value)
            and (
                run_id is None
                or snapshot.run_id
                == (run_id if isinstance(run_id, RunId) else RunId(str(run_id)))
            )
        ]
        filtered.sort(
            key=lambda item: (item.created_at, str(item.execution_id.root)),
            reverse=True,
        )
        return page.model_copy(
            update={
                "items": tuple(
                    item.model_copy() for item in filtered[offset : offset + limit]
                ),
                "total": len(filtered),
            }
        )

    def get_report(
        self,
        execution_id: ExecutionId | str,
        *,
        after_sequence: int = -1,
        event_limit: int | None = None,
        artifact_limit: int | None = None,
    ) -> ExecutionReport | None:
        snapshot = self.get_snapshot(execution_id)
        if snapshot is None:
            return None
        if (
            after_sequence < -1
            or (event_limit is not None and event_limit < 1)
            or (artifact_limit is not None and artifact_limit < 1)
        ):
            raise ValueError("invalid report event cursor or limit")
        all_events = self.events(execution_id)
        events = tuple(event for event in all_events if event.sequence > after_sequence)
        selected = events if event_limit is None else events[:event_limit]
        direct_result, error, evidence = _report_fields(all_events)
        return ExecutionReport(
            snapshot=snapshot,
            events=selected,
            direct_result=direct_result,
            error=error,
            evidence=evidence,
            turns=tuple(
                result for _, result in self.turns(execution_id) if result is not None
            ),
            evaluations=self.evaluations(execution_id),
            event_count=len(events),
            events_truncated=event_limit is not None and len(events) > event_limit,
            next_after_sequence=selected[-1].sequence if selected else after_sequence,
            artifact_count=0,
            artifacts_truncated=False,
        )

    def save_evaluation(
        self,
        execution_id: ExecutionId | str,
        result: EvaluationResult | Mapping[str, object],
        *,
        evaluation_id: str | None = None,
        turn_id: object | None = None,
    ) -> str:
        key = _execution_key(execution_id)
        with self._lock:
            if key not in self._snapshots:
                raise StorageConflict("execution does not exist")
            value: dict[str, Any] = (
                result.model_dump(mode="json")
                if isinstance(result, EvaluationResult)
                else dict(result)
            )
            raw_context = value.get("context")
            context: Mapping[str, Any] = (
                raw_context if isinstance(raw_context, Mapping) else {}
            )
            identifier = evaluation_id or str(
                value.get("evaluation_id")
                or f"evaluation-{len(self._evaluations.get(key, [])) + 1}"
            )
            turn_value = turn_id or context.get("turn_id")
            if turn_value is not None:
                turn_key = str(getattr(turn_value, "root", turn_value))
                if not any(
                    event.turn_id is not None and event.turn_id.root == turn_key
                    for event in self._events.get(key, ())
                ):
                    raise StorageConflict("turn does not belong to execution")
            subject_digest = hashlib.sha256(
                json.dumps(
                    context.get("subject"),
                    sort_keys=True,
                    default=str,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            record = EvaluationRecord(
                evaluation_id=EvaluationId(identifier),
                execution_id=ExecutionId(key),
                case_id=str(value.get("case_id") or context.get("case_id"))
                if (value.get("case_id") or context.get("case_id")) is not None
                else None,
                turn_id=TurnId(str(getattr(turn_value, "root", turn_value)))
                if turn_value
                else None,
                name=str(value.get("name", "")),
                status=EvaluationStatus(value.get("status", "error")),
                required=bool(value.get("required", False)),
                message=value.get("message"),
                score=value.get("score"),
                rationale=value.get("rationale"),
                metrics=value.get("metrics", {}),
                provenance=value.get("provenance"),
                details=value.get("details", {}),
                goal=context.get("goal"),
                metadata=context.get("metadata", {}),
                subject_kind=str(context.get("subject_kind", "unknown")),
                subject_digest=subject_digest,
                run_id=RunId(
                    str(
                        getattr(
                            self._snapshots[key].run_id,
                            "root",
                            self._snapshots[key].run_id,
                        )
                    )
                )
                if self._snapshots[key].run_id
                else None,
            )
            if any(
                item.evaluation_id == record.evaluation_id
                for item in self._evaluations.get(key, [])
            ):
                raise StorageConflict("evaluation already exists")
            self._evaluations.setdefault(key, []).append(record)
            return identifier

    def save_turn(self, snapshot: TurnState, result: TurnResult | None = None) -> None:
        execution_key = next(
            (
                key
                for key, events in self._events.items()
                if any(event.session_id == snapshot.session_id for event in events)
            ),
            None,
        )
        if execution_key is None:
            execution_key = _execution_key(str(snapshot.session_id.root))
        with self._lock:
            values = self._turns.setdefault(execution_key, [])
            values[:] = [item for item in values if item[0].turn_id != snapshot.turn_id]
            values.append(
                (
                    snapshot.model_copy(),
                    result.model_copy() if result is not None else None,
                )
            )

    append_turn = save_turn

    def turns(
        self, execution_id: ExecutionId | str
    ) -> tuple[tuple[TurnState, TurnResult | None], ...]:
        with self._lock:
            values = self._turns.get(_execution_key(execution_id), ())
            return tuple(
                (
                    snapshot.model_copy(),
                    result.model_copy() if result is not None else None,
                )
                for snapshot, result in values
            )

    def evaluations(
        self, execution_id: ExecutionId | str, *, turn_id: object | None = None
    ) -> tuple[EvaluationRecord, ...]:
        key = _execution_key(execution_id)
        with self._lock:
            values = tuple(self._evaluations.get(key, ()))
        if turn_id is not None:
            turn_key = str(getattr(turn_id, "root", turn_id))
            values = tuple(
                item
                for item in values
                if str(getattr(item.turn_id, "root", item.turn_id)) == turn_key
            )
        return tuple(item.model_copy() for item in values)

    def aggregate_evaluations(self, query: EvaluationQuery) -> EvaluationReport:
        """Calculate summaries from the evaluations currently in memory."""
        if not isinstance(query, EvaluationQuery):
            query = EvaluationQuery.model_validate(query)
        with self._lock:
            records = [
                record for values in self._evaluations.values() for record in values
            ]
            snapshots = {key: value for key, value in self._snapshots.items()}
            specifications = {key: value for key, value in self._specifications.items()}
        traces = {}
        for execution_id in {record.execution_id.root for record in records}:
            try:
                trace = self.get_trace_view(execution_id)
            except (TraceUnavailable, TraceNotFinalized, StorageError, ValueError):
                trace = None
            if trace is not None:
                traces[execution_id] = trace
        return aggregate_evaluations(
            query,
            records,
            snapshots=snapshots,
            specifications=specifications,
            traces=traces,
        )

    def get_trace(self, execution_id: ExecutionId | str) -> TraceResult | None:
        snapshot = self.get_snapshot(execution_id)
        if snapshot is None:
            return None
        events = self.events(execution_id)
        created_events = [
            event for event in events if event.kind is EventKind.EXECUTION_CREATED
        ]
        if (
            not events
            or events[0].sequence != 0
            or events[0].kind is not EventKind.EXECUTION_CREATED
            or len(created_events) != 1
        ):
            raise TraceUnavailable("persisted execution.created evidence is malformed")
        trace_id = events[0].payload.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            raise TraceUnavailable("trace identity evidence is unavailable")
        try:
            typed_trace_id = TraceId(trace_id)
        except ValueError:
            raise TraceUnavailable("trace identity evidence is invalid") from None
        if typed_trace_id.root != trace_id:
            raise TraceUnavailable("trace identity evidence is not stable")
        terminal = [
            event for event in events if event.kind is EventKind.EXECUTION_FINISHED
        ]
        if not terminal:
            raise TraceNotFinalized("execution has not been finalized")
        if len(terminal) != 1 or terminal[0] is not events[-1]:
            raise TraceUnavailable("persisted trace terminal evidence is malformed")
        final = terminal[-1]
        outcome = final.payload.get("outcome")
        completeness = final.payload.get("completeness")
        raw_limitations = final.payload.get("limitations")
        if (
            not isinstance(outcome, str)
            or outcome not in {item.value for item in ExecutionOutcome}
            or completeness not in {"complete", "partial"}
            or not isinstance(raw_limitations, (list, tuple))
            or any(
                not isinstance(item, str) or not item.strip()
                for item in raw_limitations
            )
        ):
            raise TraceUnavailable("persisted execution.finished evidence is malformed")
        limitations = tuple(raw_limitations)
        try:
            typed_outcome = ExecutionOutcome(outcome)
        except ValueError:
            raise TraceUnavailable("persisted execution outcome is invalid") from None
        if (
            snapshot.lifecycle is not ExecutionStatus.FINISHED
            or snapshot.outcome != typed_outcome
        ):
            raise TraceUnavailable(
                "persisted snapshot outcome conflicts with terminal evidence"
            )
        try:
            return TraceResult(
                trace_id=typed_trace_id,
                execution_id=snapshot.execution_id,
                completeness=cast(Literal["complete", "partial"], completeness),
                highest_sequence=events[-1].sequence if events else 0,
                events=events,
                limitations=limitations,
            )
        except (TypeError, ValueError):
            raise TraceUnavailable("persisted trace evidence is malformed") from None

    def get_trace_view(self, execution_id: ExecutionId | str) -> TraceView | None:
        trace = self.get_trace(execution_id)
        return trace.view() if trace is not None else None

    def save_snapshot(self, snapshot: ExecutionState) -> None:
        key = _execution_key(snapshot.execution_id)
        with self._lock:
            if key not in self._snapshots:
                raise StorageConflict("execution does not exist")
            derived = self._derive_snapshot(key, self._events[key])
            if snapshot != derived:
                raise StorageConflict("snapshot is derived from committed events")
            self._snapshots[key] = derived

    update_snapshot = save_snapshot

    def append_events(self, events: Sequence[Event]) -> None:
        batch = tuple(events)
        if not batch:
            return
        execution_id = _execution_key(batch[0].execution_id)
        self._commit(execution_id, batch)

    append = append_events

    def append_event(self, event: Event, content: bytes, *, media_type: str) -> Event:
        """Commit an event and its raw blob as one in-memory operation."""
        execution_id = _execution_key(event.execution_id)
        with self._lock:
            if event.raw_evidence_ref is not None:
                raise StorageConflict("raw evidence reference must be store-owned")
            if execution_id not in self._events:
                raise StorageConflict("execution does not exist")
            safe_media_type = redact_for_persistence(
                media_type,
                config=self._redaction_config,
                path="$.raw_evidence.media_type",
            )
            if (
                not isinstance(safe_media_type, str)
                or not safe_media_type
                or len(safe_media_type) > 256
            ):
                raise ValueError("raw evidence media_type must be 1-256 characters")
            event_id = str(event.event_id.root)
            if any(
                item.event_id == event.event_id for item in self._events[execution_id]
            ):
                raise StorageConflict("event id is already committed")
            evidence_id = _evidence_id_for(event.event_id)
            if evidence_id in self._raw_refs:
                raise StorageConflict("raw evidence id is already committed")
            event_ids = {str(item.event_id.root) for item in self._events[execution_id]}
            used = sum(
                ref.size_bytes or 0
                for evidence_id, ref in self._raw_refs.items()
                if self._raw_event_ids[evidence_id] in event_ids
            )
            prepared = _prepare_evidence(
                content,
                config=self._capture_config,
                redaction_config=self._redaction_config,
                remaining_bytes=max(self._capture_config.raw_execution_bytes - used, 0),
            )
            ref = _make_evidence_ref(
                event.event_id, prepared.content, media_type=safe_media_type
            ).model_copy(
                update={
                    "storage_key": "memory:"
                    + hashlib.sha256(prepared.content).hexdigest()
                }
            )
            evidence_id = ref.evidence_id
            digest = ref.sha256
            if digest is None:
                raise StorageError("raw evidence reference is incomplete")
            capture = _make_evidence_capture(ref, prepared)
            event_payload = {
                **dict(event.payload),
                "raw_capture": capture.model_dump(mode="json"),
            }
            previous_blob = self._raw_blobs.get(digest)
            previous_count = self._raw_refcounts.get(digest, 0)
            previous_ref = self._raw_refs.get(evidence_id)
            previous_event_id = self._raw_event_ids.get(evidence_id)
            self._raw_blobs.setdefault(digest, prepared.content)
            self._raw_refcounts[digest] = previous_count + 1
            self._raw_refs[evidence_id] = ref
            self._raw_event_ids[evidence_id] = event_id
            try:
                committed_event = event.model_copy(
                    update={"raw_evidence_ref": ref, "payload": event_payload}
                )
                self._commit_checked(execution_id, (committed_event,))
                return self._events[execution_id][-1].model_copy()
            except BaseException:
                self._reserved_sequences[execution_id].discard(event.sequence)
                if previous_ref is None:
                    self._raw_refs.pop(evidence_id, None)
                else:
                    self._raw_refs[evidence_id] = previous_ref
                if previous_event_id is None:
                    self._raw_event_ids.pop(evidence_id, None)
                else:
                    self._raw_event_ids[evidence_id] = previous_event_id
                if previous_count:
                    self._raw_refcounts[digest] = previous_count
                else:
                    self._raw_refcounts.pop(digest, None)
                    if previous_blob is None:
                        self._raw_blobs.pop(digest, None)
                raise

    def _commit(self, execution_id: str, events: tuple[Event, ...]) -> None:
        try:
            self._commit_checked(execution_id, events)
        except Exception:
            with self._lock:
                reserved = self._reserved_sequences.get(execution_id)
                if reserved is not None:
                    failed_sequences = {event.sequence for event in events}
                    reserved.difference_update(failed_sequences)
            raise

    def _commit_checked(self, execution_id: str, events: tuple[Event, ...]) -> None:
        if not events:
            return
        safe_events = tuple(self._redact_event(event) for event in events)
        callbacks: tuple[EventCallback, ...]
        committed: tuple[Event, ...]
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
                if event.kind is EventKind.SESSION_CREATED
                and event.session_id is not None
            }
            for event in safe_events:
                if event.kind is EventKind.SESSION_STATE_CHANGED:
                    if (
                        event.session_id is None
                        or event.session_id not in candidate_sessions
                    ):
                        raise StorageConflict("session does not exist")
                elif event.kind is EventKind.SESSION_CREATED:
                    if event.session_id is None:
                        raise StorageConflict("session event requires a session")
                    if event.session_id in candidate_sessions:
                        raise StorageConflict("session already exists")
                    candidate_sessions.add(event.session_id)
            for event in safe_events:
                if _execution_key(event.execution_id) != execution_id:
                    raise StorageConflict(
                        "all events in an append must belong to one execution"
                    )
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
            candidate_events = current + tuple(
                item.model_copy() for item in safe_events
            )
            candidate_snapshot = self._derive_snapshot(execution_id, candidate_events)
            # Tuple replacement is the commit point. Readers cannot observe
            # the candidate batch because it is never placed in _events earlier.
            # Event is recursively immutable, so a shallow model
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

    def _derive_snapshot(
        self, execution_id: str, events: tuple[Event, ...]
    ) -> ExecutionState:
        previous = self._snapshots[execution_id]
        lifecycle = ExecutionStatus.CREATED
        outcome: ExecutionOutcome | None = None
        created_at = previous.created_at
        finished_at: datetime | None = None
        highest = events[-1].sequence if events else previous.sequence
        for event in events:
            if event.kind is EventKind.EXECUTION_CREATED:
                created_at = event.timestamp
                lifecycle = ExecutionStatus.CREATED
                outcome = None
                finished_at = None
            elif event.kind is EventKind.EXECUTION_STATE_CHANGED:
                value = event.payload.get("lifecycle", event.payload.get("state"))
                try:
                    lifecycle = ExecutionStatus(value)
                except (TypeError, ValueError) as exc:
                    raise StorageConflict("execution state payload is invalid") from exc
                if lifecycle is ExecutionStatus.FINISHED:
                    raise StorageConflict("execution state payload is invalid")
            elif event.kind is EventKind.EXECUTION_FINISHED:
                try:
                    outcome = ExecutionOutcome(event.payload["outcome"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise StorageConflict(
                        "execution terminal payload is invalid"
                    ) from exc
                lifecycle = ExecutionStatus.FINISHED
                finished_at = event.timestamp
        return ExecutionState(
            execution_id=ExecutionId(execution_id),
            run_id=previous.run_id,
            lifecycle=lifecycle,
            outcome=outcome,
            sequence=highest,
            created_at=created_at
            if created_at.tzinfo is not None
            else datetime.now(timezone.utc),
            finished_at=finished_at,
        )

    def _redact_event(self, event: Event) -> Event:
        projected = redact_model_json(
            event, config=self._redaction_config, path="$.event"
        )
        if not isinstance(projected, Mapping):
            raise StorageError("event projection is invalid")
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
            # Pydantic's validation context can render projected input.  The
            # redaction boundary must not preserve it as an exception cause.
            raise StorageError("event projection is invalid") from None
        if any(
            getattr(safe_event, field) != getattr(event, field)
            for field in immutable_fields
        ):
            raise StorageError("event identity changed during redaction")
        return safe_event

    def iter_events(
        self, execution_id: ExecutionId | str, *, after_sequence: int = -1
    ) -> Iterator[Event]:
        if after_sequence < -1:
            raise ValueError("after_sequence must be >= -1")
        key = _execution_key(execution_id)
        with self._lock:
            events = tuple(event.model_copy() for event in self._events.get(key, ()))
        return iter(event for event in events if event.sequence > after_sequence)

    def events(
        self, execution_id: ExecutionId | str, *, after_sequence: int = -1
    ) -> tuple[Event, ...]:
        return tuple(self.iter_events(execution_id, after_sequence=after_sequence))

    def transaction(self, execution_id: ExecutionId | str) -> ExecutionTransaction:
        key = _execution_key(execution_id)
        with self._lock:
            if key not in self._snapshots:
                raise StorageConflict("execution does not exist")
        return _ExecutionBatch(self, key)

    def subscribe(
        self, execution_id: ExecutionId | str, callback: EventCallback
    ) -> Callable[[], None]:
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

    def allocate(
        self, execution_id: ExecutionId | str, *, count: int = 1
    ) -> tuple[int, ...]:
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

    def release(
        self, execution_id: ExecutionId | str, sequences: Sequence[int]
    ) -> None:
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

    def put_raw_evidence(
        self, event_id: EventId | str, content: bytes, *, media_type: str
    ) -> EvidenceCapture:
        event_key = str(event_id.root if isinstance(event_id, EventId) else event_id)
        with self._lock:
            event = next(
                (
                    item
                    for events in self._events.values()
                    for item in events
                    if str(item.event_id.root) == event_key
                ),
                None,
            )
            if event is None:
                raise RawEvidenceUnavailable("raw evidence event does not exist")
            evidence_id = _evidence_id_for(event_key)
            if evidence_id in self._raw_refs:
                raise StorageConflict("raw evidence already exists for event")
            event_ids = {
                str(item.event_id.root)
                for item in self._events[_execution_key(event.execution_id)]
            }
            used = sum(
                ref.size_bytes or 0
                for evidence_id, ref in self._raw_refs.items()
                if self._raw_event_ids[evidence_id] in event_ids
            )
            remaining = self._capture_config.raw_execution_bytes - used
            safe_media_type = redact_for_persistence(
                media_type,
                config=self._redaction_config,
                path="$.raw_evidence.media_type",
            )
            if (
                not isinstance(safe_media_type, str)
                or not safe_media_type
                or len(safe_media_type) > 256
            ):
                raise ValueError("raw evidence media_type must be 1-256 characters")
            prepared = _prepare_evidence(
                content,
                config=self._capture_config,
                redaction_config=self._redaction_config,
                remaining_bytes=max(remaining, 0),
            )
            ref = _make_evidence_ref(
                event_key, prepared.content, media_type=safe_media_type
            ).model_copy(
                update={
                    "storage_key": (
                        "memory:" + hashlib.sha256(prepared.content).hexdigest()
                    )
                }
            )
            digest = ref.sha256
            assert digest is not None
            self._raw_blobs.setdefault(digest, prepared.content)
            self._raw_refcounts[digest] = self._raw_refcounts.get(digest, 0) + 1
            self._raw_refs[evidence_id] = ref
            self._raw_event_ids[evidence_id] = event_key
            return _make_evidence_capture(ref.model_copy(), prepared)

    def read_raw_evidence(
        self, reference: EvidenceRef, *, max_bytes: int = 1_048_576
    ) -> RawEvidence:
        if not isinstance(reference, EvidenceRef):
            raise RawEvidenceUnavailable("raw evidence reference is invalid")
        _validate_evidence_id(reference.evidence_id)
        with self._lock:
            expected = self._raw_refs.get(reference.evidence_id)
            if expected is None:
                raise RawEvidenceUnavailable("raw evidence is unavailable")
            _verify_evidence_reference(reference, expected)
            digest = expected.sha256
            if digest is None or digest not in self._raw_blobs:
                raise RawEvidenceUnavailable("raw evidence is unavailable")
            return _make_evidence_result(
                expected, self._raw_blobs[digest], max_bytes=max_bytes
            )

    def delete_execution(self, execution_id: ExecutionId | str) -> None:
        key = _execution_key(execution_id)
        with self._lock:
            snapshot = self._snapshots.get(key)
            if snapshot is None:
                raise StorageConflict("execution does not exist")
            if snapshot.lifecycle is not ExecutionStatus.FINISHED:
                raise StorageConflict("active execution cannot be deleted")
            event_ids = {str(item.event_id.root) for item in self._events[key]}
            for evidence_id, reference in tuple(self._raw_refs.items()):
                if self._raw_event_ids[evidence_id] not in event_ids:
                    continue
                del self._raw_refs[evidence_id]
                del self._raw_event_ids[evidence_id]
                if reference.sha256 is not None:
                    count = self._raw_refcounts[reference.sha256] - 1
                    if count <= 0:
                        self._raw_refcounts.pop(reference.sha256, None)
                        self._raw_blobs.pop(reference.sha256, None)
                    else:
                        self._raw_refcounts[reference.sha256] = count
            self._snapshots.pop(key)
            self._events.pop(key)
            self._specifications.pop(key, None)
            self._reserved_sequences.pop(key, None)
            self._callbacks.pop(key, None)

    delete = delete_execution


class InMemoryArtifactStore:
    """In-memory compressed, content-addressed artifact store."""

    def __init__(self, *, config: RedactionConfig | None = None) -> None:
        self._lock = threading.RLock()
        self._refs: dict[str, ArtifactRef] = {}
        self._blobs: dict[str, bytes] = {}
        self._redaction_config = (
            config if config is not None else RedactionConfig.from_environment()
        )

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
        ref = self._new_ref(
            execution_id, safe_name, safe_content, media_type=safe_media_type
        )
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

    def _prepare_metadata(
        self, name: str, media_type: str | None
    ) -> tuple[str, str | None]:
        safe_name = redact_for_persistence(
            name, config=self._redaction_config, path="$.artifact.name"
        )
        safe_media_type = redact_for_persistence(
            media_type, config=self._redaction_config, path="$.artifact.media_type"
        )
        if not isinstance(safe_name, str) or (
            safe_media_type is not None and not isinstance(safe_media_type, str)
        ):
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
        stored = self.get_ref(
            artifact.artifact_id if isinstance(artifact, ArtifactRef) else artifact
        )
        if isinstance(artifact, ArtifactRef) and stored != artifact:
            raise StorageConflict("artifact reference does not match stored metadata")
        return stored

    def iter_refs(
        self, execution_id: ExecutionId | str | None = None
    ) -> Iterator[ArtifactRef]:
        selected = None if execution_id is None else _execution_key(execution_id)
        with self._lock:
            refs = tuple(
                ref.model_copy()
                for ref in self._refs.values()
                if selected is None or _execution_key(ref.execution_id) == selected
            )
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


def _verify_blob(
    compressed: bytes, expected_sha256: str, expected_length: int
) -> bytes:
    try:
        content = gzip.decompress(compressed)
    except (OSError, EOFError) as exc:
        raise BlobIntegrityError("compressed artifact cannot be decompressed") from exc
    if (
        len(content) != expected_length
        or hashlib.sha256(content).hexdigest() != expected_sha256
    ):
        raise BlobIntegrityError(
            "artifact hash or uncompressed length does not match metadata"
        )
    return content


class TemporaryArtifactStore(InMemoryArtifactStore):
    """Filesystem-backed temporary artifact store with atomic blob placement."""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        config: RedactionConfig | None = None,
    ) -> None:
        super().__init__(config=config)
        self._owned_root = root is None
        self._root = (
            Path(tempfile.mkdtemp(prefix="mcp-pal-artifacts-"))
            if root is None
            else Path(root).resolve()
        )
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
        ref = self._new_ref(
            execution_id, safe_name, safe_content, media_type=safe_media_type
        )
        path = self._blob_path(ref.sha256)
        compressed = gzip.compress(safe_content, mtime=0)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                _verify_blob(path.read_bytes(), ref.sha256, ref.size_bytes)
            else:
                fd, temporary_name = tempfile.mkstemp(
                    prefix=".mcp-pal-", suffix=".tmp", dir=str(path.parent)
                )
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
