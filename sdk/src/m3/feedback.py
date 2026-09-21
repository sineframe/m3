"""Deterministic feedback for coding agents improving MCP servers.

This module reads persisted M3 data. It never runs evaluators, parses
stdout, or invents a verdict when the recorded evidence is incomplete.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from ._test_runs import (
    evaluation_lineage as _evaluation_lineage,
)
from ._test_runs import (
    latest_evaluations as _latest_evaluations,
)
from ._test_runs import (
    required_evaluation_lineages as _required_evaluation_lineages,
)
from ._test_runs import (
    required_status_blocks as _required_status_blocks,
)
from ._test_runs import (
    xfail_waives_required_evaluations as _xfail_waives_required_evaluations,
)
from .storage import ExecutionStore
from .types import (
    EvaluationRecord,
    Event,
    EventDirection,
    ExecutionReport,
    ExecutionSpec,
    FrozenModel,
    RunId,
)


class Comparison(FrozenModel):
    """Differences between two saved runs on matched evidence."""

    baseline_run_id: str
    current_run_id: str
    baseline_suites: tuple[Mapping[str, Any], ...] = ()
    current_suites: tuple[Mapping[str, Any], ...] = ()
    interface_changes: tuple[Mapping[str, Any], ...] = ()
    test_changes: tuple[Mapping[str, Any], ...] = ()
    evaluation_changes: tuple[Mapping[str, Any], ...] = ()
    failures: tuple[Mapping[str, Any], ...] = ()
    coverage: Mapping[str, int] = {}
    limitations: tuple[str, ...] = ()


class Feedback(FrozenModel):
    """Agent-facing inventory and comparison for one saved run."""

    schema_version: int = 1
    run_id: str
    project_id: str | None = None
    project_name: str | None = None
    suites: tuple[Mapping[str, Any], ...] = ()
    tests: tuple[Mapping[str, Any], ...] = ()
    executions: tuple[Mapping[str, Any], ...] = ()
    failures: tuple[Mapping[str, Any], ...] = ()
    evaluation_stats: Mapping[str, Any] = {}
    summary: Mapping[str, Any] = {}
    limitations: tuple[str, ...] = ()
    comparison: Comparison | None = None


@dataclass(frozen=True)
class _Entry:
    report: ExecutionReport
    spec: ExecutionSpec | None
    trace: Any = None


@dataclass(frozen=True)
class _Page:
    server: str
    connection: str
    cursor_in: str | None
    cursor_out: str | None
    tools: tuple[Mapping[str, Any], ...]
    order: int
    request_event_id: str | None = None
    response_event_id: str | None = None


_TRIAL_SUFFIX = re.compile(r"(?:[-_:]trial[-_:]?\d+)$", re.IGNORECASE)
_OBSERVATIONAL_METADATA = {
    "m3.matrix.trial",
    "m3.matrix.trial_count",
    "m3.worker_id",
    "worker_id",
    "attempt_id",
    "started_at",
    "finished_at",
    "created_at",
}
_INPUT_FIELDS = {
    "prompt",
    "messages",
    "message",
    "goal",
    "expected",
    "expectation",
    "policy",
    "tool_policy",
    "evaluator",
    "evaluators",
    "checks",
    "matcher",
    "evaluations",
    "permission_policy",
    "elicitation_policy",
    "sampling_policy",
    "filesystem_policy",
    "terminal_policy",
    "operation",
    "validate_schemas",
}


def _id(value: Any) -> str:
    return str(getattr(value, "root", value))


def _run_key(value: RunId | str | None) -> str | None:
    return None if value is None else _id(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key
            if isinstance(key, str)
            else f"<unavailable-key:{type(key).__name__}>": _jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return (
            value
            if math.isfinite(value)
            else {"state": "unavailable", "type": "non-finite-float"}
        )
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json"))
        except Exception:
            return {
                "state": "unavailable",
                "type": f"{type(value).__module__}.{type(value).__qualname__}",
            }
    if isinstance(value, bytes):
        return {"state": "unavailable", "type": "bytes", "size": len(value)}
    return {
        "state": "unavailable",
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
    }


def _canonical(value: Any) -> str:
    return json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _scenario(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return _TRIAL_SUFFIX.sub("", text)


def _entries(store: ExecutionStore, run_id: str) -> tuple[_Entry, ...]:
    result: list[_Entry] = []
    offset = 0
    while True:
        page = store.list_executions(limit=100, offset=offset, run_id=run_id)
        if not page.items:
            break
        for snapshot in page.items:
            report = store.get_report(snapshot.execution_id)
            if report is not None:
                get_spec = getattr(store, "get_execution_spec", None)
                get_trace = getattr(store, "get_trace_view", None)
                try:
                    trace = (
                        get_trace(snapshot.execution_id)
                        if callable(get_trace)
                        else None
                    )
                except Exception:
                    trace = None
                result.append(
                    _Entry(
                        report,
                        get_spec(snapshot.execution_id) if callable(get_spec) else None,
                        trace,
                    )
                )
        offset += len(page.items)
        if offset >= page.total:
            break
    result.sort(
        key=lambda item: (
            item.report.snapshot.created_at,
            _id(item.report.snapshot.execution_id),
        )
    )
    return tuple(result)


def _metadata(
    entry: _Entry, record: EvaluationRecord | None = None
) -> Mapping[str, Any]:
    values: dict[str, Any] = {}
    if entry.spec is not None:
        values.update(entry.spec.metadata)
    if record is not None:
        values.update(record.metadata)
    return values


def _case(
    entry: _Entry,
    record: EvaluationRecord | None = None,
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> str | None:
    metadata = _metadata(entry, record)
    value = (
        (record.case_id if record is not None else None)
        or (entry.spec.case_id if entry.spec is not None else None)
        or metadata.get("m3.matrix.case_id")
        or metadata.get("m3.matrix.cell_id")
    )
    if value is None and contexts is not None:
        context = contexts.get(
            _id(
                record.execution_id
                if record is not None
                else entry.report.snapshot.execution_id
            )
        )
        value = context.get("node_id") if context else None
    return _scenario(value)


def _config(
    entry: _Entry,
    record: EvaluationRecord | None = None,
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[str | None, str | None]:
    metadata = _metadata(entry, record)
    agent = entry.report.snapshot.agent or entry.report.agent
    label = (
        metadata.get("harness_config")
        or metadata.get("m3.matrix.harness")
        or metadata.get("model")
    )
    if label is None and entry.spec is not None:
        harness = getattr(entry.spec, "harness", None)
        label = getattr(harness, "model", None)
        if label is None and harness is not None:
            label = type(harness).__name__
    if agent is not None and agent.harness.runtime == "managed":
        harness = agent.harness
        runtime_label = harness.resolved_version or harness.requested_selector
        if runtime_label:
            base_label = str(label or agent.model.requested_id)
            label = f"{base_label} · {harness.kind}@{runtime_label}"
    if entry.spec is None and label is None:
        context = (
            contexts.get(
                _id(
                    record.execution_id
                    if record is not None
                    else entry.report.snapshot.execution_id
                )
            )
            if contexts is not None
            else None
        )
        # Direct clients often have no immutable ExecutionSpec.  "direct" is
        # an honest configuration label: it makes repeated direct scenarios
        # comparable without pretending an agent/model score was observed.
        if context is not None:
            return "direct", "direct"
        return None, None
    if entry.spec is None:
        return str(label), str(label)
    dumped = entry.spec.model_dump(mode="json")
    # Configuration identity is intentionally a small allow-list.  New input
    # fields added to ExecutionSpec must not silently become identity fields;
    # otherwise an edited prompt/policy produces an unmatched result instead
    # of a matched, non-like-for-like comparison.
    stable_fields = {
        "kind",
        "servers",
        "protocol",
        "timeout_seconds",
        "artifact_policy",
        "declared_artifacts",
        "workspace",
        "harness",
        "harness_profile",
    }
    spec_value = {key: dumped[key] for key in stable_fields if key in dumped}
    if agent is not None and agent.harness.runtime == "managed":
        harness = agent.harness
        spec_value["managed_runtime"] = {
            "kind": harness.kind,
            "requested_selector": harness.requested_selector,
            "resolved_version": harness.resolved_version,
            "target": harness.target,
            "digest": harness.digest,
        }
    spec_metadata = dict(dumped.get("metadata") or {})
    for key in (
        *_OBSERVATIONAL_METADATA,
        "m3.matrix.cell_id",
        "m3.matrix.case_id",
    ):
        spec_metadata.pop(key, None)
    if spec_metadata:
        spec_value["metadata"] = spec_metadata
    digest = sha256(_canonical(spec_value).encode()).hexdigest()[:16]
    return (str(label) if label is not None else None), digest


def _input_value(
    entry: _Entry, record: EvaluationRecord | None = None
) -> Mapping[str, Any]:
    value: dict[str, Any] = {}
    if entry.spec is not None:
        dumped = entry.spec.model_dump(mode="json")
        value.update({key: dumped[key] for key in _INPUT_FIELDS if key in dumped})
    if record is not None:
        if record.details:
            details = dict(record.details)
            if record.provenance is not None and record.provenance.kind == "llm_judge":
                for key in (
                    "attempts",
                    "elapsed_ms",
                    "usage",
                    "returned_model",
                    "prompt_version",
                    "response_mode",
                    "error_code",
                ):
                    details.pop(key, None)
            # Matcher details contain observed subject evidence (execution and
            # trace IDs).  It is useful in the execution export, but must not
            # make identical expectations look changed across runs.
            details.pop("subject", None)
            identity = details.get("identity")
            if isinstance(identity, Mapping):
                details["identity"] = {
                    key: item
                    for key, item in identity.items()
                    if key not in {"execution_id", "trace_id", "run_id"}
                }
            value["details"] = details
        if record.provenance is not None:
            value["provenance"] = record.provenance.model_dump(mode="json")
    return value


def _input_fingerprint(
    entry: _Entry, record: EvaluationRecord | None = None
) -> str | None:
    value = _input_value(entry, record)
    return sha256(_canonical(value).encode()).hexdigest()[:16] if value else None


def _unknown_callable(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("implementation") == "unknown" and "callable" in value:
            return True
        return any(_unknown_callable(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_unknown_callable(item) for item in value)
    return False


def _evaluation_stats(
    records: Sequence[EvaluationRecord],
    *,
    expected_count: int | None = None,
    missing_required_count: int = 0,
    pending_required_count: int = 0,
) -> Mapping[str, Any]:
    """Return score signals without inventing values for unscored records."""
    measured_records = tuple(_latest_evaluations(records).values())
    statuses: dict[str, int] = defaultdict(int)
    scores = [record.score for record in measured_records if record.score is not None]
    for record in measured_records:
        statuses[record.status.value] += 1
    resolved_expected = (
        len(measured_records) if expected_count is None else expected_count
    )
    return {
        "evaluation_count": len(measured_records),
        "expected_count": resolved_expected,
        "missing_required_count": missing_required_count,
        "pending_required_count": pending_required_count,
        "pass_rate": (
            statuses.get("passed", 0) / resolved_expected if resolved_expected else None
        ),
        "score_count": len(scores),
        "average_score": round(sum(scores) / len(scores), 12) if scores else None,
        "status_counts": dict(sorted(statuses.items())),
    }


def _stats_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    comparable: bool,
) -> Mapping[str, Any]:
    fields = (
        "evaluation_count",
        "expected_count",
        "missing_required_count",
        "pending_required_count",
        "pass_rate",
        "score_count",
        "average_score",
    )
    result: dict[str, Any] = {}
    for field in fields:
        if field in {
            "evaluation_count",
            "expected_count",
            "missing_required_count",
            "pending_required_count",
        }:
            result[field] = after.get(field, 0) - before.get(field, 0)
        elif (
            comparable
            and before.get(field) is not None
            and after.get(field) is not None
        ):
            result[field] = round(after[field] - before[field], 12)
        else:
            result[field] = None
    return result


def _safe_filename(identifier: Any, suffix: str) -> str:
    """Use opaque names while retaining the original ID in the manifest."""
    digest = sha256(_id(identifier).encode("utf-8")).hexdigest()[:32]
    return digest + suffix


def _request_for(report: ExecutionReport, response: Event) -> Event | None:
    correlation = response.correlation
    if correlation is None or correlation.request_sequence is None:
        return None
    for candidate in report.events:
        candidate_correlation = candidate.correlation
        if (
            candidate.connection_id == response.connection_id
            and candidate_correlation is not None
            and candidate_correlation.direction is EventDirection.CLIENT_TO_SERVER
            and candidate_correlation.request_sequence == correlation.request_sequence
            and candidate.payload.get("method") == "tools/list"
        ):
            return candidate
    return None


def _pages(entry: _Entry) -> tuple[_Page, ...]:
    pages: list[_Page] = []
    for event in entry.report.events:
        payload = event.payload
        if not isinstance(payload, Mapping) or payload.get("method") not in {
            None,
            "tools/list",
        }:
            continue
        if (
            event.correlation is None
            or event.correlation.direction is not EventDirection.SERVER_TO_CLIENT
        ):
            continue
        request = _request_for(entry.report, event)
        if request is None:
            continue
        result = payload.get("result")
        tools = result.get("tools") if isinstance(result, Mapping) else None
        if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes)):
            continue
        params = request.payload.get("params")
        cursor_in = params.get("cursor") if isinstance(params, Mapping) else None
        cursor_out = (
            result.get("nextCursor", result.get("next_cursor"))
            if isinstance(result, Mapping)
            else None
        )
        pages.append(
            _Page(
                server=str(event.server_binding or request.server_binding or ""),
                connection=_id(event.connection_id or request.connection_id or ""),
                cursor_in=str(cursor_in) if cursor_in is not None else None,
                cursor_out=str(cursor_out) if cursor_out is not None else None,
                tools=tuple(item for item in tools if isinstance(item, Mapping)),
                order=event.sequence,
                request_event_id=_id(request.event_id),
                response_event_id=_id(event.event_id),
            )
        )
    return tuple(pages)


def _catalog_versions(entry: _Entry) -> tuple[Mapping[str, Any], ...]:
    grouped: dict[tuple[str, str], list[_Page]] = defaultdict(list)
    for page in _pages(entry):
        grouped[(page.server, page.connection)].append(page)
    versions: list[Mapping[str, Any]] = []
    for (server, connection), pages in grouped.items():
        pages = sorted(pages, key=lambda page: page.order)
        starts = [page for page in pages if page.cursor_in is None]
        used: set[int] = set()
        if not starts:
            versions.append(
                {
                    "server": server,
                    "connection": connection,
                    "tools": {},
                    "pages": [],
                    "complete": False,
                    "reason": "missing initial tools/list page",
                }
            )
            continue
        for start in starts:
            current = start
            tools: dict[str, Mapping[str, Any]] = {}
            observed_pages: list[Mapping[str, Any]] = []
            complete = True
            while True:
                used.add(id(current))
                observed_pages.append(
                    {
                        "cursor_in": current.cursor_in,
                        "cursor_out": current.cursor_out,
                        "order": current.order,
                        "request_event_id": current.request_event_id,
                        "response_event_id": current.response_event_id,
                        "tools": [dict(tool) for tool in current.tools],
                    }
                )
                for tool in current.tools:
                    name = tool.get("name")
                    if isinstance(name, str) and name:
                        tools[name] = dict(tool)
                if current.cursor_out is None:
                    break
                next_page = next(
                    (
                        candidate
                        for candidate in pages
                        if id(candidate) not in used
                        and candidate.order > current.order
                        and candidate.cursor_in == current.cursor_out
                    ),
                    None,
                )
                if next_page is None or id(next_page) in used:
                    complete = False
                    break
                current = next_page
            versions.append(
                {
                    "server": server,
                    "connection": connection,
                    "tools": tools,
                    "pages": observed_pages,
                    "complete": complete,
                }
            )
        if len(used) != len(pages):
            versions.append(
                {
                    "server": server,
                    "connection": connection,
                    "tools": {},
                    "pages": [],
                    "complete": False,
                    "reason": "unmatched tools/list page",
                }
            )
    return tuple(versions)


def _suite_value(value: Any) -> int | None:
    raw = getattr(value, "root", value)
    return int(raw) if raw is not None else None


def _suite(entry: _Entry, record: Any = None) -> Mapping[str, Any]:
    snapshot = entry.report.snapshot
    suite_id = _suite_value(getattr(record, "suite_id", None)) or _suite_value(
        getattr(snapshot, "suite_id", None)
    )
    suite_name = getattr(record, "suite_name", None) or getattr(
        snapshot, "suite_name", None
    )
    return {"suite_id": suite_id, "suite_name": suite_name}


def _suites(
    entries: Sequence[_Entry], results: Sequence[Mapping[str, Any]] = ()
) -> tuple[Mapping[str, Any], ...]:
    values = {
        _canonical(suite): suite
        for entry in entries
        if (suite := _suite(entry))["suite_id"] is not None
        or suite["suite_name"] is not None
    }
    for result in results:
        if result.get("suite_id") is not None or result.get("suite_name") is not None:
            value = {
                "suite_id": _suite_value(result.get("suite_id")),
                "suite_name": result.get("suite_name"),
            }
            values[_canonical(value)] = value
    return tuple(values[key] for key in sorted(values))


def _identity_sort_key(value: tuple[Any, ...]) -> tuple[Any, ...]:
    """Sort identities containing nullable integer suite IDs deterministically."""
    suite_id = value[0] if value else None
    return (
        suite_id is None,
        suite_id if suite_id is not None else 0,
        *tuple(str(item) for item in value[1:]),
    )


def _catalogs(
    entries: tuple[_Entry, ...],
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> Mapping[tuple[int | None, str, str, str], list[Mapping[str, Any]]]:
    values: dict[tuple[int | None, str, str, str], list[Mapping[str, Any]]] = (
        defaultdict(list)
    )
    for entry in entries:
        for version in _catalog_versions(entry):
            case = _case(entry, contexts=contexts)
            label, config = _config(entry, contexts=contexts)
            # Preserve a captured catalog even when it cannot yet be paired
            # across runs.  The comparison layer reports that identity gap;
            # export must never erase actual tools/list evidence.
            key = (
                _suite_value(getattr(entry.report.snapshot, "suite_id", None)),
                str(case) if case is not None else "<unknown>",
                config or "<unknown>",
                str(version["server"]),
            )
            enriched = dict(version)
            enriched["execution_id"] = _id(entry.report.snapshot.execution_id)
            enriched.update(_suite(entry))
            enriched["configuration_label"] = label
            enriched["catalog_fingerprint"] = sha256(
                _canonical(
                    {
                        "tools": version.get("tools", {}),
                        "complete": version.get("complete"),
                    }
                ).encode()
            ).hexdigest()[:16]
            values[key].append(enriched)
    return values


def _matched_catalogs(
    values: Mapping[tuple[int | None, str, str, str], list[Mapping[str, Any]]],
) -> Mapping[tuple[int | None, str, str, str], list[Mapping[str, Any]]]:
    return {
        key: catalogs for key, catalogs in values.items() if "<unknown>" not in key[1:3]
    }


def _interface_changes(
    old: tuple[_Entry, ...],
    new: tuple[_Entry, ...],
    old_contexts: Mapping[str, Mapping[str, Any]] | None = None,
    new_contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    before, after = (
        _matched_catalogs(_catalogs(old, old_contexts)),
        _matched_catalogs(_catalogs(new, new_contexts)),
    )
    changes: list[Mapping[str, Any]] = []
    for key in sorted(set(before) & set(after), key=_identity_sort_key):
        left_values, right_values = before[key], after[key]
        left_dist = defaultdict(list)
        right_dist = defaultdict(list)
        for value in left_values:
            left_dist[value["catalog_fingerprint"]].append(value)
        for value in right_values:
            right_dist[value["catalog_fingerprint"]].append(value)
        left_counts = {
            fingerprint: len(items) for fingerprint, items in sorted(left_dist.items())
        }
        right_counts = {
            fingerprint: len(items) for fingerprint, items in sorted(right_dist.items())
        }
        if left_counts == right_counts:
            continue
        # Only compare fields when both runs have one stable catalog shape.
        # Pairing arbitrary trials would manufacture tool removals/additions.
        if len(left_dist) == len(right_dist) == 1:
            left, right = (
                next(iter(left_dist.values()))[0],
                next(iter(right_dist.values()))[0],
            )
            left_tools, right_tools = left["tools"], right["tools"]
            for name in sorted(set(left_tools) | set(right_tools)):
                if left_tools.get(name) != right_tools.get(name):
                    changes.append(
                        {
                            "suite_id": key[0],
                            "case_id": key[1],
                            "configuration": key[2],
                            "server": key[3],
                            "suite_name": left.get("suite_name")
                            or right.get("suite_name"),
                            "tool": name,
                            "before": left_tools.get(name),
                            "after": right_tools.get(name),
                            "complete": bool(left["complete"] and right["complete"]),
                            "connection_before": left["connection"],
                            "connection_after": right["connection"],
                            "execution_ids_before": [
                                item["execution_id"]
                                for item in left_dist[next(iter(left_dist))]
                            ],
                            "execution_ids_after": [
                                item["execution_id"]
                                for item in right_dist[next(iter(right_dist))]
                            ],
                        }
                    )
            if left["complete"] != right["complete"]:
                changes.append(
                    {
                        "suite_id": key[0],
                        "case_id": key[1],
                        "configuration": key[2],
                        "server": key[3],
                        "kind": "catalog_completeness",
                        "before": left["complete"],
                        "after": right["complete"],
                    }
                )
        else:
            changes.append(
                {
                    "kind": "catalog_distribution",
                    "suite_id": key[0],
                    "case_id": key[1],
                    "configuration": key[2],
                    "server": key[3],
                    "comparable": False,
                    "before": {
                        "counts": left_counts,
                        "execution_ids": [item["execution_id"] for item in left_values],
                    },
                    "after": {
                        "counts": right_counts,
                        "execution_ids": [
                            item["execution_id"] for item in right_values
                        ],
                    },
                    "reason": "multiple observed catalog versions cannot be paired by trial",
                }
            )
    if set(before) != set(after):
        before_keys, after_keys = set(before), set(after)
        changes.append(
            {
                "kind": "catalog_coverage",
                "matched": len(before_keys & after_keys),
                "baseline": len(before),
                "current": len(after),
                "baseline_only": [
                    list(key)
                    for key in sorted(before_keys - after_keys, key=_identity_sort_key)
                ],
                "current_only": [
                    list(key)
                    for key in sorted(after_keys - before_keys, key=_identity_sort_key)
                ],
                "complete": False,
            }
        )
    return tuple(changes)


def _evaluation_entries(
    entries: tuple[_Entry, ...],
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[tuple[tuple[int | None, str, str, str], EvaluationRecord], ...]:
    values: list[tuple[tuple[int | None, str, str, str], EvaluationRecord]] = []
    for entry in entries:
        for record in _latest_evaluations(entry.report.evaluations).values():
            key = _record_comparison_key(entry, record, contexts)
            if key is None:
                continue
            values.append((key, record))
    return tuple(values)


def _record_comparison_key(
    entry: _Entry,
    record: EvaluationRecord,
    contexts: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[int | None, str, str, str] | None:
    """Return the comparison identity using record-level case metadata."""
    case = _case(entry, record, contexts)
    _, config = _config(entry, record, contexts)
    if case is None or config is None:
        return None
    suite_id = _suite_value(getattr(record, "suite_id", None))
    if suite_id is None:
        suite_id = _suite_value(getattr(entry.report.snapshot, "suite_id", None))
    return suite_id, str(case), record.name, config


def _required_comparison_keys(
    entries: Sequence[_Entry],
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[tuple[int | None, str, str, str], ...]:
    values: list[tuple[int | None, str, str, str]] = []
    for entry in entries:
        case = _case(entry, None, contexts)
        _, config = _config(entry, None, contexts)
        if case is None or config is None:
            continue
        suite_id = _suite_value(getattr(entry.report.snapshot, "suite_id", None))
        for name in sorted(_spec_required_names(entry)):
            if _latest_for_evaluator(entry, name):
                continue
            values.append((suite_id, str(case), name, config))
    return tuple(values)


def _key_requirement_stats(
    entries: Sequence[_Entry],
    key: tuple[int | None, str, str, str],
    contexts: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[int, int, int]:
    persisted_lineages: set[tuple[str, str | None, str, str | None, str | None]] = set()
    missing = pending = 0
    for entry in entries:
        latest = _latest_for_evaluator(entry, key[2])
        for record in latest:
            if _record_comparison_key(entry, record, contexts) == key:
                persisted_lineages.add(_evaluation_lineage(record))
        if latest or key[2] not in _required_names(entry):
            continue
        case = _case(entry, None, contexts)
        _, config = _config(entry, None, contexts)
        suite_id = _suite_value(getattr(entry.report.snapshot, "suite_id", None))
        if (
            key[2] in _spec_required_names(entry)
            and (suite_id, case, key[2], config) == key
        ):
            context = (
                contexts.get(_id(entry.report.snapshot.execution_id), {})
                if contexts is not None
                else {}
            )
            if _execution_is_running(entry) or context.get("running", False):
                pending += 1
            else:
                missing += 1
    return len(persisted_lineages) + missing, missing, pending


def _evaluation_changes(
    old: tuple[_Entry, ...],
    new: tuple[_Entry, ...],
    old_contexts: Mapping[str, Mapping[str, Any]] | None = None,
    new_contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    before: dict[tuple[int | None, str, str, str], list[EvaluationRecord]] = (
        defaultdict(list)
    )
    after: dict[tuple[int | None, str, str, str], list[EvaluationRecord]] = defaultdict(
        list
    )
    unmatched: list[Mapping[str, Any]] = []
    for key, record in _evaluation_entries(old, old_contexts):
        before[key].append(record)
    for key, record in _evaluation_entries(new, new_contexts):
        after[key].append(record)
    for key in _required_comparison_keys(old, old_contexts):
        before.setdefault(key, [])
    for key in _required_comparison_keys(new, new_contexts):
        after.setdefault(key, [])
    for entry in (*old, *new):
        for record in _latest_evaluations(entry.report.evaluations).values():
            contexts = old_contexts if entry in old else new_contexts
            case = _case(entry, record, contexts)
            _, config = _config(entry, record, contexts)
            if case is None or config is None:
                unmatched.append(
                    {
                        "execution_id": _id(record.execution_id),
                        "evaluator": record.name,
                        "suite_id": _suite_value(getattr(record, "suite_id", None))
                        or _suite_value(
                            getattr(entry.report.snapshot, "suite_id", None)
                        ),
                        "case_id": case,
                        "configuration": config,
                        "status": record.status.value,
                        "score": record.score,
                    }
                )
    changes: list[Mapping[str, Any]] = []
    old_by_execution = {_id(entry.report.snapshot.execution_id): entry for entry in old}
    new_by_execution = {_id(entry.report.snapshot.execution_id): entry for entry in new}
    for key in sorted(set(before) | set(after), key=_identity_sort_key):
        left = _latest_records_for(before.get(key, []))
        right = _latest_records_for(after.get(key, []))
        left_values = sorted(
            [(record.status.value, record.score) for record in left],
            key=lambda value: (
                value[0],
                value[1] is None,
                value[1] if value[1] is not None else 0.0,
            ),
        )
        right_values = sorted(
            [(record.status.value, record.score) for record in right],
            key=lambda value: (
                value[0],
                value[1] is None,
                value[1] if value[1] is not None else 0.0,
            ),
        )
        left_entries = [
            old_by_execution.get(_id(record.execution_id)) for record in left
        ]
        right_entries = [
            new_by_execution.get(_id(record.execution_id)) for record in right
        ]
        left_inputs = sorted(
            {
                _input_fingerprint(cast(_Entry, entry), record)
                for entry, record in zip(left_entries, left, strict=False)
            },
            key=lambda value: "" if value is None else value,
        )
        right_inputs = sorted(
            {
                _input_fingerprint(cast(_Entry, entry), record)
                for entry, record in zip(right_entries, right, strict=False)
            },
            key=lambda value: "" if value is None else value,
        )
        left_unknown = any(
            _unknown_callable(_input_value(cast(_Entry, entry), record))
            for entry, record in zip(left_entries, left, strict=False)
        )
        right_unknown = any(
            _unknown_callable(_input_value(cast(_Entry, entry), record))
            for entry, record in zip(right_entries, right, strict=False)
        )
        comparable = (
            left_inputs == right_inputs and not left_unknown and not right_unknown
        )
        left_expected, left_missing, left_pending = _key_requirement_stats(
            old, key, old_contexts
        )
        right_expected, right_missing, right_pending = _key_requirement_stats(
            new, key, new_contexts
        )
        left_stats = _evaluation_stats(
            left,
            expected_count=left_expected,
            missing_required_count=left_missing,
            pending_required_count=left_pending,
        )
        right_stats = _evaluation_stats(
            right,
            expected_count=right_expected,
            missing_required_count=right_missing,
            pending_required_count=right_pending,
        )
        left_labels = sorted(
            {
                label
                for entry, record in zip(left_entries, left, strict=False)
                if entry is not None
                for label in (_config(entry, record, old_contexts)[0],)
                if label is not None
            }
        )
        right_labels = sorted(
            {
                label
                for entry, record in zip(right_entries, right, strict=False)
                if entry is not None
                for label in (_config(entry, record, new_contexts)[0],)
                if label is not None
            }
        )
        changed_fields = []
        if left_inputs != right_inputs:
            changed_fields.append("expected_or_provenance")
        left_judges = {
            tuple(
                (key, getattr(record.provenance, key, None))
                for key in ("model", "rubric_id", "rubric_version", "config_digest")
            )
            for record in left
            if record.provenance is not None and record.provenance.kind == "llm_judge"
        }
        right_judges = {
            tuple(
                (key, getattr(record.provenance, key, None))
                for key in ("model", "rubric_id", "rubric_version", "config_digest")
            )
            for record in right
            if record.provenance is not None and record.provenance.kind == "llm_judge"
        }
        if left_judges != right_judges:
            changed_fields.append("judge_configuration")
        if left_unknown or right_unknown:
            changed_fields.append("predicate_implementation_unknown")
        if (
            left_values != right_values
            or not comparable
            or "judge_configuration" in changed_fields
            or left_stats["expected_count"] != right_stats["expected_count"]
            or left_stats["missing_required_count"]
            != right_stats["missing_required_count"]
            or left_stats["pending_required_count"]
            != right_stats["pending_required_count"]
        ):
            changes.append(
                {
                    "suite_id": key[0],
                    "case_id": key[1],
                    "evaluator": key[2],
                    "configuration": key[3],
                    "suite_name": (
                        _suite(left_entries[0], left[0]).get("suite_name")
                        if left_entries and left_entries[0] is not None
                        else (
                            _suite(right_entries[0], right[0]).get("suite_name")
                            if right_entries and right_entries[0] is not None
                            else None
                        )
                    ),
                    "configuration_label_before": left_labels,
                    "configuration_label_after": right_labels,
                    "comparable": comparable,
                    "changed_fields": changed_fields,
                    "before": {
                        "count": len(left),
                        "results": left_values,
                        "stats": left_stats,
                        "input_fingerprints": left_inputs,
                        "execution_ids": [_id(record.execution_id) for record in left],
                    },
                    "after": {
                        "count": len(right),
                        "results": right_values,
                        "stats": right_stats,
                        "input_fingerprints": right_inputs,
                        "execution_ids": [_id(record.execution_id) for record in right],
                    },
                    "delta": _stats_delta(
                        left_stats, right_stats, comparable=comparable
                    ),
                }
            )
    if unmatched:
        changes.append({"kind": "unmatched_evaluations", "results": unmatched})
    return tuple(changes)


def _test_values(
    store: ExecutionStore, run_id: str
) -> tuple[tuple[Mapping[str, Any], ...], Mapping[str, Any] | None]:
    getter = getattr(store, "get_test_run", None)
    lister = getattr(store, "list_test_results", None)
    manifest = getter(run_id) if callable(getter) else None
    results = tuple(lister(run_id)) if callable(lister) else ()
    return results, manifest


def _normalise_node_id(node_id: str, manifest: Mapping[str, Any] | None) -> str:
    """Remove run-specific checkout prefixes while retaining pytest identity."""
    if manifest is not None:
        selected = [str(value) for value in (manifest.get("selection") or ())]
        path, separator, suffix = node_id.partition("::")
        for candidate in sorted(selected, key=len, reverse=True):
            if (
                path == candidate
                or path.endswith("/" + candidate)
                or path.endswith("\\" + candidate)
            ):
                return candidate + (separator + suffix if separator else "")
    return node_id


def _contexts(
    results: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None = None,
) -> Mapping[str, Mapping[str, Any]]:
    values: dict[str, Mapping[str, Any]] = {}
    for result in results:
        raw_node_id = result.get("node_id")
        node_id = (
            _normalise_node_id(str(raw_node_id), manifest)
            if isinstance(raw_node_id, str)
            else raw_node_id
        )
        if not isinstance(node_id, str) or not node_id:
            continue
        for execution_id in result.get("execution_ids", ()) or ():
            values[str(execution_id)] = {
                "node_id": node_id,
                "attempt_id": result.get("attempt_id"),
                "running": str(result.get("outcome")) == "running",
            }
    return values


def _has_tool_error(entry: _Entry) -> bool:
    """Read tool results independently of a later terminal execution error."""
    report = entry.report
    if report.direct_result is not None and getattr(
        report.direct_result, "is_error", False
    ):
        return True
    for event in report.events:
        if event.kind.value != "tool.result_received":
            continue
        if (
            event.payload.get("isError") is True
            or event.payload.get("is_error") is True
        ):
            return True
        result = event.payload.get("result")
        if isinstance(result, Mapping) and (
            result.get("isError") is True or result.get("is_error") is True
        ):
            return True
    return False


def _tool_calls(entries: Sequence[_Entry]) -> Mapping[str, int]:
    """Summarize distinct linked execution traces without evaluator attribution."""

    total = successful = failed = 0
    for entry in entries:
        summary = getattr(entry.trace, "summary", None)
        if summary is None:
            continue
        total += int(getattr(summary, "tool_call_count", 0) or 0)
        successful += int(getattr(summary, "successful_tool_call_count", 0) or 0)
        failed += int(getattr(summary, "failed_tool_call_count", 0) or 0)
    return {"total": total, "successful": successful, "failed": failed}


def _result_kind(entry: _Entry) -> str | None:
    """Keep execution completion distinct from tool and protocol outcomes."""

    report = entry.report
    if report.error is not None:
        return report.error.code.value
    if _has_tool_error(entry):
        return "tool_error"
    if any(event.kind.value == "mcp.error" for event in report.events):
        return "protocol_error"
    return (
        report.snapshot.outcome.value if report.snapshot.outcome is not None else None
    )


def _test_verdict(
    test: Mapping[str, Any], execution_kinds: Mapping[str, str | None]
) -> str:
    outcome = str(test.get("outcome", "unknown"))
    if outcome == "error":
        phases = test.get("phases")
        if isinstance(phases, Mapping):
            setup = phases.get("setup")
            if isinstance(setup, Mapping) and setup.get("outcome") == "failed":
                return "setup_error"
            teardown = phases.get("teardown")
            if isinstance(teardown, Mapping) and teardown.get("outcome") == "failed":
                return "teardown_error"
        return "pytest_error"
    if outcome == "failed":
        phases = test.get("phases")
        call = phases.get("call") if isinstance(phases, Mapping) else None
        exception_type = (
            call.get("exception_type") if isinstance(call, Mapping) else None
        )
        exception_name = (
            exception_type.rsplit(".", 1)[-1]
            if isinstance(exception_type, str)
            else None
        )
        if exception_name == "AssertionError":
            return "failed_assertion"
        linked = {
            execution_kinds.get(str(execution_id))
            for execution_id in test.get("execution_ids", ()) or ()
        }
        linked_protocol = "protocol_error" in linked or "transport_error" in linked
        if exception_name in {"ProtocolError", "TransportError"} and linked_protocol:
            return "protocol_error"
        if exception_type is not None:
            return "pytest_error"
        if linked_protocol:
            return "protocol_error"
        return "failed_assertion"
    return outcome


def _spec_required_names(entry: _Entry) -> set[str]:
    """Return required evaluator names declared by this execution's spec."""
    spec = entry.spec
    registrations = getattr(spec, "evaluations", ()) if spec is not None else ()
    return {
        str(registration.name)
        for registration in registrations or ()
        if bool(getattr(registration, "required", False))
    }


