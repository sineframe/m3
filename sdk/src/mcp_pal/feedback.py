"""Deterministic feedback for coding agents improving MCP servers.

This module reads persisted MCP Pal data. It never runs evaluators, parses
stdout, or invents a verdict when the recorded evidence is incomplete.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from .aggregations import EvaluationQuery
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
    "mcp_pal.matrix.trial",
    "mcp_pal.matrix.trial_count",
    "mcp_pal.worker_id",
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
                result.append(
                    _Entry(
                        report,
                        get_spec(snapshot.execution_id) if callable(get_spec) else None,
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
        or metadata.get("mcp_pal.matrix.case_id")
        or metadata.get("mcp_pal.matrix.cell_id")
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
    label = (
        metadata.get("harness_config")
        or metadata.get("mcp_pal.matrix.harness")
        or metadata.get("model")
    )
    if label is None and entry.spec is not None:
        harness = getattr(entry.spec, "harness", None)
        label = getattr(harness, "model", None)
        if label is None and harness is not None:
            label = type(harness).__name__
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
    spec_metadata = dict(dumped.get("metadata") or {})
    for key in (
        *_OBSERVATIONAL_METADATA,
        "mcp_pal.matrix.cell_id",
        "mcp_pal.matrix.case_id",
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


def _evaluation_stats(records: Sequence[EvaluationRecord]) -> Mapping[str, Any]:
    """Return score signals without inventing values for unscored records."""
    statuses: dict[str, int] = defaultdict(int)
    scores = [record.score for record in records if record.score is not None]
    for record in records:
        statuses[record.status.value] += 1
    measured = sum(statuses.get(status, 0) for status in ("passed", "failed"))
    return {
        "evaluation_count": len(records),
        "measured_count": measured,
        "pass_rate": (statuses.get("passed", 0) / measured if measured else None),
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
    fields = ("measured_count", "pass_rate", "score_count", "average_score")
    return {
        field: (
            round(after[field] - before[field], 12)
            if comparable
            and before.get(field) is not None
            and after.get(field) is not None
            else None
        )
        for field in fields
    }


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


def _catalogs(
    entries: tuple[_Entry, ...],
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> Mapping[tuple[str, str, str], list[Mapping[str, Any]]]:
    values: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for entry in entries:
        for version in _catalog_versions(entry):
            case = _case(entry, contexts=contexts)
            label, config = _config(entry, contexts=contexts)
            # Preserve a captured catalog even when it cannot yet be paired
            # across runs.  The comparison layer reports that identity gap;
            # export must never erase actual tools/list evidence.
            key = (
                str(case) if case is not None else "<unknown>",
                config or "<unknown>",
                str(version["server"]),
            )
            enriched = dict(version)
            enriched["execution_id"] = _id(entry.report.snapshot.execution_id)
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
    values: Mapping[tuple[str, str, str], list[Mapping[str, Any]]],
) -> Mapping[tuple[str, str, str], list[Mapping[str, Any]]]:
    return {
        key: catalogs for key, catalogs in values.items() if "<unknown>" not in key[:2]
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
    for key in sorted(set(before) & set(after)):
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
                            "case_id": key[0],
                            "configuration": key[1],
                            "server": key[2],
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
                        "case_id": key[0],
                        "configuration": key[1],
                        "server": key[2],
                        "kind": "catalog_completeness",
                        "before": left["complete"],
                        "after": right["complete"],
                    }
                )
        else:
            changes.append(
                {
                    "kind": "catalog_distribution",
                    "case_id": key[0],
                    "configuration": key[1],
                    "server": key[2],
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
                    list(key) for key in sorted(before_keys - after_keys)
                ],
                "current_only": [list(key) for key in sorted(after_keys - before_keys)],
                "complete": False,
            }
        )
    return tuple(changes)


def _evaluation_entries(
    entries: tuple[_Entry, ...],
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[tuple[tuple[str, str, str], EvaluationRecord], ...]:
    values: list[tuple[tuple[str, str, str], EvaluationRecord]] = []
    for entry in entries:
        for record in entry.report.evaluations:
            case = _case(entry, record, contexts)
            _, config = _config(entry, record, contexts)
            if case is None or config is None:
                continue
            values.append(((str(case), record.name, config), record))
    return tuple(values)


def _evaluation_changes(
    old: tuple[_Entry, ...],
    new: tuple[_Entry, ...],
    old_contexts: Mapping[str, Mapping[str, Any]] | None = None,
    new_contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    before: dict[tuple[str, str, str], list[EvaluationRecord]] = defaultdict(list)
    after: dict[tuple[str, str, str], list[EvaluationRecord]] = defaultdict(list)
    unmatched: list[Mapping[str, Any]] = []
    for key, record in _evaluation_entries(old, old_contexts):
        before[key].append(record)
    for key, record in _evaluation_entries(new, new_contexts):
        after[key].append(record)
    for entry in (*old, *new):
        for record in entry.report.evaluations:
            contexts = old_contexts if entry in old else new_contexts
            case = _case(entry, record, contexts)
            _, config = _config(entry, record, contexts)
            if case is None or config is None:
                unmatched.append(
                    {
                        "execution_id": _id(record.execution_id),
                        "evaluator": record.name,
                        "case_id": case,
                        "configuration": config,
                        "status": record.status.value,
                        "score": record.score,
                    }
                )
    changes: list[Mapping[str, Any]] = []
    old_by_execution = {_id(entry.report.snapshot.execution_id): entry for entry in old}
    new_by_execution = {_id(entry.report.snapshot.execution_id): entry for entry in new}
    for key in sorted(set(before) | set(after)):
        left, right = before.get(key, []), after.get(key, [])
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
        left_stats = _evaluation_stats(left)
        right_stats = _evaluation_stats(right)
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
        if left_unknown or right_unknown:
            changed_fields.append("predicate_implementation_unknown")
        if left_values != right_values or not comparable:
            changes.append(
                {
                    "case_id": key[0],
                    "evaluator": key[1],
                    "configuration": key[2],
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
            }
    return values


def _failure_values(
    entries: tuple[_Entry, ...],
    tests: Sequence[Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    values: list[Mapping[str, Any]] = [
        dict(value)
        for value in tests
        if str(value.get("outcome")) in {"failed", "error", "skipped"}
    ]
    for entry in entries:
        for record in entry.report.evaluations:
            if record.status.value in {"failed", "error", "inconclusive"}:
                values.append(
                    {
                        "kind": "evaluation",
                        "execution_id": _id(record.execution_id),
                        "evaluator": record.name,
                        "case_id": _case(entry, record, contexts),
                        "status": record.status.value,
                        "score": record.score,
                        "rationale": record.rationale,
                        "details": dict(record.details),
                    }
                )
    return tuple(values)


def _manifest_failures(
    manifest: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    if manifest is None:
        return ()
    values: list[Mapping[str, Any]] = []
    for report in manifest.get("collection_reports", ()) or ():
        if isinstance(report, Mapping):
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
    for node_id in manifest.get("not_run_node_ids", ()) or ():
        values.append({"kind": "not_run", "node_id": str(node_id)})
    return tuple(values)


def _stats(
    store: ExecutionStore, entries: tuple[_Entry, ...], run_id: str
) -> Mapping[str, Any]:
    names = sorted(
        {record.name for entry in entries for record in entry.report.evaluations}
    )
    values: dict[str, Any] = {}
    for name in names:
        try:
            report = store.aggregate_evaluations(
                EvaluationQuery(filters={"evaluator": (name,), "run_id": (run_id,)})
            )
            values[name] = report.totals.model_dump(mode="json")
        except Exception:
            values[name] = {"unavailable": True}
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
    current_contexts = _contexts(results, manifest)
    limitations: list[str] = []
    if not current and not results:
        limitations.append("no executions or test results were recorded for this run")
    if manifest is not None:
        if manifest.get("collection_reports"):
            limitations.append("pytest collection reported one or more errors")
        if manifest.get("worker_errors"):
            limitations.append("one or more test workers ended with errors")
        if manifest.get("persistence_error"):
            limitations.append("some test manifest data could not be persisted")
        if manifest.get("not_run_node_ids"):
            limitations.append("some collected tests did not produce an attempt")
    tests = tuple(dict(value) for value in results)
    failures = _failure_values(current, tests, current_contexts) + _manifest_failures(
        manifest
    )
    executions = tuple(
        {
            "execution_id": _id(entry.report.snapshot.execution_id),
            "outcome": entry.report.snapshot.outcome.value
            if entry.report.snapshot.outcome is not None
            else None,
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

        def outcomes(
            values: Sequence[Mapping[str, Any]],
            manifest_value: Mapping[str, Any] | None,
        ) -> dict[str, list[Mapping[str, Any]]]:
            grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for value in values:
                if value.get("node_id") is not None:
                    node_id = _normalise_node_id(str(value["node_id"]), manifest_value)
                    grouped[node_id].append({"outcome": value.get("outcome")})
            for node_id in grouped:
                grouped[node_id].sort(key=lambda value: str(value.get("outcome")))
            return grouped

        left_outcomes, right_outcomes = (
            outcomes(baseline_results, baseline_manifest),
            outcomes(results, manifest),
        )
        test_changes = tuple(
            {
                "node_id": key,
                "baseline": left_outcomes.get(key, []),
                "current": right_outcomes.get(key, []),
            }
            for key in sorted(set(left_outcomes) | set(right_outcomes))
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
            baseline, baseline_results, baseline_contexts
        ) + _manifest_failures(baseline_manifest)
        comparison = Comparison(
            baseline_run_id=baseline_id,
            current_run_id=current_id,
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
    summary: dict[str, Any] = {
        "executions": len(current),
        "tests": len(tests),
        "failures": len(failures),
        "terminal_executions": sum(
            1 for entry in current if entry.report.snapshot.outcome is not None
        ),
    }
    if manifest is not None:
        summary["run_status"] = manifest.get("status")
    return Feedback(
        run_id=current_id,
        tests=tests,
        executions=executions,
        failures=failures,
        evaluation_stats=_stats(store, current, current_id),
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
            (root / "diagnostics" / diagnostic_name).write_text(
                json.dumps(_jsonable(test), sort_keys=True, indent=2) + "\n",
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


__all__ = ["Comparison", "Feedback", "build_feedback", "export_feedback"]
