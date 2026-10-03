from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from m3._wire import internalize_request, neutralize_response
from m3.events import EventFactory, EventSequence
from m3.feedback import Feedback, build_feedback, export_feedback
from m3.hosted_comparison import _RunStore, compare_runs, comparison_input
from m3.types import (
    CallTool,
    DirectSpec,
    EvaluationId,
    EvaluationRecord,
    EvaluationStatus,
    EventDirection,
    EventId,
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionReport,
    ExecutionSpec,
    ExecutionState,
    ExecutionStatus,
    RequestLink,
    ServerBinding,
    StdioServer,
    SuiteId,
)


def _run(run_id: str, outcome: str, suite_id: int) -> dict:
    feedback = Feedback(
        run_id=run_id,
        run_label=f"Run {run_id}",
        tests=(
            {
                "attempt_id": f"{run_id}:1",
                "node_id": "tests/test_api.py::test_list",
                "suite_id": suite_id,
                "outcome": outcome,
                "verdict": outcome,
                "effective_verdict": outcome,
            },
        ),
    ).model_dump(mode="json")
    return {
        "run_id": run_id,
        "feedback": {
            "version": "v2",
            "feedback": neutralize_response(f"/api/v2/feedback/{run_id}", feedback),
        },
        "suite_ids": {str(suite_id): 9001},
        "comparison_input": {
            "schema_version": 1,
            "manifest": {
                "run_id": run_id,
                "run_label": f"Run {run_id}",
                "status": "finished",
                "project_id": "project-a",
                "selection": ["tests/test_api.py::test_list"],
            },
            "test_results": [
                {
                    "attempt_id": f"{run_id}:1",
                    "node_id": "tests/test_api.py::test_list",
                    "suite_id": suite_id,
                    "outcome": outcome,
                    "phases": {},
                    "execution_ids": [],
                }
            ],
        },
        "reports": [],
    }