def _execution_is_running(entry: _Entry) -> bool:
    """Treat every non-finished execution lifecycle as still live."""

    lifecycle = getattr(entry.report.snapshot, "lifecycle", None)
    return getattr(lifecycle, "value", lifecycle) != "finished"


def _required_lineages(
    entry: _Entry,
) -> set[tuple[str, str | None, str, str | None, str | None]]:
    """Dynamic requirements apply only to the exact persisted lineage."""
    return _required_evaluation_lineages(entry.report.evaluations)


def _required_names(entry: _Entry) -> set[str]:
    return _spec_required_names(entry) | {
        lineage[2] for lineage in _required_lineages(entry)
    }


def _record_is_required(entry: _Entry, record: EvaluationRecord) -> bool:
    return record.name in _spec_required_names(entry) or _evaluation_lineage(
        record
    ) in _required_lineages(entry)


def _latest_for_evaluator(entry: _Entry, evaluator: str) -> list[EvaluationRecord]:
    return [
        record
        for lineage, record in _latest_evaluations(entry.report.evaluations).items()
        if lineage[2] == evaluator
    ]


def _latest_records_for(
    records: Sequence[EvaluationRecord],
) -> tuple[EvaluationRecord, ...]:
    latest = _latest_evaluations(records)
    return tuple(
        sorted(
            latest.values(),
            key=lambda record: (
                record.created_at,
                _id(record.evaluation_id),
            ),
        )
    )


