"""Query and result values for persisted evaluation summaries."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from statistics import median
from typing import Any, cast

from pydantic import Field, field_validator, model_validator

from .types import (
    EvaluationId,
    EvaluationRecord,
    EvaluationStatus,
    ExecutionId,
    FrozenModel,
)

Scalar = str | int | float | bool | None
_TIME_GROUPS = {"time.hour", "time.day", "time.week"}
_SYSTEM_LABELS = {
    "run_id",
    "project_id",
    "project_name",
    "suite_name",
    "trial_id",
    "turn_id",
    "evaluator",
    "evaluation_status",
    "subject_kind",
    "case_id",
    "execution_kind",
    "server",
    "tool",
    "transport",
    "harness",
    "model",
    "evaluation_kind",
    "judge_provider",
    "judge_model",
    "rubric_id",
    "matrix.id",
    "matrix.cell",
    "trial.number",
}


class EvaluationQuery(FrozenModel):
    """A read-only query over persisted evaluations."""

    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    group_by: tuple[str, ...] = ()
    filters: Mapping[str, tuple[Scalar, ...]] = Field(default_factory=dict)
    limit: int = Field(default=200, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)

    @property
    def start(self) -> datetime | None:
        return self.from_

    @field_validator("from_", "to")
    @classmethod
    def _aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("aggregate time bounds must be timezone-aware")
        return value

    @field_validator("filters", mode="before")
    @classmethod
    def _filter_values(cls, value: Any) -> Mapping[str, tuple[Scalar, ...]]:
        if not isinstance(value, Mapping):
            raise ValueError("aggregate filters must be a mapping")
        normalized = {
            str(key): tuple(item) if isinstance(item, (list, tuple, set)) else (item,)
            for key, item in value.items()
        }
        if any(not values for values in normalized.values()):
            raise ValueError("aggregate filter values must not be empty")
        return normalized

    @model_validator(mode="after")
    def _valid_query(self) -> EvaluationQuery:
        if self.from_ is not None and self.to is not None and self.from_ >= self.to:
            raise ValueError("aggregate from must be before to")
        if len(set(self.group_by)) != len(self.group_by):
            raise ValueError("aggregate group_by labels must be unique")
        time_groups = tuple(name for name in self.group_by if name.startswith("time."))
        if len(time_groups) > 1 or any(
            name not in _TIME_GROUPS for name in time_groups
        ):
            raise ValueError("group_by may contain only one supported time bucket")
        if "evaluator" in self.filters and len(self.filters["evaluator"]) != 1:
            raise ValueError("evaluator filter must name exactly one evaluator")
        if "evaluator" not in self.group_by and "evaluator" not in self.filters:
            raise ValueError("evaluator must be filtered or grouped")
        for name in self.filters:
            if name.startswith("time."):
                raise ValueError("time buckets are only valid in group_by")
        for name in (*self.group_by, *self.filters):
            if name.startswith("metadata.") and len(name) > len("metadata."):
                continue
            if name not in _SYSTEM_LABELS and name not in _TIME_GROUPS:
                raise ValueError(f"unknown aggregate label: {name}")
        return self


class LatencyStats(FrozenModel):
    count: int = Field(default=0, ge=0)
    p50: float | None = Field(default=None, ge=0)
    p95: float | None = Field(default=None, ge=0)


class ToolCallStats(FrozenModel):
    total: int = Field(default=0, ge=0)
    successful: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class HealthStats(FrozenModel):
    """Execution and tool health observed for a group."""

    execution_count: int = Field(default=0, ge=0)
    tool_calls: ToolCallStats = Field(default_factory=ToolCallStats)
    protocol_error_count: int = Field(default=0, ge=0)
    outcome_counts: Mapping[str, int] = Field(default_factory=dict)
    execution_duration_ms: LatencyStats = Field(default_factory=LatencyStats)
    server_latency_ms: LatencyStats = Field(default_factory=LatencyStats)


class EvaluationStats(FrozenModel):
    """Counts and health metrics for one aggregate group."""

    trial_count: int = Field(default=0, ge=0)
    evaluation_count: int = Field(default=0, ge=0)
    expected_count: int = Field(default=0, ge=0)
    missing_required_count: int = Field(default=0, ge=0)
    pending_required_count: int = Field(default=0, ge=0)
    status_counts: Mapping[str, int] = Field(default_factory=dict)
    pass_rate: float | None = Field(default=None, ge=0, le=1)
    average_score: float | None = Field(default=None, ge=0, le=1)
    score_count: int = Field(default=0, ge=0)
    health: HealthStats = Field(default_factory=HealthStats)


class EvaluationGroup(FrozenModel):
    key: Mapping[str, Scalar] = Field(default_factory=dict)
    values: EvaluationStats = Field(default_factory=EvaluationStats)


class EvaluationReport(FrozenModel):
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    totals: EvaluationStats = Field(default_factory=EvaluationStats)
    groups: tuple[EvaluationGroup, ...] = ()
    total_groups: int = 0
    limit: int = 200
    offset: int = 0


def _utc(value: datetime) -> datetime:
    return (
        value.astimezone(timezone.utc)
        if value.tzinfo
        else value.replace(tzinfo=timezone.utc)
    )


def _bucket(value: datetime, name: str) -> str:
    value = _utc(value)
    if name == "time.hour":
        return (
            value.replace(minute=0, second=0, microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    if name == "time.week":
        start = value.date().fromordinal(value.date().toordinal() - value.weekday())
        return start.isoformat()
    return value.date().isoformat()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    values.sort()
    if percentile <= 0:
        return values[0]
    if percentile >= 1:
        return values[-1]
    index = (len(values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def _observed(observation: Any) -> Scalar:
    return (
        getattr(observation, "value", None)
        if getattr(observation, "state", None) is not None
        else None
    )


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a persisted mapping or a model object."""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _labels(
    record: EvaluationRecord, snapshot: Any, spec: Any, trace: Any = None
) -> dict[str, Scalar]:
    labels: dict[str, Scalar] = {}
    metadata: dict[str, Scalar] = {}
    spec_metadata = _field(spec, "metadata")
    if isinstance(spec_metadata, Mapping):
        metadata.update(spec_metadata)
    if isinstance(record.metadata, Mapping):
        metadata.update(record.metadata)
    run = record.run_id or getattr(snapshot, "run_id", None)
    labels["run_id"] = str(getattr(run, "root", run)) if run is not None else None
    project = getattr(snapshot, "project_id", None)
    labels["project_id"] = getattr(project, "root", project)
    labels["project_name"] = metadata.get("project_name")
    labels["suite_name"] = (
        record.suite_name
        or getattr(snapshot, "suite_name", None)
        or _field(spec, "suite_name")
    )
    labels["trial_id"] = str(record.execution_id.root)
    labels["turn_id"] = str(record.turn_id.root) if record.turn_id is not None else None
    labels["evaluator"] = record.name
    labels["evaluation_status"] = record.status.value
    labels["subject_kind"] = record.subject_kind
    labels["evaluation_kind"] = (
        getattr(record.provenance, "kind", None) or "deterministic"
    )
    labels["judge_provider"] = getattr(record.provenance, "provider", None)
    labels["judge_model"] = getattr(record.provenance, "model", None)
    labels["rubric_id"] = getattr(record.provenance, "rubric_id", None)
    matrix_id = metadata.get("m3.matrix.matrix_id")
    cell_id = metadata.get("m3.matrix.cell_id")
    labels["case_id"] = (
        record.case_id
        or _field(spec, "case_id")
        or metadata.get("case_id")
        or metadata.get("m3.case_id")
    )
    if labels["case_id"] is None and matrix_id is not None and cell_id is not None:
        labels["case_id"] = f"{matrix_id}:{cell_id}"
    if matrix_id is not None and cell_id is not None:
        labels["matrix.id"] = matrix_id
        labels["matrix.cell"] = cell_id
        labels["trial.number"] = metadata.get("m3.matrix.trial")
    for key, value in metadata.items():
        labels[f"metadata.{key}"] = value
    if trace is not None:
        runtime = getattr(trace, "runtime", None)
        observed_transport = _observed(getattr(runtime, "transport", None))
        if observed_transport is not None:
            labels["transport"] = str(
                getattr(observed_transport, "value", observed_transport)
            )
        observed_model = _observed(getattr(runtime, "model_id", None))
        if observed_model is not None:
            labels["model"] = str(observed_model)
        calls = getattr(trace, "tool_calls", ())
        if calls:
            observed_servers = {
                _observed(getattr(call, "server", None)) for call in calls
            }
            tools = {_observed(getattr(call, "tool", None)) for call in calls}
            observed_servers.discard(None)
            tools.discard(None)
            labels["server"] = (
                next(iter(observed_servers))
                if len(observed_servers) == 1
                else ("multiple" if len(observed_servers) > 1 else None)
            )
            labels["tool"] = (
                next(iter(tools))
                if len(tools) == 1
                else ("multiple" if len(tools) > 1 else None)
            )
        runtime_kind = getattr(runtime, "kind", None)
        if runtime_kind is not None:
            labels.setdefault(
                "execution_kind", "direct" if runtime_kind == "direct" else "agent"
            )
            labels.setdefault("harness", runtime_kind)
    if spec is not None:
        labels["execution_kind"] = _field(spec, "kind")
        server_bindings = _field(spec, "servers", ()) or ()
        server_names: tuple[Any, ...] = tuple(
            _field(binding, "alias") or _field(_field(binding, "server"), "name")
            for binding in server_bindings
        )
        server_names = tuple(str(value) for value in server_names if value)
        labels.setdefault(
            "server",
            server_names[0]
            if len(server_names) == 1
            else ("multiple" if server_names else None),
        )
        operation = _field(spec, "operation")
        labels.setdefault(
            "tool", _field(operation, "name") if operation is not None else None
        )
        # A persisted spec is authoritative when it contains a harness.  Old
        # records may have only runtime trace metadata, so retain that
        # observed value when the spec has no resolved harness.
        harness = _field(spec, "harness")
        spec_harness = (
            harness.get("name") or harness.get("kind")
            if isinstance(harness, Mapping)
            else getattr(harness, "name", None)
        )
        if spec_harness is not None:
            labels["harness"] = spec_harness
        server_value: Any = (
            _field(_field(server_bindings[0], "server"), "kind")
            if server_bindings
            else None
        )
        labels.setdefault("transport", server_value)
        labels.setdefault("model", _field(harness, "model"))
        if labels["case_id"] is None:
            if isinstance(spec, Mapping):
                normalized = {
                    key: value
                    for key, value in spec.items()
                    if key
                    not in {
                        "run_id",
                        "case_id",
                        "suite_id",
                        "suite_name",
                        "metadata",
                        "evaluations",
                    }
                }
            else:
                normalized = spec.model_dump(
                    mode="json",
                    exclude={
                        "run_id",
                        "case_id",
                        "suite_id",
                        "suite_name",
                        "metadata",
                        "evaluations",
                    },
                )
            labels["case_id"] = (
                "spec:"
                + hashlib.sha256(
                    json.dumps(
                        normalized, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
            )
    return labels


def _requirement_is_running(snapshot: Any, attempt_state: str | None = None) -> bool:
    """Return linked-attempt liveness, falling back to execution lifecycle.

    Storage adapters normalize persisted attempt outcomes to a string before
    calling the aggregation layer.  The only attempt state that changes the
    terminal fallback is ``running``; every other state is terminal/unknown.
    """
    lifecycle = getattr(snapshot, "lifecycle", None)
    execution_running = getattr(lifecycle, "value", lifecycle) != "finished"
    attempt_running = attempt_state == "running"
    # An unresolved requirement stays pending while either linked source is
    # live.  A terminal attempt cannot override a still-running execution.
    return execution_running or attempt_running


def aggregate_evaluations(
    query: EvaluationQuery,
    records: Iterable[EvaluationRecord],
    *,
    snapshots: Mapping[str, Any] | None = None,
    specifications: Mapping[str, Any] | None = None,
    traces: Mapping[str, Any] | None = None,
    attempt_states: Mapping[str, str] | None = None,
    project_names: Mapping[str, str] | None = None,
) -> EvaluationReport:
    """Aggregate persisted evaluations and unresolved specification requirements.

    The function deliberately keeps synthetic missing/pending requirements out
    of persisted status counts.  They are represented only by the explicit
    requirement counters, which keeps status grouping a faithful view of rows
    that actually exist in storage.
    """
    snapshots = snapshots or {}
    specifications = specifications or {}
    traces = traces or {}
    attempt_states = attempt_states or {}
    project_names = project_names or {}
    start = _utc(query.start) if query.start else None
    end = _utc(query.to) if query.to else None

    def _key(value: Any) -> str:
        return str(getattr(value, "root", value))

    snapshot_map = {_key(key): value for key, value in snapshots.items()}
    specification_map = {_key(key): value for key, value in specifications.items()}
    trace_map = {_key(key): value for key, value in traces.items()}
    attempt_map = {_key(key): value for key, value in attempt_states.items()}

    def _snapshot(execution_id: str) -> Any:
        return snapshot_map.get(execution_id)

    def _spec(execution_id: str) -> Any:
        return specification_map.get(execution_id)

    def _trace(execution_id: str) -> Any:
        return trace_map.get(execution_id)

    def _created(record: EvaluationRecord, execution_id: str | None = None) -> datetime:
        snapshot = _snapshot(execution_id or record.execution_id.root)
        return _utc(getattr(snapshot, "created_at", record.created_at))

    def _created_for_execution(execution_id: str) -> datetime:
        snapshot = _snapshot(execution_id)
        created = getattr(snapshot, "created_at", None)
        if created is not None:
            return _utc(created)
        # A requirement without a linked execution snapshot cannot be
        # assigned a deterministic time bucket; callers skip such entries.
        raise ValueError(f"execution snapshot missing for {execution_id}")

    def _in_time_window(created: datetime) -> bool:
        return not (
            (start is not None and created < start)
            or (end is not None and created >= end)
        )

    def _attach_labels(
        record: EvaluationRecord, execution_id: str | None = None
    ) -> tuple[dict[str, Scalar], Any]:
        execution = execution_id or record.execution_id.root
        trace = _trace(execution)
        labels = _labels(record, _snapshot(execution), _spec(execution), trace)
        project_id = labels.get("project_id")
        if project_id is not None and str(project_id) in project_names:
            labels["project_name"] = project_names[str(project_id)]
        if labels.get("case_id") is None and trace is not None:
            runtime = getattr(trace, "runtime", None)
            shape = {
                "runtime": getattr(runtime, "kind", None),
                "transport": _observed(getattr(runtime, "transport", None)),
                "bindings": tuple(
                    sorted(
                        str(getattr(item, "server_binding", None))
                        for item in getattr(trace, "timeline", ())
                        if getattr(item, "server_binding", None)
                    )
                ),
                "tools": tuple(
                    (
                        _observed(getattr(item, "server", None)),
                        _observed(getattr(item, "tool", None)),
                    )
                    for item in getattr(trace, "tool_calls", ())
                ),
            }
            if any(shape.values()):
                labels["case_id"] = (
                    "trace:"
                    + hashlib.sha256(
                        json.dumps(shape, sort_keys=True, default=str).encode()
                    ).hexdigest()
                )
        return labels, trace

    # Select and reduce persisted rows first.  A lineage is one execution,
    # turn, evaluator, subject kind and subject digest; a later advisory row
    # cannot erase the required flag established by an earlier row in it.
    candidates: list[tuple[EvaluationRecord, dict[str, Scalar], Any, bool]] = []
    for record in records:
        execution_id = record.execution_id.root
        created = _created(record)
        if not _in_time_window(created):
            continue
        labels, trace = _attach_labels(record)
        candidates.append((record, labels, trace, record.required))
    latest: dict[
        tuple[str, str | None, str, str, str | None],
        tuple[EvaluationRecord, dict[str, Scalar], Any, bool],
    ] = {}
    for item in candidates:
        record, labels, trace, required_seen = item
        lineage = (
            record.execution_id.root,
            record.turn_id.root if record.turn_id else None,
            record.name,
            record.subject_kind,
            record.subject_digest,
        )
        current = latest.get(lineage)
        if current is not None:
            required_seen = required_seen or current[3]
        if current is None or (record.created_at, record.evaluation_id.root) > (
            current[0].created_at,
            current[0].evaluation_id.root,
        ):
            latest[lineage] = (
                record.model_copy(update={"required": required_seen}),
                labels,
                trace,
                required_seen,
            )
        else:
            latest[lineage] = (
                current[0].model_copy(update={"required": required_seen}),
                current[1],
                current[2],
                required_seen,
            )

    persisted = [
        item
        for item in latest.values()
        if all(
            not values or item[1].get(name) in values
            for name, values in query.filters.items()
        )
    ]

    # Build spec-required expectations from execution/spec snapshots even when
    # no evaluation row exists.  A status filter is intentionally a persisted
    # row filter, so it excludes synthetic expectations completely.
    synthetic: list[tuple[None, dict[str, Scalar], Any, bool, str, str]] = []
    has_status_filter = "evaluation_status" in query.filters
    execution_ids = {_key(key) for key in snapshots}
    execution_ids.update(_key(key) for key in specifications)
    execution_ids.update(item[0].execution_id.root for item in latest.values())
    persisted_pairs = {
        (item[0].execution_id.root, item[0].name) for item in latest.values()
    }

    def _registrations(spec: Any) -> tuple[Any, ...]:
        if isinstance(spec, Mapping):
            value = spec.get("evaluations", ())
        else:
            value = getattr(spec, "evaluations", ())
        return tuple(value) if isinstance(value, (list, tuple, set)) else ()

    if not has_status_filter:
        for execution_id in execution_ids:
            snapshot = _snapshot(execution_id)
            spec = _spec(execution_id)
            if spec is None or snapshot is None:
                continue
            registrations = _registrations(spec)
            for registration in registrations:
                name = (
                    getattr(registration, "name", None)
                    if not isinstance(registration, Mapping)
                    else registration.get("name")
                )
                required = (
                    getattr(registration, "required", False)
                    if not isinstance(registration, Mapping)
                    else registration.get("required", False)
                )
                if (
                    not name
                    or not required
                    or (execution_id, str(name)) in persisted_pairs
                ):
                    continue
                try:
                    created = _created_for_execution(execution_id)
                except ValueError:
                    continue
                if not _in_time_window(created):
                    continue
                # A lightweight virtual row gives labels the same execution /
                # evaluator identity as a real row without entering counts.
                virtual = EvaluationRecord(
                    evaluation_id=EvaluationId(f"requirement:{execution_id}:{name}"),
                    execution_id=ExecutionId(execution_id),
                    name=str(name),
                    status=EvaluationStatus.NOT_RUN,
                    case_id=_field(spec, "case_id"),
                    suite_name=_field(spec, "suite_name"),
                    metadata=_field(spec, "metadata", {}) or {},
                )
                labels, trace = _attach_labels(virtual, execution_id)
                if not all(
                    not values or labels.get(label) in values
                    for label, values in query.filters.items()
                ):
                    continue
                active = _requirement_is_running(
                    snapshot, attempt_map.get(execution_id)
                )
                synthetic.append(
                    (
                        None,
                        labels,
                        trace,
                        False,
                        "pending" if active else "missing",
                        execution_id,
                    )
                )

    # evaluation_status is a persisted-row grouping/filter.  Synthetic items
    # are never visible in those groups; their counters are consequently zero.
    all_items: list[
        tuple[EvaluationRecord | None, dict[str, Scalar], Any, bool, str | None, str]
    ] = [
        (latest_record, labels, trace, required, None, latest_record.execution_id.root)
        for latest_record, labels, trace, required in persisted
    ]
    if "evaluation_status" not in query.group_by:
        all_items.extend(synthetic)

    group_names = tuple(query.group_by)

    def key_for(
        item: tuple[
            EvaluationRecord | None, dict[str, Scalar], Any, bool, str | None, str
        ],
    ) -> dict[str, Scalar]:
        record, labels, _, _, _, execution_id = item
        created = (
            _created(record, execution_id)
            if record is not None
            else _created_for_execution(execution_id)
        )
        return {
            name: _bucket(created, name)
            if name.startswith("time.")
            else labels.get(name)
            for name in group_names
        }

    grouped: dict[
        tuple[tuple[str, Scalar], ...],
        list[
            tuple[
                EvaluationRecord | None, dict[str, Scalar], Any, bool, str | None, str
            ]
        ],
    ] = defaultdict(list)
    for aggregate_item in all_items:
        group_key = key_for(aggregate_item)
        grouped[tuple(group_key.items())].append(aggregate_item)

    def values(
        items: list[
            tuple[
                EvaluationRecord | None, dict[str, Scalar], Any, bool, str | None, str
            ]
        ],
    ) -> EvaluationStats:
        statuses = {status.value: 0 for status in EvaluationStatus}
        scores: list[float] = []
        durations: list[float] = []
        latencies: list[float] = []
        execution_ids: set[str] = set()
        health_items: dict[str, Any] = {}
        missing = pending = expected = passed = 0
        tools = success = failed = protocol = 0
        for record, _, trace, _, synthetic_kind, execution_id in items:
            if record is None:
                execution_ids.add(execution_id)
                if trace is not None and execution_id not in health_items:
                    health_items[execution_id] = trace
                if synthetic_kind == "missing":
                    missing += 1
                    expected += 1
                elif synthetic_kind == "pending":
                    pending += 1
                continue
            statuses[record.status.value] += 1
            expected += 1
            passed += int(record.status is EvaluationStatus.PASSED)
            if record.score is not None:
                scores.append(float(record.score))
            execution_id = record.execution_id.root
            execution_ids.add(execution_id)
            if trace is not None and execution_id not in health_items:
                health_items[execution_id] = trace
        for _execution_id, trace in health_items.items():
            summary = getattr(trace, "summary", None)
            timing = getattr(summary, "timing", None)
            if timing is not None:
                durations.append(float(timing.duration_ms))
            tools += len(getattr(trace, "tool_calls", ()))
            success += int(getattr(summary, "successful_tool_call_count", 0))
            failed += int(getattr(summary, "failed_tool_call_count", 0))
            protocol += int(getattr(summary, "protocol_error_count", 0))
            for call in getattr(trace, "tool_calls", ()):
                observed = getattr(
                    getattr(call, "server_latency_ms", None), "value", None
                )
                if observed is not None:
                    latencies.append(float(observed))
        outcome_counts: dict[str, int] = {}
        for execution_id in execution_ids:
            outcome = getattr(
                getattr(_snapshot(execution_id), "outcome", None), "value", None
            )
            if outcome is not None:
                outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        return EvaluationStats(
            trial_count=len(execution_ids),
            evaluation_count=sum(1 for record, *_ in items if record is not None),
            expected_count=expected,
            missing_required_count=missing,
            pending_required_count=pending,
            status_counts=statuses,
            pass_rate=(passed / expected if expected else None),
            average_score=(sum(scores) / len(scores) if scores else None),
            score_count=len(scores),
            health=HealthStats(
                execution_count=len(execution_ids),
                tool_calls=ToolCallStats(
                    total=tools, successful=success, failed=failed
                ),
                protocol_error_count=protocol,
                outcome_counts=outcome_counts,
                execution_duration_ms=LatencyStats(
                    count=len(durations),
                    p50=median(durations) if durations else None,
                    p95=_percentile(durations, 0.95),
                ),
                server_latency_ms=LatencyStats(
                    count=len(latencies),
                    p50=median(latencies) if latencies else None,
                    p95=_percentile(latencies, 0.95),
                ),
            ),
        )

    group_values = [(dict(key), values(items)) for key, items in grouped.items()]
    group_values.sort(key=lambda item: tuple(str(value) for value in item[0].values()))
    total = values(all_items)
    visible = group_values[query.offset : query.offset + query.limit]
    report_type = cast(Callable[..., EvaluationReport], EvaluationReport)
    return report_type(
        from_=query.start,
        to=query.to,
        totals=total,
        groups=tuple(EvaluationGroup(key=key, values=item) for key, item in visible),
        total_groups=len(group_values),
        limit=query.limit,
        offset=query.offset,
    )


__all__ = [
    "EvaluationGroup",
    "EvaluationQuery",
    "EvaluationReport",
    "EvaluationStats",
    "HealthStats",
    "LatencyStats",
    "ToolCallStats",
    "aggregate_evaluations",
]
