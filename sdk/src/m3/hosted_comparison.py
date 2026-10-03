"""Rebuild a hosted run comparison with the SDK's canonical feedback logic.

The worker accepts only data DTOs on stdin. It never opens a database, makes a
network request, evaluates prompts, or starts a process of its own.
"""

from __future__ import annotations

import json
import posixpath
import sys
from collections.abc import Mapping
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter

from ._wire import internalize_request, neutralize_response
from .feedback import Feedback, build_feedback
from .types import ExecutionPage, ExecutionReport, ExecutionSpec

if TYPE_CHECKING:
    from .storage import ExecutionStore

_MAX_INPUT_BYTES = 32 << 20
_MAX_OUTPUT_BYTES = 4 << 20
_SPEC_ADAPTER: TypeAdapter[ExecutionSpec] = TypeAdapter(ExecutionSpec)
_EXPORT_FIELDS = frozenset(
    {
        "execution_files",
        "spec_files",
        "catalog_files",
        "trace_files",
        "evidence_files",
        "artifact_files",
        "diagnostic_files",
        "test_run_files",
        "test_result_files",
        "unavailable_references",
    }
)


def comparison_input(manifest: Mapping[str, Any], tests: Any) -> dict[str, Any]:
    """Project already-redacted saved evidence without machine paths or output.

    This projection is deterministic across uploader environments. Diagnostics
    and manifest failures are restored from the feedback already in the upload.
    """
    root = str(manifest.get("project_root") or "").replace("\\", "/").rstrip("/")

    def node(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if root and value.startswith(root + "/"):
            return value[len(root) + 1 :]
        return value

    projected = {
        key: manifest[key]
        for key in (
            "run_id",
            "run_label",
            "project_id",
            "status",
            "persistence_error",
        )
        if key in manifest
    }
    # Selection is needed for checkout-independent case matching. Upload only
    # relative pytest file/node identities, never command-line flags or values.
    selected = []
    for value in manifest.get("selection", ()) or ():
        value = node(value)
        if (
            isinstance(value, str)
            and value
            and not value.startswith(("-", "/"))
            and "://" not in value
            and "=" not in value
            and ".." not in value.split("::", 1)[0].split("/")
            and not posixpath.isabs(value)
            and not (len(value) > 1 and value[1] == ":")
        ):
            selected.append(value)
    projected["selection"] = selected
    attempts = []
    for test in tests:
        item = {
            key: test[key]
            for key in (
                "attempt_id",
                "run_id",
                "node_id",
                "suite_id",
                "suite_name",
                "outcome",
                "execution_ids",
                "detached_evaluations",
            )
            if key in test
        }
        item["node_id"] = node(item.get("node_id"))
        phases = test.get("phases")
        if isinstance(phases, Mapping):
            item["phases"] = {
                phase: {
                    key: value[key]
                    for key in ("outcome", "wasxfail", "exception_type")
                    if key in value
                }
                for phase, value in phases.items()
                if isinstance(value, Mapping)
            }
        attempts.append(item)
    return neutralize_response(
        "/api/v2/feedback/{run_id}",
        {
            "schema_version": 1,
            "manifest": projected,
            "test_results": attempts,
        },
    )


def _id(value: Any) -> str:
    return str(getattr(value, "root", value))


def _suite_map(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("invalid suite identity map")
    result: dict[str, int] = {}
    for local, hosted in value.items():
        local_id = str(local)
        if local_id in result:
            raise ValueError("duplicate local suite identity")
        if isinstance(hosted, bool):
            raise ValueError("invalid hosted suite identity")
        try:
            hosted_id = int(hosted)
        except (TypeError, ValueError):
            raise ValueError("invalid hosted suite identity") from None
        if hosted_id < 1:
            raise ValueError("invalid hosted suite identity")
        result[local_id] = hosted_id
    return result


def _suite_id(value: Any, suite_ids: Mapping[str, int]) -> Any:
    if value is None:
        return None
    mapped = suite_ids.get(str(value))
    if mapped is None:
        raise ValueError("suite identity mapping is incomplete")
    return mapped


def _mapped_suite_record(value: Any, suite_ids: Mapping[str, int]) -> Any:
    if not isinstance(value, Mapping) or "suite_id" not in value:
        return value
    return {**value, "suite_id": _suite_id(value.get("suite_id"), suite_ids)}


def _remap_feedback_suites(
    value: Mapping[str, Any], suite_ids: Mapping[str, int]
) -> dict[str, Any]:
    result = dict(value)
    for field in ("suites", "tests", "executions", "failures"):
        records = result.get(field)
        if isinstance(records, (list, tuple)):
            result[field] = tuple(
                _mapped_suite_record(item, suite_ids) for item in records
            )
    return result


def _remap_report_suites(
    value: Mapping[str, Any], suite_ids: Mapping[str, int]
) -> dict[str, Any]:
    result = dict(value)
    if "suite_id" in result:
        result["suite_id"] = _suite_id(result["suite_id"], suite_ids)
    snapshot = result.get("snapshot")
    if isinstance(snapshot, Mapping) and "suite_id" in snapshot:
        result["snapshot"] = _mapped_suite_record(snapshot, suite_ids)
    evaluations = result.get("evaluations")
    if isinstance(evaluations, (list, tuple)):
        result["evaluations"] = tuple(
            _mapped_suite_record(item, suite_ids) for item in evaluations
        )
    return result


def _remap_manifest_suites(
    value: Mapping[str, Any], suite_ids: Mapping[str, int]
) -> dict[str, Any]:
    result = dict(value)
    if "suite_id" in result:
        result["suite_id"] = _suite_id(result["suite_id"], suite_ids)
    suites = result.get("suites")
    if isinstance(suites, (list, tuple)):
        result["suites"] = tuple(
            _mapped_suite_record(item, suite_ids) for item in suites
        )
    return result


def _wire_feedback(
    run: Mapping[str, Any], run_id: str, suite_ids: Mapping[str, int]
) -> Mapping[str, Any]:
    envelope = run.get("feedback")
    if not isinstance(envelope, Mapping):
        raise ValueError("missing feedback envelope")
    value = envelope.get("feedback", envelope)
    if not isinstance(value, Mapping):
        raise ValueError("invalid feedback envelope")
    restored = internalize_request(f"/api/v2/feedback/{run_id}", dict(value))
    if not isinstance(restored, Mapping):
        raise ValueError("invalid feedback envelope")
    restored = dict(restored)
    # Ignore upload-time comparison state before validating the feedback DTO;
    # it can be stale or from an older schema and is never comparison input.
    restored.pop("comparison", None)
    for field in _EXPORT_FIELDS:
        restored.pop(field, None)
    restored = _remap_feedback_suites(restored, suite_ids)
    feedback = Feedback.model_validate(restored)
    if feedback.run_id != run_id:
        raise ValueError("feedback run identity mismatch")
    return feedback.model_dump(mode="json")


class _RunStore:
    """Read-only store surface consumed by ``build_feedback``."""

    def __init__(
        self, run_id: str, run: Mapping[str, Any], suite_ids: Mapping[str, int]
    ):
        self.run_id = run_id
        self.reports: dict[str, ExecutionReport] = {}
        self.specs: dict[str, ExecutionSpec | None] = {}
        self.traces: dict[str, Any] = {}
        feedback = dict(_wire_feedback(run, run_id, suite_ids))
        comparison_input = run.get("comparison_input")
        if comparison_input is None:
            self.manifest, self.test_results = self._legacy_input(feedback)
        else:
            if (
                not isinstance(comparison_input, Mapping)
                or type(comparison_input.get("schema_version")) is not int
                or comparison_input.get("schema_version") != 1
            ):
                raise ValueError("unsupported comparison input")
            comparison_input = internalize_request(
                "/api/v2/feedback/{run_id}", dict(comparison_input)
            )
            raw_manifest = comparison_input.get("manifest")
            raw_tests = comparison_input.get("test_results")
            if not isinstance(raw_manifest, Mapping) or not isinstance(raw_tests, list):
                raise ValueError("invalid comparison input")
            if raw_manifest.get("run_id") != run_id:
                raise ValueError("manifest run identity mismatch")
            if any(not isinstance(item, Mapping) for item in raw_tests):
                raise ValueError("invalid comparison test record")
            self.manifest = _remap_manifest_suites(raw_manifest, suite_ids)
            self.test_results = tuple(
                _mapped_suite_record(item, suite_ids) for item in raw_tests
            )

        # The compact upload carries comparison identities only. Reuse the
        # persisted diagnostic/failure projections instead of duplicating them.
        projected_tests = {
            item.get("attempt_id"): item
            for item in feedback.get("tests", ())
            if isinstance(item, Mapping)
        }
        self.test_results = tuple(
            {
                **item,
                **{
                    key: projected_tests[item.get("attempt_id")][key]
                    for key in (
                        "diagnostics",
                        "metadata",
                        "description",
                        "started_at",
                        "finished_at",
                        "duration_seconds",
                    )
                    if item.get("attempt_id") in projected_tests
                    and key in projected_tests[item.get("attempt_id")]
                    and key not in item
                },
            }
            for item in self.test_results
        )
        self.manifest = dict(self.manifest)
        for kind, field in (
            ("collection", "collection_reports"),
            ("worker", "worker_errors"),
        ):
            if field not in self.manifest:
                self.manifest[field] = [
                    {key: value for key, value in item.items() if key != "kind"}
                    for item in feedback.get("failures", ())
                    if isinstance(item, Mapping) and item.get("kind") == kind
                ]

        reports = run.get("reports")
        if not isinstance(reports, list):
            raise ValueError("invalid reports")
        for envelope in reports:
            if not isinstance(envelope, Mapping):
                raise ValueError("invalid report envelope")
            restored = internalize_request(
                "/api/v2/executions/{execution_id}/report", dict(envelope)
            )
            execution_id = _id(restored.get("execution_id"))
            report_value = restored.get("report")
            if not isinstance(report_value, Mapping):
                raise ValueError("invalid execution report")
            report_value = _remap_report_suites(report_value, suite_ids)
            report = ExecutionReport.model_validate(report_value)
            report_id = _id(report.snapshot.execution_id)
            if execution_id != report_id:
                raise ValueError("report execution identity mismatch")
            if report_id in self.reports:
                raise ValueError("duplicate report execution identity")
            recorded_run_id = (
                _id(report.snapshot.run_id) if report.snapshot.run_id else None
            )
            if recorded_run_id is not None and recorded_run_id != run_id:
                raise ValueError("report run identity mismatch")
            spec_value = restored.get("spec")
            spec = (
                _SPEC_ADAPTER.validate_python(
                    _mapped_suite_record(spec_value, suite_ids)
                )
                if spec_value is not None
                else None
            )
            trace_value = restored.get("trace")
            summary = (
                trace_value.get("summary") if isinstance(trace_value, Mapping) else None
            )
            trace_summary = summary if isinstance(summary, Mapping) else {}
            self.reports[report_id] = report
            self.specs[report_id] = spec
            self.traces[report_id] = SimpleNamespace(
                summary=SimpleNamespace(
                    **{
                        key: trace_summary.get(key, 0)
                        for key in (
                            "tool_call_count",
                            "successful_tool_call_count",
                            "failed_tool_call_count",
                        )
                    }
                )
            )

        for report in self.reports.values():
            recorded_run_id = (
                _id(report.snapshot.run_id) if report.snapshot.run_id else None
            )
            if recorded_run_id != run_id:
                raise ValueError("report is not associated with its run")

        for attempt in self.test_results:
            if attempt.get("run_id") is not None and attempt["run_id"] != run_id:
                raise ValueError("test attempt run identity mismatch")
            execution_ids = attempt.get("execution_ids") or ()
            if not isinstance(execution_ids, (list, tuple)) or any(
                not isinstance(value, str) or value not in self.reports
                for value in execution_ids
            ):
                raise ValueError("test attempt execution identity mismatch")

    @staticmethod
    def _legacy_input(
        feedback: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
        summary = feedback.get("summary")
        manifest: dict[str, Any] = {"run_id": feedback.get("run_id")}
        if isinstance(summary, Mapping):
            for source, target in (("run_status", "status"),):
                if summary.get(source) is not None:
                    manifest[target] = summary[source]
        if feedback.get("run_label") is not None:
            manifest["run_label"] = feedback["run_label"]
        if feedback.get("project_id") is not None:
            manifest["project_id"] = feedback["project_id"]
        # Keep the exported projection's actual attempt context where present.
        # The explicit limitation below makes clear that it is not equivalent
        # to persisted raw attempts or the full run manifest.
        tests = tuple(
            dict(item)
            for item in feedback.get("tests", ())
            if isinstance(item, Mapping)
        )
        return manifest, tests

    def list_executions(
        self, *, limit: int = 100, offset: int = 0, run_id: Any = None, **_: Any
    ) -> ExecutionPage:
        if _id(run_id) != self.run_id:
            return ExecutionPage(limit=limit, offset=offset, total=0)
        snapshots = sorted(
            (report.snapshot for report in self.reports.values()),
            key=lambda item: (item.created_at, _id(item.execution_id)),
        )
        return ExecutionPage(
            items=tuple(snapshots[offset : offset + limit]),
            limit=limit,
            offset=offset,
            total=len(snapshots),
        )

    def get_report(self, execution_id: Any, **_: Any) -> ExecutionReport | None:
        return self.reports.get(_id(execution_id))

    def get_execution_spec(self, execution_id: Any, **_: Any) -> ExecutionSpec | None:
        return self.specs.get(_id(execution_id))

    def get_trace_view(self, execution_id: Any) -> Any:
        return self.traces.get(_id(execution_id))

    def get_test_run(self, run_id: Any) -> Mapping[str, Any] | None:
        return self.manifest if _id(run_id) == self.run_id else None

    def list_test_results(self, run_id: Any) -> tuple[Mapping[str, Any], ...]:
        return self.test_results if _id(run_id) == self.run_id else ()

    def get_project(self, project_id: Any) -> None:
        return None


class _PairStore:
    """Route the SDK's ordinary read-only store calls across two runs."""

    def __init__(self, current: _RunStore, baseline: _RunStore):
        self.runs = {current.run_id: current, baseline.run_id: baseline}
        self.by_execution: dict[str, _RunStore] = {}
        for run in self.runs.values():
            for execution_id in run.reports:
                if execution_id in self.by_execution:
                    raise ValueError("execution identity is duplicated across runs")
                self.by_execution[execution_id] = run

    def list_executions(
        self, *, run_id: Any, limit: int = 100, offset: int = 0, **_: Any
    ) -> ExecutionPage:
        run = self.runs.get(_id(run_id))
        return (
            run.list_executions(run_id=run_id, limit=limit, offset=offset)
            if run is not None
            else ExecutionPage(limit=limit, offset=offset, total=0)
        )

    def get_report(self, execution_id: Any, **kwargs: Any) -> ExecutionReport | None:
        run = self.by_execution.get(_id(execution_id))
        return run.get_report(execution_id, **kwargs) if run else None

    def get_execution_spec(
        self, execution_id: Any, **kwargs: Any
    ) -> ExecutionSpec | None:
        run = self.by_execution.get(_id(execution_id))
        return run.get_execution_spec(execution_id, **kwargs) if run else None

    def get_trace_view(self, execution_id: Any) -> Any:
        run = self.by_execution.get(_id(execution_id))
        return run.get_trace_view(execution_id) if run else None

    def get_test_run(self, run_id: Any) -> Mapping[str, Any] | None:
        run = self.runs.get(_id(run_id))
        return run.get_test_run(run_id) if run else None

    def list_test_results(self, run_id: Any) -> tuple[Mapping[str, Any], ...]:
        run = self.runs.get(_id(run_id))
        return run.list_test_results(run_id) if run else ()

    def get_project(self, project_id: Any) -> None:
        return None


def compare_runs(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact SDK Comparison as neutral v2 JSON."""
    if not isinstance(payload, Mapping):
        raise ValueError("invalid comparison request")
    current = payload.get("current")
    baseline = payload.get("baseline")
    if not isinstance(current, Mapping) or not isinstance(baseline, Mapping):
        raise ValueError("comparison requires current and baseline runs")
    current_id = current.get("run_id")
    baseline_id = baseline.get("run_id")
    if not isinstance(current_id, str) or not current_id:
        raise ValueError("invalid current run identity")
    if not isinstance(baseline_id, str) or not baseline_id or current_id == baseline_id:
        raise ValueError("invalid baseline run identity")
    current_store = _RunStore(current_id, current, _suite_map(current.get("suite_ids")))
    baseline_store = _RunStore(
        baseline_id, baseline, _suite_map(baseline.get("suite_ids"))
    )
    current_execution_ids = set(current_store.reports)
    if current_execution_ids.intersection(baseline_store.reports):
        raise ValueError("execution identity is duplicated across runs")
    pair_store = _PairStore(current_store, baseline_store)
    feedback = build_feedback(
        cast("ExecutionStore", pair_store),
        current_id,
        baseline_run_id=baseline_id,
    )
    comparison = feedback.comparison
    if comparison is None:
        raise ValueError("comparison was not produced")
    legacy_limitations: list[str] = []
    if current.get("comparison_input") is None:
        legacy_limitations.append(
            "current run has only the legacy feedback projection; test attempt and "
            "manifest evidence is incomplete"
        )
    if baseline.get("comparison_input") is None:
        legacy_limitations.append(
            "baseline run has only the legacy feedback projection; test attempt and "
            "manifest evidence is incomplete"
        )
    if legacy_limitations:
        comparison = comparison.model_copy(
            update={
                "limitations": tuple(
                    dict.fromkeys((*comparison.limitations, *legacy_limitations))
                )
            }
        )
    result = neutralize_response(
        "/api/v2/feedback/{run_id}", comparison.model_dump(mode="json")
    )
    if not isinstance(result, dict):
        raise ValueError("comparison could not be serialized")
    if (
        result.get("baseline_run_id") != baseline_id
        or result.get("current_run_id") != current_id
    ):
        raise ValueError("comparison identity mismatch")
    return result


def main() -> int:
    """Fixed stdin/stdout worker entry point with bounded, generic errors."""
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("request too large")
        payload = json.loads(raw)
        result = compare_runs(payload)
        output = json.dumps(
            result, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        if len(output) > _MAX_OUTPUT_BYTES:
            raise ValueError("response too large")
        sys.stdout.buffer.write(output + b"\n")
        return 0
    except (ValueError, MemoryError):
        sys.stdout.buffer.write(b'{"error":"comparison_incompatible"}\n')
        return 1
    except Exception:
        sys.stdout.buffer.write(b'{"error":"comparison_unavailable"}\n')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["compare_runs", "comparison_input", "main"]
