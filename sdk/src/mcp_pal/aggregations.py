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

from .types import EvaluationRecord, EvaluationStatus, FrozenModel

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
    measured_count: int = Field(default=0, ge=0)
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


def _labels(
    record: EvaluationRecord, snapshot: Any, spec: Any, trace: Any = None
) -> dict[str, Scalar]:
    labels: dict[str, Scalar] = {}
    metadata: dict[str, Scalar] = {}
    if spec is not None and isinstance(getattr(spec, "metadata", None), Mapping):
        metadata.update(spec.metadata)
    if isinstance(record.metadata, Mapping):
        metadata.update(record.metadata)
    run = record.run_id or getattr(snapshot, "run_id", None)
    labels["run_id"] = str(getattr(run, "root", run)) if run is not None else None
    project = getattr(snapshot, "project_id", None)
    labels["project_id"] = getattr(project, "root", project)
    labels["project_name"] = metadata.get("project_name")
    labels["suite_name"] = record.suite_name or getattr(snapshot, "suite_name", None)
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
    matrix_id = metadata.get("mcp_pal.matrix.matrix_id")
    cell_id = metadata.get("mcp_pal.matrix.cell_id")
    labels["case_id"] = (
        record.case_id
        or getattr(spec, "case_id", None)
        or metadata.get("case_id")
        or metadata.get("mcp_pal.case_id")
    )
    if labels["case_id"] is None and matrix_id is not None and cell_id is not None:
        labels["case_id"] = f"{matrix_id}:{cell_id}"
    if matrix_id is not None and cell_id is not None:
        labels["matrix.id"] = matrix_id
        labels["matrix.cell"] = cell_id
        labels["trial.number"] = metadata.get("mcp_pal.matrix.trial")
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
        labels["execution_kind"] = getattr(spec, "kind", None)
        server_names: tuple[Any, ...] = tuple(
            getattr(binding, "alias", None)
            or getattr(getattr(binding, "server", None), "name", None)
            for binding in getattr(spec, "servers", ())
        )
        server_names = tuple(str(value) for value in server_names if value)
        labels.setdefault(
            "server",
            server_names[0]
            if len(server_names) == 1
            else ("multiple" if server_names else None),
        )
        operation = getattr(spec, "operation", None)
        labels.setdefault(
            "tool", getattr(operation, "name", None) if operation is not None else None
        )
        labels["harness"] = getattr(getattr(spec, "harness", None), "name", None)
        server_bindings: Any = cast(Any, getattr(spec, "servers", ()))
        server_value: Any = (
            getattr(getattr(server_bindings[0], "server", None), "kind", None)
            if server_bindings
            else None
        )
        labels.setdefault("transport", server_value)
        labels.setdefault(
            "model", getattr(getattr(spec, "harness", None), "model", None)
        )
        if labels["case_id"] is None:
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


def aggregate_evaluations(
    query: EvaluationQuery,
    records: Iterable[EvaluationRecord],
    *,
    snapshots: Mapping[str, Any] | None = None,
    specifications: Mapping[str, Any] | None = None,
    traces: Mapping[str, Any] | None = None,
) -> EvaluationReport:
    """Aggregate records using the same implementation for memory and SQLite."""
    snapshots = snapshots or {}
    specifications = specifications or {}
    traces = traces or {}
    start = _utc(query.start) if query.start else None
    end = _utc(query.to) if query.to else None
    selected: list[tuple[EvaluationRecord, dict[str, Scalar], Any]] = []
    for record in records:
        snapshot = snapshots.get(record.execution_id.root)
        created = _utc(getattr(snapshot, "created_at", record.created_at))
        if (start and created < start) or (end and created >= end):
            continue
        snapshot = snapshots.get(record.execution_id.root)
        spec = specifications.get(record.execution_id.root)
        trace = traces.get(record.execution_id.root)
        labels = _labels(record, snapshot, spec, trace)
        if labels.get("case_id") is None:
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
            if (
                shape["runtime"]
                or shape["transport"]
                or shape["bindings"]
                or shape["tools"]
            ):
                labels["case_id"] = (
                    "trace:"
                    + hashlib.sha256(
                        json.dumps(shape, sort_keys=True, default=str).encode()
                    ).hexdigest()
                )
        selected.append((record, labels, traces.get(record.execution_id.root)))

    # Re-evaluation of the same execution/turn/subject is one trial.
    latest: dict[
        tuple[str, str | None, str, str, str | None],
        tuple[EvaluationRecord, dict[str, Scalar], Any],
    ] = {}
    for record, labels, trace in selected:
        key = (
            record.execution_id.root,
            record.turn_id.root if record.turn_id else None,
            record.name,
            record.subject_kind,
            record.subject_digest,
        )
        current = latest.get(key)
        if current is None or (record.created_at, record.evaluation_id.root) > (
            current[0].created_at,
            current[0].evaluation_id.root,
        ):
            latest[key] = (record, labels, trace)
    selected = list(latest.values())
    selected = [
        item
        for item in selected
        if all(
            not values or item[1].get(key) in values
            for key, values in query.filters.items()
        )
    ]

    group_names = tuple(query.group_by)
    evaluator_values = {record.name for record, _, _ in selected}

    def key_for(
        item: tuple[EvaluationRecord, dict[str, Scalar], Any],
    ) -> dict[str, Scalar]:
        record, labels, _ = item
        snapshot = snapshots.get(record.execution_id.root)
        created = getattr(snapshot, "created_at", record.created_at)
        return {
            name: (
                _bucket(created, name) if name.startswith("time.") else labels.get(name)
            )
            for name in group_names
        }

    grouped: dict[
        tuple[tuple[str, Scalar], ...],
        list[tuple[EvaluationRecord, dict[str, Scalar], Any]],
    ] = defaultdict(list)
    for item in selected:
        group_key: dict[str, Scalar] = key_for(item)
        grouped[tuple(group_key.items())].append(item)

    def values(
        items: list[tuple[EvaluationRecord, dict[str, Scalar], Any]],
    ) -> EvaluationStats:
        statuses = {status.value: 0 for status in EvaluationStatus}
        scores: list[float] = []
        durations: list[float] = []
        latencies: list[float] = []
        execution_ids: set[str] = set()
        health_items: dict[str, Any] = {}
        tools = success = failed = protocol = 0
        for record, _, trace in items:
            statuses[record.status.value] += 1
            if record.score is not None:
                scores.append(float(record.score))
            execution_ids.add(record.execution_id.root)
            if trace is not None and record.execution_id.root not in health_items:
                health_items[record.execution_id.root] = trace
        for execution_id, trace in health_items.items():
            snapshots.get(execution_id)
            if trace is not None:
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
        measured = (
            statuses[EvaluationStatus.PASSED.value]
            + statuses[EvaluationStatus.FAILED.value]
        )
        outcome_counts: dict[str, int] = {}
        for execution_id in execution_ids:
            outcome = getattr(
                getattr(snapshots.get(execution_id), "outcome", None), "value", None
            )
            if outcome is not None:
                outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        return EvaluationStats(
            trial_count=len(execution_ids),
            evaluation_count=len(items),
            measured_count=measured,
            status_counts=statuses,
            pass_rate=(
                statuses[EvaluationStatus.PASSED.value] / measured if measured else None
            ),
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
    total = values(selected)
    if len(evaluator_values) > 1:
        total = total.model_copy(update={"pass_rate": None, "average_score": None})
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