def _report_envelope(
    run_id: str,
    execution_id: str,
    suite_id: int,
    description: str,
    scores: tuple[float, ...],
    config: str = "model-a",
) -> dict:
    factory = EventFactory(
        execution_id,
        allocator=EventSequence(start=0),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    events = (
        factory.create(
            EventKind.MCP_REQUEST,
            event_id=EventId(f"{execution_id}-request"),
            connection_id="connection",
            server_binding="orders",
            correlation=RequestLink(
                jsonrpc_id=1,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=1,
            ),
            payload={"method": "tools/list", "params": {}},
            monotonic_offset_ms=0,
        ),
        factory.create(
            EventKind.MCP_RESPONSE,
            event_id=EventId(f"{execution_id}-response"),
            connection_id="connection",
            server_binding="orders",
            correlation=RequestLink(
                jsonrpc_id=1,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=1,
            ),
            payload={
                "method": "tools/list",
                "result": {
                    "tools": [
                        {
                            "name": "lookup",
                            "description": description,
                            "inputSchema": {"type": "object"},
                        }
                    ]
                },
            },
            monotonic_offset_ms=1,
        ),
    )
    snapshot = ExecutionState(
        execution_id=ExecutionId(execution_id),
        run_id=run_id,
        suite_id=SuiteId(suite_id),
        suite_name="catalog",
        lifecycle=ExecutionStatus.FINISHED,
        outcome=ExecutionOutcome.COMPLETED,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    evaluations = tuple(
        EvaluationRecord(
            evaluation_id=EvaluationId(f"{execution_id}-{index}"),
            execution_id=snapshot.execution_id,
            case_id="tests/test_api.py::test_list",
            name="quality",
            status=EvaluationStatus.PASSED if score >= 0.5 else EvaluationStatus.FAILED,
            score=score,
            metadata={
                "harness_config": config,
                "m3.matrix.trial": f"trial-{index}",
            },
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        for index, score in enumerate(scores, 1)
    )
    report = ExecutionReport(
        snapshot=snapshot,
        events=events,
        evaluations=evaluations,
        event_count=len(events),
    )
    spec = DirectSpec(
        suite_name="catalog",
        metadata={"harness_config": config},
        servers=(
            ServerBinding(
                server=StdioServer(name="orders", command="orders"),
                alias="orders",
            ),
        ),
        operation=CallTool(server="orders", name="lookup", arguments={}),
    )
    envelope = {
        "version": "v2",
        "execution_id": execution_id,
        "spec": spec.model_dump(mode="json", by_alias=True),
        "report": report.model_dump(mode="json", by_alias=True),
        "trace": {
            "schema_id": "m3.trace_view",
            "summary": {
                "tool_call_count": 0,
                "successful_tool_call_count": 0,
                "failed_tool_call_count": 0,
            },
        },
        "test_results": [],
    }
    return neutralize_response(f"/api/v2/executions/{execution_id}/report", envelope)


def _run_with_report(
    run_id: str,
    suite_id: int,
    description: str,
    scores: tuple[float, ...],
    config: str = "model-a",
) -> dict:
    run = _run(run_id, "passed", suite_id)
    execution_id = f"execution-{run_id}"
    run["suite_ids"] = {str(suite_id): 9001}
    run["reports"] = [
        _report_envelope(run_id, execution_id, suite_id, description, scores, config)
    ]
    run["comparison_input"]["test_results"][0]["execution_ids"] = [execution_id]
    return run


def test_arbitrary_baseline_uses_requested_pair_and_hosted_suite_identity() -> None:
    baseline_a = _run("A", "passed", 10)
    baseline_b = _run("B", "failed", 20)
    current_c = _run("C", "passed", 30)
    # An upload-time comparison embedded in C must never replace the selected
    # baseline. A real stale comparison here would be C versus B.
    current_c["feedback"]["feedback"]["comparison"] = {
        "outdated_unvalidated_shape": object(),
    }

    against_a = compare_runs({"current": current_c, "baseline": baseline_a})
    against_b = compare_runs({"current": current_c, "baseline": baseline_b})

    assert (against_a["current_run_id"], against_a["baseline_run_id"]) == ("C", "A")
    assert (against_b["current_run_id"], against_b["baseline_run_id"]) == ("C", "B")
    assert against_a["test_changes"] == []
    assert against_b["test_changes"] == [
        {
            "suite_id": 9001,
            "node_id": "tests/test_api.py::test_list",
            "baseline": [{"outcome": "failed", "effective_verdict": "failed"}],
            "current": [{"outcome": "passed", "effective_verdict": "passed"}],
        }
    ]


def test_legacy_run_comparison_is_explicitly_limited() -> None:
    baseline = _run("old", "passed", 1)
    current = _run("new", "failed", 2)
    baseline.pop("comparison_input")
    current.pop("comparison_input")

    result = compare_runs({"current": current, "baseline": baseline})

    assert any("legacy feedback projection" in item for item in result["limitations"])


def test_suite_remapping_does_not_rewrite_opaque_user_payloads() -> None:
    run = _run("opaque", "passed", 1)
    run["comparison_input"]["manifest"]["user_value"] = {
        "context": {"suite_id": 1},
        "suite_id": 1,
    }
    run["comparison_input"]["test_results"][0]["metadata"] = {"suite_id": 1}
    other = _run("other", "passed", 2)

    store = _RunStore("opaque", run, {"1": 9001})
    result = compare_runs({"current": run, "baseline": other})

    assert result["current_run_id"] == "opaque"
    assert result["baseline_run_id"] == "other"
    assert store.manifest["user_value"] == {
        "context": {"suite_id": 1},
        "suite_id": 1,
    }
    assert store.test_results[0]["metadata"] == {"suite_id": 1}
    assert store.test_results[0]["suite_id"] == 9001


def test_rejects_duplicate_execution_identity_and_foreign_manifest() -> None:
    current = _run_with_report("current", 11, "current", (0.8,))
    baseline = _run_with_report("baseline", 22, "baseline", (0.2,))
    baseline["reports"] = [
        _report_envelope("baseline", "execution-current", 22, "baseline", (0.2,))
    ]
    baseline["comparison_input"]["test_results"][0]["execution_ids"] = [
        "execution-current"
    ]
    with pytest.raises(ValueError, match="duplicated across runs"):
        compare_runs({"current": current, "baseline": baseline})

    baseline = _run("baseline", "passed", 22)
    baseline["comparison_input"]["manifest"]["run_id"] = "foreign-run"
    with pytest.raises(ValueError, match="manifest run identity mismatch"):
        compare_runs({"current": current, "baseline": baseline})


class _CanonicalStore:
    def __init__(self, reports, manifests, attempts, specs):
        self.reports = {report.snapshot.execution_id.root: report for report in reports}
        self.manifests = manifests
        self.attempts = attempts
        self.specs = specs

    def list_executions(self, *, run_id, limit=100, offset=0):
        values = [
            report.snapshot
            for report in self.reports.values()
            if report.snapshot.run_id.root == str(run_id)
        ]
        return ExecutionPage(
            items=tuple(values[offset : offset + limit]),
            limit=limit,
            offset=offset,
            total=len(values),
        )

    def get_report(self, execution_id):
        return self.reports.get(str(getattr(execution_id, "root", execution_id)))

    def get_execution_spec(self, execution_id):
        return self.specs.get(str(getattr(execution_id, "root", execution_id)))

    def get_trace_view(self, _execution_id):
        return SimpleNamespace(
            summary=SimpleNamespace(
                tool_call_count=0,
                successful_tool_call_count=0,
                failed_tool_call_count=0,
            )
        )

    def get_test_run(self, run_id):
        return self.manifests.get(str(run_id))

    def list_test_results(self, run_id):
        return tuple(self.attempts.get(str(run_id), ()))

    def get_project(self, _project_id):
        return None


def hosted_golden_fixture() -> dict:
    """Three uploaded runs and expected C→A/C→B outputs from build_feedback."""
    specifications = (
        ("A", 10, "old description", (0.2, 0.4), "model-a", "passed"),
        ("B", 20, "middle description", (0.3, 0.3), "model-b", "failed"),
        ("C", 30, "current description", (0.8, 0.9), "model-a", "passed"),
    )
    dtos: dict[str, dict] = {}
    typed_reports = []
    typed_specs = {}
    manifests = {}
    attempts: dict[str, list[dict]] = {}
    for run_id, local_suite_id, description, scores, config, outcome in specifications:
        dto = _run_with_report(run_id, local_suite_id, description, scores, config)
        raw = dto["reports"][0]
        internal = internalize_request(
            f"/api/v2/executions/execution-{run_id}/report", raw
        )
        report = ExecutionReport.model_validate(internal["report"])
        typed_specs[f"execution-{run_id}"] = TypeAdapter(ExecutionSpec).validate_python(
            internal["spec"]
        )
        mapped_evaluations = tuple(
            item.model_copy(update={"suite_id": SuiteId(9001)})
            for item in report.evaluations
        )
        report = report.model_copy(
            update={
                "snapshot": report.snapshot.model_copy(
                    update={"suite_id": SuiteId(9001)}
                ),
                "evaluations": mapped_evaluations,
            }
        )
        typed_reports.append(report)
        # The raw pytest records include two stable trials for the primary
        # case and distinct added/removed cases across the three runs.
        raw_attempts = [
            {
                "attempt_id": f"{run_id}:main:1",
                "node_id": "tests/test_api.py::test_list",
                "suite_id": 9001,
                "suite_name": "catalog",
                "outcome": outcome,
                "execution_ids": [f"execution-{run_id}"],
                "phases": {},
                "metadata": {"harness_config": config, "m3.matrix.trial": "trial-1"},
            },
            {
                "attempt_id": f"{run_id}:main:2",
                "node_id": "tests/test_api.py::test_list",
                "suite_id": 9001,
                "suite_name": "catalog",
                "outcome": outcome,
                "execution_ids": [f"execution-{run_id}"],
                "phases": {},
                "metadata": {"harness_config": config, "m3.matrix.trial": "trial-2"},
            },
        ]
        if run_id == "A":
            raw_attempts.append(
                {
                    "attempt_id": "A:removed",
                    "node_id": "tests/test_api.py::test_removed",
                    "suite_id": 9001,
                    "outcome": "passed",
                    "execution_ids": [],
                    "phases": {},
                }
            )
        if run_id == "C":
            raw_attempts.append(
                {
                    "attempt_id": "C:added",
                    "node_id": "tests/test_api.py::test_added",
                    "suite_id": 9001,
                    "outcome": "passed",
                    "execution_ids": [],
                    "phases": {},
                }
            )
        attempts[run_id] = raw_attempts
        manifests[run_id] = {
            "run_id": run_id,
            "run_label": f"Run {run_id}",
            "status": "finished",
            "project_id": "project-a",
            "selection": ["tests/test_api.py"],
        }
        dto["comparison_input"]["test_results"] = [
            {**item, "suite_id": local_suite_id} for item in raw_attempts
        ]
        dto["comparison_input"]["manifest"] = manifests[run_id]
        # Preserve an opaque user-owned suite_id-looking string in exported
        # evidence to pin the wire and identity remapping boundary.
        dto["comparison_input"]["manifest"]["user_value"] = {
            "context": {"suite_id": local_suite_id},
            "suite_id": f"local-{local_suite_id}",
        }
        dtos[run_id] = dto

    canonical = _CanonicalStore(typed_reports, manifests, attempts, typed_specs)
    comparisons = []
    for baseline_id in ("A", "B"):
        expected = build_feedback(
            canonical, "C", baseline_run_id=baseline_id
        ).comparison
        assert expected is not None
        comparisons.append(
            {
                "current_run_id": "C",
                "baseline_run_id": baseline_id,
                "expected": neutralize_response(
                    "/api/v2/feedback/{run_id}", expected.model_dump(mode="json")
                ),
            }
        )
    return {"runs": [dtos["A"], dtos["B"], dtos["C"]], "comparisons": comparisons}


def test_hosted_worker_matches_sdk_golden_for_three_runs() -> None:
    fixture = hosted_golden_fixture()
    checked_in = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "hosted-comparison.json").read_text()
    )
    assert fixture == checked_in
    by_id = {run["run_id"]: run for run in fixture["runs"]}
    for expected in fixture["comparisons"]:
        actual = compare_runs(
            {
                "current": by_id[expected["current_run_id"]],
                "baseline": by_id[expected["baseline_run_id"]],
            }
        )
        assert actual == expected["expected"]
    against_a = compare_runs({"current": by_id["C"], "baseline": by_id["A"]})
    assert any(
        item.get("tool") == "lookup"
        and item.get("before", {}).get("description") == "old description"
        for item in against_a["interface_changes"]
    )


def test_real_exported_feedback_compares_with_sdk_golden(tmp_path: Path) -> None:
    fixture = hosted_golden_fixture()
    for run in fixture["runs"]:
        local_map = {key: int(key) for key in run["suite_ids"]}
        store = _RunStore(run["run_id"], run, local_map)
        value = build_feedback(store, run["run_id"])
        exported = json.loads(
            export_feedback(value, store, tmp_path / run["run_id"]).read_text()
        )
        assert "execution_files" in exported and "unavailable_references" in exported
        run["feedback"]["feedback"] = neutralize_response(
            "/api/v2/feedback/{run_id}", exported
        )
    by_id = {run["run_id"]: run for run in fixture["runs"]}
    for pair in fixture["comparisons"]:
        assert (
            compare_runs(
                {
                    "current": by_id[pair["current_run_id"]],
                    "baseline": by_id[pair["baseline_run_id"]],
                }
            )
            == pair["expected"]
        )


@pytest.mark.parametrize("version", [True, 2, "1"])
def test_unknown_comparison_input_version_is_explicitly_rejected(version) -> None:
    current = _run("current", "passed", 11)
    current["comparison_input"]["schema_version"] = version
    with pytest.raises(ValueError, match="unsupported comparison input"):
        compare_runs({"current": current, "baseline": _run("baseline", "failed", 22)})


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "field,value", [("run_id", "baseline"), ("execution_ids", ["execution-baseline"])]
)
def test_attempt_cannot_borrow_another_runs_identity(legacy, field, value) -> None:
    current = _run_with_report("current", 11, "current", (0.8,))
    baseline = _run_with_report("baseline", 22, "baseline", (0.2,))
    if legacy:
        current.pop("comparison_input")
        attempt = current["feedback"]["feedback"]["tests"][0]
    else:
        attempt = current["comparison_input"]["test_results"][0]
    attempt[field] = value
    with pytest.raises(ValueError, match="test attempt"):
        compare_runs({"current": current, "baseline": baseline})


