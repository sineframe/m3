"""Agent feedback projections compare observed MCP evidence conservatively."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import pytest
from mcp import types as mcp_types

from m3.events import EventFactory, EventSequence
from m3.feedback import build_feedback, export_feedback
from m3.storage import SQLiteExecutionStore
from m3.types import (
    CallToolResult,
    ErrorCode,
    ErrorInfo,
    EvaluationId,
    EvaluationRecord,
    EvaluationStatus,
    EventDirection,
    EventKind,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionReport,
    ExecutionState,
    ExecutionStatus,
    RequestLink,
    SuiteId,
)


class _Store:
    def __init__(self, reports, *, tests=None, manifests=None, specs=None):
        self.reports = {
            str(report.snapshot.execution_id.root): report for report in reports
        }
        self.specs = specs or {
            str(report.snapshot.execution_id.root): _Spec() for report in reports
        }
        self.tests = tests or {}
        self.manifests = manifests or {}

    def list_executions(self, *, limit=100, offset=0, run_id=None, **_):
        values = [
            report.snapshot
            for report in self.reports.values()
            if str(getattr(report.snapshot.run_id, "root", report.snapshot.run_id))
            == str(getattr(run_id, "root", run_id))
        ]
        values.sort(key=lambda item: str(item.execution_id))
        return ExecutionPage(
            items=tuple(values[offset : offset + limit]),
            limit=limit,
            offset=offset,
            total=len(values),
        )

    def get_report(self, execution_id, **_):
        return self.reports.get(str(getattr(execution_id, "root", execution_id)))

    def get_execution_spec(self, execution_id, **_):
        return self.specs.get(str(getattr(execution_id, "root", execution_id)))

    def get_test_run(self, run_id):
        return self.manifests.get(str(run_id))

    def list_test_results(self, run_id):
        return tuple(self.tests.get(str(run_id), ()))


class _Spec:
    case_id = "case"
    metadata: ClassVar[dict[str, str]] = {"harness_config": "model-a"}

    def model_dump(self, mode="json"):
        del mode
        return {
            "kind": "agent",
            "case_id": self.case_id,
            "metadata": dict(self.metadata),
            "servers": (),
            "harness": "model-a",
        }


class _InputSpec(_Spec):
    """Minimal spec double exposing the real ExecutionSpec input fields."""

    def __init__(self, **updates):
        self.payload = {
            "kind": "agent",
            "case_id": "case",
            "metadata": {"harness_config": "model-a"},
            "servers": [
                {
                    "server": {"kind": "stdio", "name": "orders", "command": "orders"},
                    "required": True,
                }
            ],
            "protocol": {"revision": "2025-11-25", "transport": "stdio"},
            "timeout_seconds": 30.0,
            "artifact_policy": "failed",
            "declared_artifacts": [],
            "workspace": {
                "kind": "temporary",
                "source": None,
                "acknowledge_risk": False,
            },
            "harness": {
                "kind": "opencode",
                "name": "opencode",
                "model": "model-a",
                "provider": None,
                "dialect": "auto",
                "credential_references": {},
            },
            "harness_profile": None,
            "message": {
                "content": [{"kind": "text", "text": "find an order"}],
                "metadata": {},
            },
            "goal": "find the matching order",
            "evaluations": [{"name": "quality", "required": True}],
            "tool_policy": {
                "kind": "restrictive",
                "allowed_tools": ["lookup"],
                "denied_tools": [],
            },
            "permission_policy": {"mode": "deny"},
            "elicitation_policy": {"mode": "deny"},
            "sampling_policy": {"mode": "deny"},
            "filesystem_policy": {"mode": "deny"},
            "terminal_policy": {"mode": "deny"},
        }
        self.payload.update(updates)
        self.metadata = self.payload["metadata"]
        self.case_id = self.payload["case_id"]

    def model_dump(self, mode="json"):
        del mode
        return self.payload


def _report(
    execution_id,
    run_id,
    description,
    *,
    server="orders",
    connection="connection",
    pages=None,
    wire_tools=None,
    suite_id=None,
    suite_name=None,
):
    factory = EventFactory(
        execution_id,
        allocator=EventSequence(start=0),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    selected_pages = pages or ((None, None, "lookup", description),)
    events = []
    for index, (cursor_in, cursor_out, name, text) in enumerate(selected_pages, 1):
        tool_values = (
            wire_tools
            if wire_tools is not None
            else [
                {"name": name, "description": text, "inputSchema": {"type": "object"}}
            ]
        )
        events.extend(
            (
                factory.create(
                    EventKind.MCP_REQUEST,
                    connection_id=connection,
                    server_binding=server,
                    correlation=RequestLink(
                        jsonrpc_id=index,
                        direction=EventDirection.CLIENT_TO_SERVER,
                        request_sequence=index,
                    ),
                    payload={
                        "method": "tools/list",
                        "params": {} if cursor_in is None else {"cursor": cursor_in},
                    },
                ),
                factory.create(
                    EventKind.MCP_RESPONSE,
                    connection_id=connection,
                    server_binding=server,
                    correlation=RequestLink(
                        jsonrpc_id=index,
                        direction=EventDirection.SERVER_TO_CLIENT,
                        request_sequence=index,
                    ),
                    payload={
                        "method": "tools/list",
                        "result": {
                            "tools": tool_values,
                            **(
                                {"nextCursor": cursor_out}
                                if cursor_out is not None
                                else {}
                            ),
                        },
                    },
                ),
            )
        )
    snapshot = ExecutionState(
        execution_id=execution_id,
        run_id=run_id,
        suite_id=SuiteId(suite_id) if suite_id is not None else None,
        suite_name=suite_name,
        lifecycle=ExecutionStatus.FINISHED,
        outcome=ExecutionOutcome.COMPLETED,
        finished_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return ExecutionReport(
        snapshot=snapshot, events=tuple(events), event_count=len(events)
    )


def test_comparison_is_server_scoped_and_reports_description_change():
    store = _Store(
        (
            _report("old", "baseline", "old", server="a"),
            _report("new", "current", "new", server="a"),
            _report("other", "current", "different", server="b"),
        )
    )
    feedback = build_feedback(store, "current", baseline_run_id="baseline")
    changes = feedback.comparison.interface_changes
    assert any(
        item.get("server") == "a" and item.get("tool") == "lookup" for item in changes
    )
    assert all(item.get("server") != "b" for item in changes if "server" in item)


def test_catalog_accepts_official_mcp_tools_list_shape():
    result = mcp_types.ListToolsResult(
        tools=[
            mcp_types.Tool(
                name="lookup", description="official", inputSchema={"type": "object"}
            )
        ]
    )
    report = _report(
        "official", "run", "ignored", wire_tools=result.model_dump(mode="json")["tools"]
    )
    store = _Store((report,))
    feedback = build_feedback(store, "run")
    assert feedback.executions[0]["execution_id"] == "official"


def test_unmatched_catalog_is_limitation_and_not_tool_removal():
    old = _report("old", "baseline", "old")
    current = _report("new", "current", "new", connection="different")
    feedback = build_feedback(
        _Store((old, current)), "current", baseline_run_id="baseline"
    )
    assert any(
        item.get("tool") == "lookup" for item in feedback.comparison.interface_changes
    )
    assert all(
        "connection_before" not in item
        or item["connection_before"] != item["connection_after"]
        for item in feedback.comparison.interface_changes
        if item.get("tool") == "lookup"
    )


def test_export_keeps_current_and_baseline_supporting_reports(tmp_path):
    old = _report("old", "baseline", "old")
    current = _report("new", "current", "new")
    store = _Store((old, current))
    feedback = build_feedback(store, "current", baseline_run_id="baseline")
    path = export_feedback(feedback, store, tmp_path)
    payload = json.loads(path.read_text())
    assert set(payload["execution_files"]) == {"old", "new"}
    for ref in payload["execution_files"].values():
        assert (tmp_path / ref).is_file()


def test_feedback_exports_suite_inventory_entries_and_comparison_identity(tmp_path):
    baseline = _report("old", "baseline", "old", suite_id=1, suite_name="catalog")
    current = _report("new", "current", "new", suite_id=2, suite_name="other")
    store = _Store(
        (baseline, current),
        tests={
            "baseline": (
                {
                    "attempt_id": "a",
                    "node_id": "test.py::case",
                    "outcome": "passed",
                    "execution_ids": ["old"],
                },
            ),
            "current": (
                {
                    "attempt_id": "b",
                    "node_id": "test.py::case",
                    "outcome": "failed",
                    "execution_ids": ["new"],
                },
            ),
        },
    )
    feedback = build_feedback(store, "current", baseline_run_id="baseline")
    assert feedback.suites == ({"suite_id": 2, "suite_name": "other"},)
    assert feedback.executions[0]["suite_id"] == 2
    assert feedback.tests[0]["suite_name"] == "other"
    comparison = feedback.comparison.model_dump(mode="json")
    assert comparison["baseline_suites"] == [{"suite_id": 1, "suite_name": "catalog"}]
    assert comparison["current_suites"] == [{"suite_id": 2, "suite_name": "other"}]
    assert {item["suite_id"] for item in comparison["test_changes"]} == {1, 2}
    path = export_feedback(feedback, store, tmp_path)
    exported = json.loads(path.read_text())
    assert exported["suites"] == [{"suite_id": 2, "suite_name": "other"}]
    sidecar = json.loads((tmp_path / exported["test_result_files"]["b"]).read_text())
    assert sidecar["suite_id"] == 2
    assert sidecar["suite_name"] == "other"


def test_feedback_suite_inventory_includes_pytest_only_suite():
    store = _Store(
        (),
        tests={
            "run": (
                {
                    "attempt_id": "pytest-only",
                    "node_id": "test.py::case",
                    "outcome": "passed",
                    "suite_id": 9,
                    "suite_name": "pytest-only",
                },
            )
        },
    )
    feedback = build_feedback(store, "run")
    assert feedback.suites == ({"suite_id": 9, "suite_name": "pytest-only"},)


def test_feedback_preserves_attempt_suite_when_execution_has_none(tmp_path):
    execution = _report("unlabelled", "run", "unlabelled")
    store = _Store(
        (execution,),
        tests={
            "run": (
                {
                    "attempt_id": "attempt",
                    "node_id": "test.py::case",
                    "outcome": "failed",
                    "execution_ids": ["unlabelled"],
                    "suite_id": 7,
                    "suite_name": "catalog",
                },
            )
        },
    )
    feedback = build_feedback(store, "run")
    assert feedback.tests[0]["suite_name"] == "catalog"
    assert feedback.tests[0]["suite_id"] == 7
    assert feedback.failures[0]["suite_name"] == "catalog"
    assert feedback.suites == ({"suite_id": 7, "suite_name": "catalog"},)
    exported = export_feedback(feedback, store, tmp_path)
    payload = json.loads(exported.read_text())
    attempt = json.loads(
        (tmp_path / payload["test_result_files"]["attempt"]).read_text()
    )
    assert attempt["suite_id"] == 7
    assert attempt["suite_name"] == "catalog"


def test_repeated_trials_compare_distributions_without_matching_trial_ids():
    baseline = _report("old", "baseline", "old")
    current = _report("new", "current", "new")
    baseline = baseline.model_copy(
        update={
            "evaluations": tuple(
                EvaluationRecord(
                    evaluation_id=EvaluationId("b-1"),
                    execution_id=baseline.snapshot.execution_id,
                    case_id="lookup",
                    name="quality",
                    status=EvaluationStatus.PASSED,
                    score=1.0,
                    metadata={"harness_config": "model-a"},
                )
                for _ in range(2)
            )
        }
    )
    current = current.model_copy(
        update={
            "evaluations": tuple(
                EvaluationRecord(
                    evaluation_id=EvaluationId("c-1"),
                    execution_id=current.snapshot.execution_id,
                    case_id="lookup",
                    name="quality",
                    status=EvaluationStatus.PASSED,
                    score=1.0,
                    metadata={"harness_config": "model-a"},
                )
                for _ in range(2)
            )
        }
    )
    feedback = build_feedback(
        _Store((baseline, current)), "current", baseline_run_id="baseline"
    )
    assert feedback.comparison.evaluation_changes == ()


def test_repeated_trials_expose_score_and_pass_rate_delta():
    baseline = _report("old-score", "baseline", "same")
    current = _report("new-score", "current", "same")

    def record(execution_id, evaluation_id, status, score):
        return EvaluationRecord(
            evaluation_id=EvaluationId(evaluation_id),
            execution_id=execution_id,
            case_id="case",
            name="quality",
            status=EvaluationStatus(status),
            score=score,
        )

    baseline_records = (
        record(baseline.snapshot.execution_id, "b-pass", "passed", 0.4),
        record(baseline.snapshot.execution_id, "b-fail", "failed", 0.2),
    )
    current_records = (
        record(current.snapshot.execution_id, "c-pass-1", "passed", 0.8),
        record(current.snapshot.execution_id, "c-pass-2", "passed", 1.0),
    )
    baseline = baseline.model_copy(update={"evaluations": baseline_records})
    current = current.model_copy(update={"evaluations": current_records})
    feedback = build_feedback(
        _Store((baseline, current)), "current", baseline_run_id="baseline"
    )
    change = next(
        item
        for item in feedback.comparison.evaluation_changes
        if item.get("evaluator") == "quality"
    )
    assert change["configuration_label_before"] == ("model-a",)
    assert change["configuration_label_after"] == ("model-a",)
    assert change["before"]["stats"] == {
        "evaluation_count": 2,
        "measured_count": 2,
        "pass_rate": 0.5,
        "score_count": 2,
        "average_score": 0.3,
        "status_counts": {"failed": 1, "passed": 1},
    }
    assert change["after"]["stats"]["pass_rate"] == 1.0
    assert change["after"]["stats"]["average_score"] == 0.9
    assert change["delta"]["measured_count"] == 0
    assert change["delta"]["pass_rate"] == 0.5
    assert change["delta"]["score_count"] == 0
    assert change["delta"]["average_score"] == 0.6


def test_unscored_results_keep_null_score_signals_and_null_delta():
    baseline = _report("old-unscored", "baseline", "same")
    current = _report("new-unscored", "current", "same")
    baseline_record = EvaluationRecord(
        evaluation_id=EvaluationId("b-unscored"),
        execution_id=baseline.snapshot.execution_id,
        case_id="case",
        name="quality",
        status=EvaluationStatus.PASSED,
        score=None,
    )
    current_record = baseline_record.model_copy(
        update={
            "evaluation_id": EvaluationId("c-unscored"),
            "execution_id": current.snapshot.execution_id,
            "status": EvaluationStatus.FAILED,
        }
    )
    baseline = baseline.model_copy(update={"evaluations": (baseline_record,)})
    current = current.model_copy(update={"evaluations": (current_record,)})
    feedback = build_feedback(
        _Store((baseline, current)), "current", baseline_run_id="baseline"
    )
    change = next(
        item
        for item in feedback.comparison.evaluation_changes
        if item.get("evaluator") == "quality"
    )
    assert change["before"]["stats"]["average_score"] is None
    assert change["before"]["stats"]["score_count"] == 0
    assert change["delta"]["average_score"] is None


def test_paginated_catalog_requires_the_complete_cursor_chain():
    complete = _report(
        "old",
        "baseline",
        "old",
        pages=((None, "next", "lookup", "old"), ("next", None, "extra", "page")),
    )
    incomplete = _report(
        "new", "current", "new", pages=((None, "missing", "lookup", "new"),)
    )
    feedback = build_feedback(
        _Store((complete, incomplete)), "current", baseline_run_id="baseline"
    )
    change = next(
        item
        for item in feedback.comparison.interface_changes
        if item.get("tool") == "lookup"
    )
    assert change["complete"] is False


def test_direct_runs_use_manifest_node_id_and_ignore_connection_ids():
    old = _report("old-direct", "baseline", "old", connection="random-old")
    current = _report("new-direct", "current", "new", connection="random-new")
    tests = {
        "baseline": (
            {
                "attempt_id": "attempt-old",
                "node_id": "test_server.py::test_catalog",
                "outcome": "passed",
                "execution_ids": ["old-direct"],
            },
        ),
        "current": (
            {
                "attempt_id": "attempt-new",
                "node_id": "/checkout/test_server.py::test_catalog",
                "outcome": "passed",
                "execution_ids": ["new-direct"],
            },
        ),
    }
    manifests = {
        "baseline": {"selection": ["test_server.py"], "status": "finished"},
        "current": {"selection": ["test_server.py"], "status": "finished"},
    }
    feedback = build_feedback(
        _Store((old, current), tests=tests, manifests=manifests),
        "current",
        baseline_run_id="baseline",
    )
    assert any(
        item.get("tool") == "lookup" for item in feedback.comparison.interface_changes
    )
    assert feedback.comparison.coverage["baseline_tests"] == 1
    assert not any(
        "scenario identity was unavailable" in item
        for item in feedback.comparison.limitations
    )


def test_changed_matcher_expectation_is_explicitly_not_comparable():
    old = _report("old", "baseline", "old")
    current = _report("new", "current", "new")
    old_record = EvaluationRecord(
        evaluation_id=EvaluationId("old-eval"),
        execution_id=old.snapshot.execution_id,
        case_id="lookup",
        name="m3.matcher.to_have_tool",
        status=EvaluationStatus.PASSED,
        score=1.0,
        details={"matcher": "to_have_tool", "arguments": {"name": "lookup"}},
    )
    new_record = EvaluationRecord(
        evaluation_id=EvaluationId("new-eval"),
        execution_id=current.snapshot.execution_id,
        case_id="lookup",
        name="m3.matcher.to_have_tool",
        status=EvaluationStatus.PASSED,
        score=1.0,
        details={"matcher": "to_have_tool", "arguments": {"name": "different"}},
    )
    old = old.model_copy(update={"evaluations": (old_record,)})
    current = current.model_copy(update={"evaluations": (new_record,)})
    feedback = build_feedback(
        _Store((old, current)), "current", baseline_run_id="baseline"
    )
    change = next(
        item
        for item in feedback.comparison.evaluation_changes
        if item.get("evaluator") == "m3.matcher.to_have_tool"
    )
    assert change["comparable"] is False
    assert "expected_or_provenance" in change["changed_fields"]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        (
            "message",
            {
                "content": [{"kind": "text", "text": "find a different order"}],
                "metadata": {},
            },
        ),
        (
            "tool_policy",
            {
                "kind": "restrictive",
                "allowed_tools": ["other_lookup"],
                "denied_tools": [],
            },
        ),
        ("evaluations", [{"name": "different_quality", "required": True}]),
    ],
)
def test_changed_agent_inputs_stay_matched_but_are_not_comparable(field, replacement):
    baseline = _report("old-input", "baseline", "same")
    current = _report("new-input", "current", "same")
    specs = {
        "old-input": _InputSpec(),
        "new-input": _InputSpec(**{field: replacement}),
    }
    records = (
        EvaluationRecord(
            evaluation_id=EvaluationId("old-quality"),
            execution_id=baseline.snapshot.execution_id,
            case_id="case",
            name="quality",
            status=EvaluationStatus.PASSED,
            score=1.0,
        ),
        EvaluationRecord(
            evaluation_id=EvaluationId("new-quality"),
            execution_id=current.snapshot.execution_id,
            case_id="case",
            name="quality",
            status=EvaluationStatus.PASSED,
            score=1.0,
        ),
    )
    baseline = baseline.model_copy(update={"evaluations": (records[0],)})
    current = current.model_copy(update={"evaluations": (records[1],)})
    feedback = build_feedback(
        _Store((baseline, current), specs=specs),
        "current",
        baseline_run_id="baseline",
    )
    change = next(
        item
        for item in feedback.comparison.evaluation_changes
        if item.get("evaluator") == "quality"
    )
    assert change["comparable"] is False
    assert "expected_or_provenance" in change["changed_fields"]
    assert feedback.comparison.limitations == ()


def test_manifest_failures_and_reruns_are_preserved():
    report = _report("execution", "run", "description")
    tests = {
        "run": (
            {
                "attempt_id": "first",
                "node_id": "test.py::test_case",
                "outcome": "failed",
                "execution_ids": ["execution"],
            },
            {
                "attempt_id": "second",
                "node_id": "test.py::test_case",
                "outcome": "passed",
                "execution_ids": ["execution"],
            },
        )
    }
    manifests = {
        "run": {
            "selection": ["test.py"],
            "collection_reports": [{"node_id": "bad.py", "outcome": "failed"}],
            "not_run_node_ids": ["test.py::not_run"],
        }
    }
    feedback = build_feedback(
        _Store((report,), tests=tests, manifests=manifests), "run"
    )
    assert len(feedback.tests) == 2
    assert any(item.get("kind") == "collection" for item in feedback.failures)
    assert any("not produce an attempt" in item for item in feedback.limitations)


def test_failed_matcher_is_evidence_for_one_failed_pytest_case():
    report = _report("execution", "run", "description")
    matcher = EvaluationRecord(
        evaluation_id=EvaluationId("matcher-1"),
        execution_id=report.snapshot.execution_id,
        name="m3.matcher.to_have_text.v1",
        status=EvaluationStatus.FAILED,
    )
    report = report.model_copy(update={"evaluations": (matcher,)})
    store = _Store(
        (report,),
        tests={
            "run": (
                {
                    "attempt_id": "attempt-1",
                    "node_id": "test.py::test_failure",
                    "outcome": "failed",
                    "execution_ids": ["execution"],
                },
            )
        },
    )

    feedback = build_feedback(store, "run")

    assert feedback.summary["failures"] == 1
    assert len(feedback.failures) == 1
    assert feedback.failures[0]["verdict"] == "failed_assertion"
    assert len(feedback.failures[0]["evaluations"]) == 1


def test_skipped_and_not_run_cases_do_not_inflate_pytest_failures():
    feedback = build_feedback(
        _Store(
            (),
            tests={"run": ({"node_id": "test.py::test_skip", "outcome": "skipped"},)},
            manifests={"run": {"not_run_node_ids": ["test.py::test_not_run"]}},
        ),
        "run",
    )

    assert feedback.summary["failures"] == 0
    assert feedback.summary["skipped_tests"] == 1
    assert feedback.failures == ()
    assert any("not produce an attempt" in item for item in feedback.limitations)


def test_feedback_keeps_test_verdict_tool_result_and_execution_outcome_separate():
    tool = _report("tool", "run", "tool result").model_copy(
        update={"direct_result": CallToolResult(is_error=True)}
    )
    protocol = _report("protocol", "run", "protocol failure").model_copy(
        update={"error": ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="failed")}
    )
    tests = {
        "run": (
            {
                "node_id": "test.py::test_expected_error",
                "outcome": "passed",
                "execution_ids": ["tool"],
            },
            {
                "node_id": "test.py::test_protocol",
                "outcome": "failed",
                "execution_ids": ["protocol"],
            },
            {
                "node_id": "test.py::test_setup",
                "outcome": "error",
                "execution_ids": [],
                "phases": {"setup": {"outcome": "failed"}},
            },
        )
    }

    feedback = build_feedback(_Store((tool, protocol), tests=tests), "run")

    assert [test["verdict"] for test in feedback.tests] == [
        "passed",
        "protocol_error",
        "setup_error",
    ]
    assert feedback.tests[0]["tool_result"] == "tool_error"
    executions = {item["execution_id"]: item for item in feedback.executions}
    assert executions["tool"]["outcome"] == "completed"
    assert executions["tool"]["result_kind"] == "tool_error"
    assert feedback.summary["failures"] == 2


def test_feedback_detects_top_level_tool_error_without_result_body():
    report = _report("tool", "run", "tool error")
    factory = EventFactory(
        report.snapshot.execution_id,
        allocator=EventSequence(start=len(report.events)),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    event = factory.create(
        EventKind.TOOL_RESULT_RECEIVED,
        payload={"isError": True, "tool_status": "tool_error"},
    )
    report = report.model_copy(
        update={
            "events": (*report.events, event),
            "event_count": report.event_count + 1,
        }
    )
    store = _Store(
        (report,),
        tests={
            "run": (
                {
                    "node_id": "test.py::test_expected_tool_error",
                    "outcome": "passed",
                    "execution_ids": ["tool"],
                },
            )
        },
    )

    feedback = build_feedback(store, "run")

    assert feedback.executions[0]["outcome"] == "completed"
    assert feedback.executions[0]["result_kind"] == "tool_error"
    assert feedback.tests[0]["verdict"] == "passed"
    assert feedback.tests[0]["tool_result"] == "tool_error"


def test_feedback_keeps_tool_error_when_cleanup_also_fails():
    report = _report("tool", "run", "tool error").model_copy(
        update={
            "direct_result": CallToolResult(is_error=True),
            "error": ErrorInfo(code=ErrorCode.CLEANUP_FAILED, message="cleanup failed"),
        }
    )
    feedback = build_feedback(
        _Store(
            (report,),
            tests={
                "run": (
                    {
                        "node_id": "test.py::test_cleanup_error",
                        "outcome": "error",
                        "execution_ids": ["tool"],
                        "phases": {"teardown": {"outcome": "failed"}},
                    },
                )
            },
        ),
        "run",
    )

    assert feedback.executions[0]["result_kind"] == "cleanup_failed"
    assert feedback.executions[0]["tool_result"] == "tool_error"
    assert feedback.tests[0]["verdict"] == "teardown_error"
    assert feedback.tests[0]["tool_result"] == "tool_error"


def test_unrelated_exception_after_protocol_result_keeps_pytest_verdict():
    report = _report("protocol", "run", "protocol result").model_copy(
        update={"error": ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="failed")}
    )
    feedback = build_feedback(
        _Store(
            (report,),
            tests={
                "run": (
                    {
                        "node_id": "test.py::test_unrelated_error",
                        "outcome": "failed",
                        "execution_ids": ["protocol"],
                        "phases": {
                            "call": {
                                "outcome": "failed",
                                "exception_type": "RuntimeError",
                            }
                        },
                    },
                )
            },
        ),
        "run",
    )

    assert feedback.tests[0]["verdict"] == "pytest_error"


def test_test_comparison_ignores_attempt_ids_and_durations():
    old = _report("old", "baseline", "description")
    current = _report("new", "current", "description")
    manifests = {
        "baseline": {"selection": ["test.py"]},
        "current": {"selection": ["test.py"]},
    }
    tests = {
        "baseline": (
            {
                "attempt_id": "uuid-a",
                "node_id": "test.py::test_case",
                "outcome": "passed",
                "duration_seconds": 0.1,
            },
            {
                "attempt_id": "uuid-b",
                "node_id": "test.py::test_case",
                "outcome": "passed",
                "duration_seconds": 0.2,
            },
        ),
        "current": (
            {
                "attempt_id": "uuid-c",
                "node_id": "test.py::test_case",
                "outcome": "passed",
                "duration_seconds": 8.0,
            },
            {
                "attempt_id": "uuid-d",
                "node_id": "test.py::test_case",
                "outcome": "passed",
                "duration_seconds": 9.0,
            },
        ),
    }
    store = _Store((old, current), tests=tests, manifests=manifests)
    feedback = build_feedback(store, "current", baseline_run_id="baseline")
    assert feedback.comparison.test_changes == ()
    store.tests["current"] = (
        dict(tests["current"][0], outcome="failed"),
        tests["current"][1],
    )
    changed = build_feedback(store, "current", baseline_run_id="baseline")
    assert changed.comparison.test_changes[0]["node_id"] == "test.py::test_case"
    comparison = changed.comparison.model_dump(mode="json")
    assert comparison["test_changes"][0]["baseline"] == [
        {"outcome": "passed"},
        {"outcome": "passed"},
    ]


def test_real_direct_plugin_sqlite_two_runs_capture_tool_description_change(
    tmp_path: Path,
):
    """Exercise the complete agent feedback path with no fake reports.

    Each subprocess runs the actual pytest plugin, SDK direct client, MCP
    low-level in-process server, matcher recording, and SQLite store.  The
    only edit between runs is the server-owned description.
    """
    source = """