def _evaluation_projection(
    entry: _Entry,
    record: EvaluationRecord,
    contexts: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "kind": "evaluation",
        "evaluation_id": _id(record.evaluation_id),
        "execution_id": _id(record.execution_id),
        "evaluator": record.name,
        "case_id": _case(entry, record, contexts),
        "status": record.status.value,
        "required": _record_is_required(entry, record),
        "score": record.score,
        "rationale": record.rationale,
        "message": record.message,
        "details": dict(record.details),
    }


def _detached_evaluation_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project persisted required evidence without an execution identity."""
    status = str(value.get("status") or "error")
    evaluator = str(value.get("name") or value.get("evaluator") or "<unknown>")
    details = value.get("details")
    metrics = value.get("metrics")
    return {
        "kind": "evaluation",
        "evaluation_id": value.get("evaluation_id"),
        "execution_id": None,
        "evaluator": evaluator,
        "status": status,
        "required": bool(value.get("required", False)),
        "score": value.get("score"),
        "rationale": value.get("rationale"),
        "message": value.get("message"),
        "metrics": dict(metrics) if isinstance(metrics, Mapping) else {},
        "details": dict(details) if isinstance(details, Mapping) else {},
    }


def _pair_completeness(
    entry: _Entry, evaluator: str, *, running: bool
) -> tuple[str, Mapping[str, Any] | None]:
    """Classify one distinct (execution_id, evaluator) evidence identity."""
    spec_required = evaluator in _spec_required_names(entry)
    latest_records = _latest_for_evaluator(entry, evaluator)
    if not spec_required:
        latest_records = [
            record
            for record in latest_records
            if _evaluation_lineage(record) in _required_lineages(entry)
        ]
    execution_id = _id(entry.report.snapshot.execution_id)
    base = {"execution_id": execution_id, "evaluator": evaluator}
    if not latest_records:
        if running:
            return "incomplete", {"kind": "pending_evaluation", **base}
        return "incomplete", {"kind": "missing_evaluation", **base}
    failed = next(
        (record for record in latest_records if record.status.value == "failed"),
        None,
    )
    if failed is not None:
        return "complete", {
            "kind": "failed_evaluation",
            "evaluation_id": _id(failed.evaluation_id),
            **base,
            "status": failed.status.value,
        }
    incomplete = next(
        (
            record
            for record in latest_records
            if _required_status_blocks(record.status)
            and record.status.value != "failed"
        ),
        None,
    )
    if incomplete is not None:
        return "incomplete", {
            "kind": "incomplete_evaluation",
            "evaluation_id": _id(incomplete.evaluation_id),
            **base,
            "status": incomplete.status.value,
        }
    return "complete", None


def _evaluation_evidence(
    entries: Sequence[_Entry],
    execution_ids: Sequence[Any],
    contexts: Mapping[str, Mapping[str, Any]] | None,
    *,
    manifest_only: bool = False,
    attempt_running: bool = False,
    detached_evaluations: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any], str, list[Mapping[str, Any]]]:
    """Project evaluations and required-pair completeness for one test."""
    selected = {str(getattr(value, "root", value)) for value in execution_ids}
    linked = [
        entry
        for entry in entries
        if _id(entry.report.snapshot.execution_id) in selected
    ]
    evaluations: list[Mapping[str, Any]] = []
    completeness: list[Mapping[str, Any]] = []
    reasons: list[Mapping[str, Any]] = []
    required_states: list[str] = []
    missing_required: list[Mapping[str, Any]] = []
    pending_required: list[Mapping[str, Any]] = []
    incomplete_required: list[Mapping[str, Any]] = []
    complete_pair_count = 0
    for entry in linked:
        for record in _latest_evaluations(entry.report.evaluations).values():
            evaluations.append(_evaluation_projection(entry, record, contexts))
        running = attempt_running or _execution_is_running(entry)
        for evaluator in sorted(_required_names(entry)):
            status, reason = _pair_completeness(entry, evaluator, running=running)
            pair = {
                "execution_id": _id(entry.report.snapshot.execution_id),
                "evaluator": evaluator,
            }
            completeness.append(pair)
            if status == "complete":
                complete_pair_count += 1
            required_states.append(
                "failed"
                if reason is not None and reason.get("kind") == "failed_evaluation"
                else "incomplete"
                if status != "complete"
                else "complete"
            )
            if reason is not None:
                reasons.append(reason)
                if reason.get("kind") == "missing_evaluation":
                    missing_required.append(pair)
                elif reason.get("kind") == "pending_evaluation":
                    pending_required.append(pair)
                elif reason.get("kind") == "incomplete_evaluation":
                    incomplete_required.append(reason)
    for detached in detached_evaluations:
        if not bool(detached.get("required", False)):
            continue
        evaluation = _detached_evaluation_projection(detached)
        evaluations.append(evaluation)
        status = str(evaluation["status"])
        identity = {
            "evaluation_id": evaluation["evaluation_id"],
            "execution_id": None,
            "evaluator": evaluation["evaluator"],
            "status": status,
        }
        if status == "failed":
            required_states.append("failed")
            reasons.append({"kind": "failed_evaluation", **identity})
        elif _required_status_blocks(status) or status != "passed":
            required_states.append("incomplete")
            reason = {"kind": "incomplete_evaluation", **identity}
            reasons.append(reason)
            incomplete_required.append(reason)
        else:
            required_states.append("complete")
    if manifest_only:
        return (
            evaluations,
            {
                "status": "not_applicable",
                "required_pair_count": 0,
                "complete_pair_count": 0,
                "incomplete_pair_count": 0,
                "missing_required_count": 0,
                "pending_required_count": 0,
                "missing_required": [],
                "pending_required": [],
                "incomplete_required": [],
            },
            "incomplete",
            [{"kind": "pytest", "reason": "test_not_run"}],
        )
    if any(state == "failed" for state in required_states):
        required_state = "failed"
    elif any(state == "incomplete" for state in required_states):
        required_state = "incomplete"
    elif required_states:
        required_state = "passed"
    else:
        required_state = "none"
    if not completeness:
        completeness_status = "not_applicable"
    elif len(completeness) == complete_pair_count:
        completeness_status = "complete"
    else:
        completeness_status = "incomplete"
    return (
        evaluations,
        {
            "status": completeness_status,
            "required_pair_count": len(completeness),
            "complete_pair_count": complete_pair_count,
            "incomplete_pair_count": len(completeness) - complete_pair_count,
            "missing_required_count": len(missing_required),
            "pending_required_count": len(pending_required),
            "missing_required": missing_required,
            "pending_required": pending_required,
            "incomplete_required": incomplete_required,
        },
        required_state,
        reasons,
    )


def _xfail_state(test: Mapping[str, Any]) -> tuple[bool, bool]:
    phases = test.get("phases")
    if not isinstance(phases, Mapping):
        return False, False
    call = phases.get("call")
    if not isinstance(call, Mapping) or not call.get("wasxfail"):
        return False, False
    # A failed call with wasxfail is strict XPASS; a skipped call is the
    # ordinary expected-failure path.  The latter is the only waiver case.
    return call.get("outcome") == "skipped", call.get("outcome") == "passed"


def _effective_verdict(
    test: Mapping[str, Any],
    required_state: str,
    *,
    valid_xfail: bool,
    running: bool,
) -> str:
    outcome = str(test.get("outcome", "unknown"))
    if running or outcome == "running":
        return "pending"
    if outcome == "not_run":
        return "incomplete"
    xfail, xpass = _xfail_state(test)
    if xfail and valid_xfail:
        return "skipped"
    if xpass:
        if required_state == "failed":
            return "failed"
        return "incomplete" if required_state == "incomplete" else "passed"
    if outcome in {"error", "unknown"}:
        return "incomplete"
    if outcome == "failed":
        # A valid strict XPASS remains an ordinary pytest failure.
        return "failed"
    if required_state == "failed":
        return "failed"
    if required_state == "incomplete":
        return "incomplete"
    if outcome == "skipped":
        return "skipped"
    return "passed"


def _attempt_effective_verdict(
    value: Mapping[str, Any],
    entries: Sequence[_Entry],
    manifest: Mapping[str, Any] | None,
) -> str:
    return str(
        project_test_attempt(
            value,
            entries,
            manifest=manifest,
            contexts=_contexts((value,), manifest),
        )["effective_verdict"]
    )


def project_test_attempt(
    value: Mapping[str, Any],
    entries: Sequence[_Entry],
    *,
    manifest: Mapping[str, Any] | None = None,
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
    execution_kinds: Mapping[str, str | None] | None = None,
) -> Mapping[str, Any]:
    """Project one raw pytest attempt with independent and effective verdicts.

    This small entry point is also suitable for services that already have a
    linked set of execution entries and need the same contract as feedback.
    """
    raw = dict(value)
    execution_ids = raw.get("execution_ids", ()) or ()
    linked_entries = [
        entry
        for entry in entries
        if _id(entry.report.snapshot.execution_id)
        in {str(getattr(item, "root", item)) for item in execution_ids}
    ]
    attempt_running = str(raw.get("outcome")) == "running"
    manifest_only = str(raw.get("outcome")) == "not_run" and not execution_ids
    projected_evaluations, completeness, required_state, reasons = _evaluation_evidence(
        entries,
        execution_ids,
        contexts if contexts is not None else _contexts((raw,), manifest),
        manifest_only=manifest_only,
        attempt_running=attempt_running,
        detached_evaluations=(
            raw.get("detached_evaluations", ())
            if isinstance(raw.get("detached_evaluations", ()), (list, tuple))
            else ()
        ),
    )
    collection_error = bool(
        manifest
        and (
            manifest.get("persistence_error")
            or any(
                isinstance(report, Mapping) and report.get("outcome") == "failed"
                for report in manifest.get("collection_reports", ()) or ()
            )
        )
    )
    xfail, _ = _xfail_state(raw)
    projected: dict[str, Any] = {
        **raw,
        "verdict": _test_verdict(raw, execution_kinds or {}),
        "effective_verdict": _effective_verdict(
            raw,
            required_state,
            valid_xfail=(
                xfail
                and _xfail_waives_required_evaluations(raw)
                and not collection_error
            ),
            running=(
                attempt_running or completeness.get("pending_required_count", 0) > 0
            ),
        ),
        "evaluations": projected_evaluations,
        "evaluation_completeness": completeness,
        "evaluation_reasons": reasons,
        "tool_calls": _tool_calls(linked_entries),
    }
    if (
        execution_ids
        and projected.get("suite_id") is None
        and projected.get("suite_name") is None
    ):
        first_execution_id = str(getattr(execution_ids[0], "root", execution_ids[0]))
        first_entry = next(
            (
                entry
                for entry in linked_entries
                if _id(entry.report.snapshot.execution_id) == first_execution_id
            ),
            None,
        )
        if first_entry is not None:
            projected.update(_suite(first_entry))
    projected["tool_result"] = (
        "tool_error"
        if any(_has_tool_error(entry) for entry in linked_entries)
        else None
    )
    return projected


def project_test_attempts(
    store: ExecutionStore,
    run_id: RunId | str,
) -> tuple[Mapping[str, Any], ...]:
    """Return projected pytest attempts for a run without building full feedback."""
    normalized_run_id = _run_key(run_id)
    if normalized_run_id is None:
        raise ValueError("run_id is required")
    entries = _entries(store, normalized_run_id)
    results, manifest = _test_values(store, normalized_run_id)
    contexts = _contexts(results, manifest)
    execution_kinds = {
        _id(entry.report.snapshot.execution_id): _result_kind(entry)
        for entry in entries
    }
    return tuple(
        project_test_attempt(
            result,
            entries,
            manifest=manifest,
            contexts=contexts,
            execution_kinds=execution_kinds,
        )
        for result in results
    )


def _failure_values(
    entries: tuple[_Entry, ...],
    tests: Sequence[Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    values: list[dict[str, Any]] = [
        dict(value)
        for value in tests
        if str(value.get("outcome")) in {"failed", "error"}
    ]
    failed_by_execution: dict[str, list[int]] = defaultdict(list)
    for index, value in enumerate(values):
        for execution_id in value.get("execution_ids", ()) or ():
            failed_by_execution[str(execution_id)].append(index)
    for entry in entries:
        for record in _latest_evaluations(entry.report.evaluations).values():
            if _required_status_blocks(record.status):
                evaluation = _evaluation_projection(entry, record, contexts)
                matches = failed_by_execution.get(_id(record.execution_id), [])
                if len(matches) == 1:
                    projected = values[matches[0]].setdefault("evaluations", [])
                    if not any(
                        item.get("evaluation_id") == evaluation.get("evaluation_id")
                        for item in projected
                        if isinstance(item, Mapping)
                    ):
                        projected.append(evaluation)
                else:
                    values.append(evaluation)
    for test_value in tests:
        for detached_evaluation in test_value.get("evaluations", ()) or ():
            if not isinstance(detached_evaluation, Mapping):
                continue
            if detached_evaluation.get("execution_id") is not None:
                continue
            if not _required_status_blocks(detached_evaluation.get("status")):
                continue
            if not any(
                existing.get("evaluation_id")
                == detached_evaluation.get("evaluation_id")
                for existing in values
                if isinstance(existing, Mapping)
            ):
                values.append(dict(detached_evaluation))
    return tuple(values)


def _manifest_failures(
    manifest: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    if manifest is None:
        return ()
    values: list[Mapping[str, Any]] = []
    for report in manifest.get("collection_reports", ()) or ():
        if isinstance(report, Mapping) and report.get("outcome") == "failed":
            values.append({"kind": "collection", **dict(report)})
    for error in manifest.get("worker_errors", ()) or ():
        if isinstance(error, Mapping):
            values.append({"kind": "worker", **dict(error)})
    if manifest.get("persistence_error"):
        values.append(
            {
                "kind": "persistence",
                "message": "one or more test manifest writes failed",
            }
        )
    return tuple(values)


def _stats(
    entries: tuple[_Entry, ...],
    *,
    running_execution_ids: Collection[str] = (),
) -> Mapping[str, Any]:
    names = sorted(
        {record.name for entry in entries for record in entry.report.evaluations}
        | {name for entry in entries for name in _spec_required_names(entry)}
    )
    values: dict[str, Any] = {}
    for name in names:
        records = [
            record
            for entry in entries
            for record in entry.report.evaluations
            if record.name == name
        ]
        expected = len(_latest_evaluations(records))
        missing = 0
        pending = 0
        for entry in entries:
            if name not in _required_names(entry):
                continue
            latest = _latest_for_evaluator(entry, name)
            if name in _spec_required_names(entry) and not latest:
                execution_id = _id(entry.report.snapshot.execution_id)
                if (
                    _execution_is_running(entry)
                    or execution_id in running_execution_ids
                ):
                    pending += 1
                else:
                    expected += 1
                    missing += 1
        values[name] = _evaluation_stats(
            records,
            expected_count=expected,
            missing_required_count=missing,
            pending_required_count=pending,
        )
    return values


def build_feedback(
    store: ExecutionStore,
    run_id: RunId | str,
    *,
    baseline_run_id: RunId | str | None = None,
) -> Feedback:
    current_id = _run_key(run_id)
    if current_id is None:
        raise ValueError("run_id is required")
    current = _entries(store, current_id)
    results, manifest = _test_values(store, current_id)
    project_id = (
        str(manifest.get("project_id"))
        if manifest and manifest.get("project_id")
        else None
    )
    if project_id is None and current:
        project_value = current[0].report.snapshot.project_id
        project_id = project_value.root if project_value is not None else None
    get_project = getattr(store, "get_project", None)
    project = get_project(project_id) if project_id and callable(get_project) else None
    current_contexts = _contexts(results, manifest)
    limitations: list[str] = []
    if not current and not results:
        limitations.append("no executions or test results were recorded for this run")
    if manifest is not None:
        if any(
            isinstance(report, Mapping) and report.get("outcome") == "failed"
            for report in manifest.get("collection_reports", ()) or ()
        ):
            limitations.append("pytest collection reported one or more errors")
        if manifest.get("worker_errors"):
            limitations.append("one or more test workers ended with errors")
        if manifest.get("persistence_error"):
            limitations.append("some test manifest data could not be persisted")
        if manifest.get("not_run_node_ids"):
            limitations.append("some collected tests did not produce an attempt")
    execution_kinds = {
        _id(entry.report.snapshot.execution_id): _result_kind(entry)
        for entry in current
    }
    tool_error_ids = {
        _id(entry.report.snapshot.execution_id)
        for entry in current
        if _has_tool_error(entry)
    }
    tests = tuple(
        project_test_attempt(
            value,
            current,
            manifest=manifest,
            contexts=current_contexts,
            execution_kinds=execution_kinds,
        )
        for value in results
    )
    failures = _failure_values(current, tests, current_contexts) + _manifest_failures(
        manifest
    )
    executions = tuple(
        {
            "execution_id": _id(entry.report.snapshot.execution_id),
            **_suite(entry),
            "outcome": entry.report.snapshot.outcome.value
            if entry.report.snapshot.outcome is not None
            else None,
            "result_kind": execution_kinds[_id(entry.report.snapshot.execution_id)],
            "tool_result": (
                "tool_error"
                if _id(entry.report.snapshot.execution_id) in tool_error_ids
                else None
            ),
            "lifecycle": entry.report.snapshot.lifecycle.value,
            "event_count": entry.report.event_count,
            "events_truncated": entry.report.events_truncated,
            "evaluation_count": len(entry.report.evaluations),
            "evidence": None
            if entry.report.evidence is None
            else entry.report.evidence.model_dump(mode="json"),
        }
        for entry in current
    )
    comparison = None
    if baseline_run_id is not None:
        baseline_id = _run_key(baseline_run_id)
        assert baseline_id is not None
        baseline = _entries(store, baseline_id)
        baseline_results, baseline_manifest = _test_values(store, baseline_id)
        baseline_contexts = _contexts(baseline_results, baseline_manifest)
        baseline_execution_kinds = {
            _id(entry.report.snapshot.execution_id): _result_kind(entry)
            for entry in baseline
        }
        baseline_tests = tuple(
            project_test_attempt(
                value,
                baseline,
                manifest=baseline_manifest,
                contexts=baseline_contexts,
                execution_kinds=baseline_execution_kinds,
            )
            for value in baseline_results
        )

        def outcomes(
            values: Sequence[Mapping[str, Any]],
            manifest_value: Mapping[str, Any] | None,
            entries_value: Sequence[_Entry],
        ) -> dict[tuple[int | None, str], list[Mapping[str, Any]]]:
            grouped: dict[tuple[int | None, str], list[Mapping[str, Any]]] = (
                defaultdict(list)
            )
            suite_lookup = {
                _id(entry.report.snapshot.execution_id): _suite_value(
                    getattr(entry.report.snapshot, "suite_id", None)
                )
                for entry in entries_value
            }
            for value in values:
                if value.get("node_id") is not None:
                    node_id = _normalise_node_id(str(value["node_id"]), manifest_value)
                    suite_id = _suite_value(value.get("suite_id"))
                    if suite_id is None and value.get("execution_ids"):
                        suite_id = suite_lookup.get(str(value["execution_ids"][0]))
                    grouped[(suite_id, node_id)].append(
                        {
                            "outcome": value.get("outcome"),
                            "effective_verdict": _attempt_effective_verdict(
                                value, entries_value, manifest_value
                            ),
                        }
                    )
            for grouped_key in grouped:
                grouped[grouped_key].sort(key=lambda value: str(value.get("outcome")))
            return grouped

        left_outcomes, right_outcomes = (
            outcomes(baseline_results, baseline_manifest, baseline),
            outcomes(results, manifest, current),
        )
        test_changes = tuple(
            {
                "suite_id": key[0],
                "node_id": key[1],
                "baseline": left_outcomes.get(key, []),
                "current": right_outcomes.get(key, []),
            }
            for key in sorted(
                set(left_outcomes) | set(right_outcomes), key=_identity_sort_key
            )
            if _canonical(left_outcomes.get(key, []))
            != _canonical(right_outcomes.get(key, []))
        )
        if not baseline and not baseline_results:
            limitations.append("baseline run has no executions or test results")
        before_observed, after_observed = (
            _catalogs(baseline, baseline_contexts),
            _catalogs(current, current_contexts),
        )
        before_catalogs, after_catalogs = (
            _matched_catalogs(before_observed),
            _matched_catalogs(after_observed),
        )
        evaluation_changes = _evaluation_changes(
            baseline, current, baseline_contexts, current_contexts
        )
        # This is a real limitation only when the persisted records actually
        # lack the identity needed for matching.  Direct-client evaluations
        # commonly have no ExecutionSpec, but the pytest attempt context can
        # still provide a stable case/configuration identity; warning in that
        # normal path makes the agent distrust an otherwise valid comparison.
        unmatched_evaluations = next(
            (
                item
                for item in evaluation_changes
                if item.get("kind") == "unmatched_evaluations"
            ),
            None,
        )
        comparison_limitations: list[str] = []
        if any(
            "judge_configuration" in item.get("changed_fields", ())
            for item in evaluation_changes
        ):
            comparison_limitations.append(
                "judge model, rubric, prompt version, or configuration digest changed; pass-rate comparison is not like-for-like"
            )
        if unmatched_evaluations is not None:
            count = len(unmatched_evaluations.get("results", ()))
            comparison_limitations.append(
                f"{count} evaluation record{'s' if count != 1 else ''} "
                "without case/configuration identity were not matched"
            )
        if (
            before_catalogs
            and after_catalogs
            and not set(before_catalogs) & set(after_catalogs)
        ):
            comparison_limitations.append(
                "baseline and current catalogs have no shared case/configuration/server identity"
            )
        if not before_observed:
            comparison_limitations.append("baseline run has no observed tool catalog")
        elif not before_catalogs:
            comparison_limitations.append(
                "baseline tool catalogs were captured but scenario identity was unavailable"
            )
        if not after_observed:
            comparison_limitations.append("current run has no observed tool catalog")
        elif not after_catalogs:
            comparison_limitations.append(
                "current tool catalogs were captured but scenario identity was unavailable"
            )
        baseline_failures = _failure_values(
            baseline, baseline_tests, baseline_contexts
        ) + _manifest_failures(baseline_manifest)
        comparison = Comparison(
            baseline_run_id=baseline_id,
            current_run_id=current_id,
            baseline_suites=_suites(baseline, baseline_results),
            current_suites=_suites(current, results),
            interface_changes=_interface_changes(
                baseline, current, baseline_contexts, current_contexts
            ),
            test_changes=test_changes,
            evaluation_changes=evaluation_changes,
            failures=tuple(
                {"source": "baseline", **value} for value in baseline_failures
            )
            + tuple({"source": "current", **value} for value in failures),
            coverage={
                "baseline_executions": len(baseline),
                "current_executions": len(current),
                "baseline_tests": len(baseline_results),
                "current_tests": len(results),
            },
            limitations=tuple(comparison_limitations),
        )
    test_counts = {
        outcome: sum(test.get("outcome") == outcome for test in tests)
        for outcome in ("passed", "failed", "error", "skipped", "not_run")
    }
    effective_counts = {
        verdict: sum(test.get("effective_verdict") == verdict for test in tests)
        for verdict in ("pending", "passed", "failed", "incomplete", "skipped")
    }
    not_run_tests = tuple(
        dict.fromkeys(
            [
                str(test.get("node_id"))
                for test in tests
                if str(test.get("outcome")) == "not_run"
            ]
            + [
                _normalise_node_id(str(node_id), manifest)
                for node_id in (
                    manifest.get("not_run_node_ids", ()) if manifest else ()
                )
            ]
        )
    )
    collection_errors = (
        sum(
            isinstance(report, Mapping) and report.get("outcome") == "failed"
            for report in manifest.get("collection_reports", ()) or ()
        )
        if manifest
        else 0
    )
    summary: dict[str, Any] = {
        "executions": len(current),
        "tests": len(tests),
        "passed_tests": test_counts["passed"],
        "failed_tests": test_counts["failed"],
        "error_tests": test_counts["error"],
        "skipped_tests": test_counts["skipped"],
        "test_outcome_counts": dict(test_counts),
        "effective_verdict_counts": effective_counts,
        "not_run_tests": not_run_tests,
        "collection_errors": collection_errors,
        "failures": test_counts["failed"] + test_counts["error"] + collection_errors,
        "terminal_executions": sum(
            1 for entry in current if entry.report.snapshot.outcome is not None
        ),
    }
    if manifest is not None:
        summary["run_status"] = manifest.get("status")
    running_execution_ids = {
        str(execution_id)
        for result in results
        if str(result.get("outcome")) == "running"
        for execution_id in (result.get("execution_ids", ()) or ())
    }
    return Feedback(
        run_id=current_id,
        project_id=project_id,
        project_name=project[1] if project else None,
        suites=_suites(current, results),
        tests=tests,
        executions=executions,
        failures=failures,
        evaluation_stats=_stats(
            current,
            running_execution_ids=running_execution_ids,
        ),
        summary=summary,
        limitations=tuple(limitations),
        comparison=comparison,
    )


def export_feedback(
    feedback: Feedback, store: ExecutionStore, directory: str | os.PathLike[str]
) -> Path:
    """Write a complete feedback bundle with resolvable supporting files."""
    entries = list(_entries(store, feedback.run_id))
    if feedback.comparison is not None:
        entries.extend(_entries(store, feedback.comparison.baseline_run_id))
    reports = {_id(entry.report.snapshot.execution_id): entry for entry in entries}
    execution_suites = {key: _suite(entry) for key, entry in reports.items()}
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    for name in (
        "executions",
        "specs",
        "catalogs",
        "traces",
        "diagnostics",
        "evidence",
        "artifacts",
    ):
        (root / name).mkdir(exist_ok=True)
    execution_files: dict[str, str] = {}
    spec_files: dict[str, str] = {}
    catalog_files: dict[str, str] = {}
    trace_files: dict[str, str] = {}
    evidence_files: dict[str, str] = {}
    artifact_files: dict[str, str] = {}
    diagnostic_files: dict[str, str] = {}
    test_run_files: dict[str, str] = {}
    test_result_files: dict[str, str] = {}
    unavailable: list[Mapping[str, Any]] = []
    for execution_id, entry in reports.items():
        execution_name = _safe_filename(execution_id, ".json")
        (root / "executions" / execution_name).write_text(
            json.dumps(
                _jsonable(entry.report.model_dump(mode="json")),
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        execution_files[execution_id] = f"executions/{execution_name}"
        if entry.spec is not None:
            spec_name = _safe_filename(execution_id, ".json")
            (root / "specs" / spec_name).write_text(
                json.dumps(
                    _jsonable(entry.spec.model_dump(mode="json")),
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            spec_files[execution_id] = f"specs/{spec_name}"
        versions = _catalog_versions(entry)
        if versions:
            catalog_name = _safe_filename(execution_id, ".json")
            (root / "catalogs" / catalog_name).write_text(
                json.dumps(
                    _jsonable({"execution_id": execution_id, "versions": versions}),
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            catalog_files[execution_id] = f"catalogs/{catalog_name}"
        get_trace_view = getattr(store, "get_trace_view", None)
        if callable(get_trace_view):
            try:
                trace = get_trace_view(entry.report.snapshot.execution_id)
                if trace is not None:
                    trace_name = _safe_filename(execution_id, ".json")
                    (root / "traces" / trace_name).write_text(
                        json.dumps(
                            _jsonable(trace.model_dump(mode="json")),
                            sort_keys=True,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    trace_files[execution_id] = f"traces/{trace_name}"
            except Exception:
                unavailable.append(
                    {
                        "execution_id": execution_id,
                        "kind": "trace",
                        "reason": "trace view unavailable",
                    }
                )
        for event in entry.report.events:
            if event.raw_evidence_ref is not None:
                try:
                    size = event.raw_evidence_ref.size_bytes
                    evidence = store.read_raw_evidence(
                        event.raw_evidence_ref,
                        max_bytes=size if size is not None else 1_048_576,
                    )
                    if evidence.truncated:
                        unavailable.append(
                            {
                                "execution_id": execution_id,
                                "evidence_id": event.raw_evidence_ref.evidence_id,
                                "reason": "capture is truncated",
                            }
                        )
                        continue
                    evidence_name = _safe_filename(
                        event.raw_evidence_ref.evidence_id, ".json"
                    )
                    (root / "evidence" / evidence_name).write_text(
                        json.dumps(
                            _jsonable(evidence.model_dump(mode="json")),
                            sort_keys=True,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    evidence_files[event.raw_evidence_ref.evidence_id] = (
                        f"evidence/{evidence_name}"
                    )
                except Exception:
                    unavailable.append(
                        {
                            "execution_id": execution_id,
                            "evidence_id": event.raw_evidence_ref.evidence_id,
                            "reason": "evidence unavailable",
                        }
                    )
        artifact_store = getattr(store, "artifacts", None)
        artifact_get = getattr(artifact_store, "get", None)
        for artifact in entry.report.artifacts:
            if callable(artifact_get):
                try:
                    artifact_name = _safe_filename(artifact.artifact_id.root, ".bin")
                    artifact_path = root / "artifacts" / artifact_name
                    artifact_path.write_bytes(artifact_get(artifact))
                    artifact_files[artifact.artifact_id.root] = (
                        f"artifacts/{artifact_name}"
                    )
                except Exception:
                    unavailable.append(
                        {
                            "execution_id": execution_id,
                            "artifact_id": artifact.artifact_id.root,
                            "reason": "artifact unavailable",
                        }
                    )
            else:
                unavailable.append(
                    {
                        "execution_id": execution_id,
                        "artifact_id": artifact.artifact_id.root,
                        "reason": "artifact store unavailable",
                    }
                )
    manifest_get = getattr(store, "get_test_run", None)
    result_list = getattr(store, "list_test_results", None)
    run_ids = [feedback.run_id] + (
        [feedback.comparison.baseline_run_id] if feedback.comparison is not None else []
    )
    for report_run_id in run_ids:
        if callable(manifest_get):
            test_manifest = manifest_get(report_run_id)
            if test_manifest is not None:
                manifest_name = _safe_filename(report_run_id, ".json")
                (root / "diagnostics" / manifest_name).write_text(
                    json.dumps(_jsonable(test_manifest), sort_keys=True, indent=2)
                    + "\n",
                    encoding="utf-8",
                )
                test_run_files[report_run_id] = f"diagnostics/{manifest_name}"
        values = tuple(result_list(report_run_id)) if callable(result_list) else ()
        for test in values:
            attempt_id = test.get("attempt_id")
            if not isinstance(attempt_id, str):
                continue
            diagnostic_name = _safe_filename(attempt_id, ".json")
            test_payload = dict(test)
            execution_ids = test_payload.get("execution_ids") or ()
            if (
                execution_ids
                and test_payload.get("suite_id") is None
                and test_payload.get("suite_name") is None
            ):
                test_payload.update(execution_suites.get(str(execution_ids[0]), {}))
            (root / "diagnostics" / diagnostic_name).write_text(
                json.dumps(_jsonable(test_payload), sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            test_result_files[attempt_id] = f"diagnostics/{diagnostic_name}"
    for test in feedback.tests:
        attempt_id = test.get("attempt_id")
        diagnostics = test.get("diagnostics")
        if isinstance(attempt_id, str) and diagnostics:
            filename = _safe_filename(attempt_id, ".json")
            (root / "diagnostics" / filename).write_text(
                json.dumps(_jsonable(diagnostics), sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            diagnostic_files[attempt_id] = f"diagnostics/{filename}"
    payload = _jsonable(feedback.model_dump(mode="json"))
    payload.update(
        {
            "execution_files": execution_files,
            "spec_files": spec_files,
            "catalog_files": catalog_files,
            "trace_files": trace_files,
            "evidence_files": evidence_files,
            "artifact_files": artifact_files,
            "diagnostic_files": diagnostic_files,
            "test_run_files": test_run_files,
            "test_result_files": test_result_files,
            "unavailable_references": unavailable,
        }
    )
    target = root / "feedback.json"
    staged = root / ".feedback.json.tmp"
    staged.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(staged, target)
    return target


__all__ = [
    "Comparison",
    "Feedback",
    "build_feedback",
    "export_feedback",
    "project_test_attempt",
    "project_test_attempts",
]