def test_compact_evidence_excludes_machine_data_and_reuses_diagnostics(
    tmp_path: Path,
) -> None:
    manifest = {
        "run_id": "current",
        "project_root": "/private/checkout",
        "status": "finished",
        "selection": [
            "tests/test_login.py",
            "--db-url=postgres://u:hunter2@db/x",
            "--password",
            "raw-credential",
            "unrelated.py",
            "/private/checkout/tests/test_login.py",
        ],
        "capture": {"token": "private"},
    }
    tests = [
        {
            "run_id": "current",
            "attempt_id": "attempt",
            "node_id": "/private/checkout/tests/test_login.py::test_ok",
            "suite_id": 11,
            "outcome": "failed",
            "execution_ids": [],
            "diagnostics": {"stdout": "x" * 7000},
            "phases": {"call": {"outcome": "failed", "longrepr": "x" * 7000}},
        }
    ]
    compact = comparison_input(manifest, tests)
    assert "hunter2" not in json.dumps(
        compact
    ) and "/private/checkout" not in json.dumps(compact)
    assert "raw-credential" not in json.dumps(compact)
    assert "unrelated.py" not in compact["manifest"]["selection"]
    assert "capture" not in compact["manifest"]
    assert "diagnostics" not in compact["test_results"][0]
    assert "longrepr" not in compact["test_results"][0]["phases"]["call"]
    current = _run("current", "passed", 11)
    current["comparison_input"] = compact
    current["feedback"]["feedback"]["tests"] = [
        {**tests[0], "node_id": "tests/test_login.py::test_ok"}
    ]
    store = _RunStore("current", current, {"11": 9001})
    assert store.test_results[0]["diagnostics"] == tests[0]["diagnostics"]