import os
from mcp import types
from mcp.server.lowlevel import Server
from m3 import MCPTestKit, expect
from m3.storage import SQLiteExecutionStore
from m3.types import InProcessServer

def _server():
    async def list_tools(_context, _params):
        return types.ListToolsResult(tools=[types.Tool(
            name="lookup",
            description=os.environ["M3_DESCRIPTION"],
            inputSchema={"type": "object"},
        )])
    return Server("feedback-fixture", on_list_tools=list_tools)

def test_server_catalog():
    store = SQLiteExecutionStore(os.environ["M3_DATABASE"])
    try:
        with MCPTestKit(store=store, env={}, cwd=os.getcwd(), record_checks=True) as kit:
            with kit.direct(InProcessServer(name="orders", factory=_server)) as client:
                page = client.list_tools()
                assert page.tools[0].description == os.environ["M3_DESCRIPTION"]
            trace = client.final_trace
            expect(trace).to_have_trace()
    finally:
        store.close()
"""
    test_file = tmp_path / "test_server.py"
    test_file.write_text(source, encoding="utf-8")
    database = tmp_path / "runs.sqlite"
    sdk_source = str(Path(__file__).parents[2] / "src")

    def run(description: str, baseline_id: str | None = None):
        env = os.environ.copy()
        env["PYTHONPATH"] = sdk_source + os.pathsep + env.get("PYTHONPATH", "")
        env["M3_DATABASE"] = str(database)
        env["M3_DESCRIPTION"] = description
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--results-db",
            str(database),
        ]
        if baseline_id is not None:
            command.extend(["--baseline", baseline_id])
        command.append(str(test_file))
        return subprocess.run(
            command,
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    first = run("Find orders by their ID.")
    assert first.returncode == 0, first.stdout + first.stderr
    store = SQLiteExecutionStore(database)
    try:
        runs = store.list_test_runs()
        assert len(runs) == 1
        baseline_id = str(runs[0]["run_id"])
        baseline_report = tmp_path / ".m3" / "reports" / baseline_id / "feedback.json"
        baseline_payload = json.loads(baseline_report.read_text(encoding="utf-8"))
        executions = store.list_executions(run_id=baseline_id).items
        assert len(executions) == 1
        baseline_execution = executions[0].execution_id.root
        baseline_execution_report = store.get_report(baseline_execution)
        assert baseline_execution_report is not None
        assert any(
            event.payload.get("method") == "tools/list"
            for event in baseline_execution_report.events
        )
        assert [record.name for record in store.evaluations(baseline_execution)] == [
            "m3.matcher.to_have_trace.v1"
        ]
        assert len(store.list_test_results(baseline_id)) == 1
    finally:
        store.close()

    second = run("Look up an order using its identifier.", baseline_id)
    assert second.returncode == 0, second.stdout + second.stderr
    store = SQLiteExecutionStore(database)
    try:
        runs = store.list_test_runs()
        assert len(runs) == 2
        current_id = str(
            next(item["run_id"] for item in runs if str(item["run_id"]) != baseline_id)
        )
        current_report = tmp_path / ".m3" / "reports" / current_id / "feedback.json"
        payload = json.loads(current_report.read_text(encoding="utf-8"))
        executions = store.list_executions(run_id=current_id).items
        assert len(executions) == 1
        current_execution = executions[0].execution_id.root
        current_execution_report = store.get_report(current_execution)
        assert current_execution_report is not None
        assert any(
            event.payload.get("method") == "tools/list"
            for event in current_execution_report.events
        )
        assert [record.name for record in store.evaluations(current_execution)] == [
            "m3.matcher.to_have_trace.v1"
        ]
        assert len(store.list_test_results(current_id)) == 1
    finally:
        store.close()

    generated_changes = [
        item
        for item in payload["comparison"]["interface_changes"]
        if item.get("tool") == "lookup"
    ]
    assert len(generated_changes) == 1
    assert generated_changes[0]["before"]["description"] == "Find orders by their ID."
    assert (
        generated_changes[0]["after"]["description"]
        == "Look up an order using its identifier."
    )

    # The same persisted SQLite rows should also be sufficient for a later
    # API/service caller to rebuild the comparison deterministically.
    store = SQLiteExecutionStore(database)
    try:
        compared = build_feedback(store, current_id, baseline_run_id=baseline_id)
    finally:
        store.close()
    [
        item
        for item in compared.comparison.interface_changes
        if item.get("tool") == "lookup"
    ]
    rebuilt_changes = json.loads(compared.model_dump_json())["comparison"][
        "interface_changes"
    ]
    assert rebuilt_changes == generated_changes
    assert payload["run_id"] == current_id
    assert baseline_payload["run_id"] == baseline_id