def test_worker_classifies_permanent_invalid_input(monkeypatch) -> None:
    import io

    import m3.hosted_comparison as worker

    source = io.BytesIO(b"{invalid")
    destination = io.BytesIO()
    monkeypatch.setattr(worker.sys, "stdin", SimpleNamespace(buffer=source))
    monkeypatch.setattr(worker.sys, "stdout", SimpleNamespace(buffer=destination))
    assert worker.main() == 1
    assert json.loads(destination.getvalue()) == {"error": "comparison_incompatible"}


def test_committed_cli_export_fixture_matches_golden(tmp_path: Path) -> None:
    fixture = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "hosted-comparison-exported.json"
        ).read_text()
    )
    generated = hosted_golden_fixture()
    for run in generated["runs"]:
        store = _RunStore(
            run["run_id"], run, {key: int(key) for key in run["suite_ids"]}
        )
        exported = json.loads(
            export_feedback(
                build_feedback(store, run["run_id"]), store, tmp_path / run["run_id"]
            ).read_text()
        )
        run["feedback"]["feedback"] = neutralize_response(
            "/api/v2/feedback/{run_id}", exported
        )
        run["comparison_input"] = comparison_input(store.manifest, store.test_results)
    assert fixture == generated
    by_id = {run["run_id"]: run for run in fixture["runs"]}
    for pair in fixture["comparisons"]:
        assert (
            compare_runs(
                {
                    "current": by_id[pair["current_run_id"]],
                    "baseline": by_id[pair["baseline_run_id"]],
                }
            )
            == pair["expected"]
        )


def test_compact_evidence_maps_reserved_detached_evaluator_identity() -> None:
    evidence = comparison_input(
        {"run_id": "current"},
        [
            {
                "attempt_id": "a",
                "metadata": {"m3.case_id": "user-value"},
                "detached_evaluations": [
                    {
                        "name": "m3.output.has_text.v1",
                        "evaluator": "m3.output.has_text.v1",
                        "required": True,
                        "status": "passed",
                        "details": {"user": "m3.output.has_text.v1"},
                    }
                ],
            }
        ],
    )
    detached = evidence["test_results"][0]["detached_evaluations"][0]
    assert detached["name"] == "output.has_text.v1"
    assert detached["evaluator"] == "output.has_text.v1"
    assert detached["details"]["user"] == "m3.output.has_text.v1"
    restored = internalize_request("/api/v2/feedback/current", evidence)
    assert (
        restored["test_results"][0]["detached_evaluations"][0]["name"]
        == "m3.output.has_text.v1"
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_missing_attempt_execution_is_ignored_like_local_feedback(legacy) -> None:
    current = _run_with_report("current", 11, "current", (0.8,))
    baseline = _run_with_report("baseline", 22, "baseline", (0.2,))
    if legacy:
        current.pop("comparison_input")
        attempt = current["feedback"]["feedback"]["tests"][0]
    else:
        attempt = current["comparison_input"]["test_results"][0]
    attempt["execution_ids"] = ["execution-current", "deleted-or-unassociated"]
    result = compare_runs({"current": current, "baseline": baseline})
    attempt["execution_ids"] = ["execution-current"]
    assert result == compare_runs({"current": current, "baseline": baseline})


def test_absolute_selection_does_not_change_monorepo_identity() -> None:
    current = _run("current", "failed", 11)
    baseline = _run("baseline", "passed", 22)
    for run in (current, baseline):
        manifest = run["comparison_input"]["manifest"]
        manifest["project_root"] = "/ci/repo/pkg"
        manifest["selection"] = ["/ci/repo/pkg/tests/test_api.py"]
        run["comparison_input"]["test_results"][0]["node_id"] = (
            "pkg/tests/test_api.py::test_list"
        )
    original = compare_runs({"current": current, "baseline": baseline})
    for run in (current, baseline):
        raw = run["comparison_input"]
        run["comparison_input"] = comparison_input(raw["manifest"], raw["test_results"])
        assert run["comparison_input"]["manifest"]["selection"] == []
    assert compare_runs({"current": current, "baseline": baseline}) == original


def test_detached_user_details_stay_opaque_in_both_wire_directions() -> None:
    value = {
        "test_results": [
            {
                "detached_evaluations": [
                    {
                        "name": "m3.matcher.foo.v1",
                        "details": {
                            "name": "matcher.foo.v1",
                            "nested": {"name": "m3.matcher.foo.v1"},
                        },
                    }
                ]
            }
        ]
    }
    wire = neutralize_response("/api/v2/feedback/current", value)
    restored = internalize_request("/api/v2/feedback/current", wire)
    assert restored == value


@pytest.mark.parametrize("limit", ["input", "output"])
def test_worker_bounds_input_and_output(monkeypatch, limit) -> None:
    import io

    import m3.hosted_comparison as worker

    payload = {
        "current": _run("current", "passed", 11),
        "baseline": _run("baseline", "failed", 22),
    }
    destination = io.BytesIO()
    monkeypatch.setattr(
        worker.sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode())),
    )
    monkeypatch.setattr(worker.sys, "stdout", SimpleNamespace(buffer=destination))
    monkeypatch.setattr(
        worker, "_MAX_INPUT_BYTES" if limit == "input" else "_MAX_OUTPUT_BYTES", 1
    )
    assert worker.main() == 1
    assert json.loads(destination.getvalue()) == {"error": "comparison_incompatible"}


def test_manifest_error_kind_cannot_override_failure_category() -> None:
    from m3.feedback import _manifest_failures

    failures = _manifest_failures(
        {"worker_errors": [{"kind": "node_down", "message": "stopped"}]}
    )
    assert failures == ({"kind": "worker", "message": "stopped"},)
