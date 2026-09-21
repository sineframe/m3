from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import m3.feedback as feedback_module
from m3._test_runs import run_record, xfail_waives_required_evaluations
from m3.pytest_plugin import (
    _persist_manifest_not_run,
    _pytest_sessionfinish,
    _required_evaluation_issues,
)
from m3.storage import SQLiteExecutionStore


def _run(
    tmp_path: Path, source: str, *extra: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    test_file = tmp_path / "test_case.py"
    test_file.write_text(source, encoding="utf-8")
    database = tmp_path / "results.sqlite"
    env = os.environ.copy()
    sdk_source = str(Path(__file__).parents[2] / "src")
    env["PYTHONPATH"] = sdk_source + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--results-db",
            str(database),
            *extra,
            str(test_file),
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, database


def _manifest(database: Path) -> tuple[SQLiteExecutionStore, str, dict]:
    store = SQLiteExecutionStore(database)
    records = store.list_test_runs()
    assert len(records) == 1
    record = dict(records[0])
    return store, str(record["run_id"]), record


def test_printed_scores_are_diagnostics_not_evaluations(tmp_path: Path) -> None:
    result, database = _run(
        tmp_path,
        "def test_print_only():\n    print('Score: 0.99')\n",
    )
    assert result.returncode == 0
    assert "\nM3 run " in result.stdout
    store, run_id, record = _manifest(database)
    try:
        attempts = store.list_test_results(run_id)
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "passed"
        assert attempts[0]["execution_ids"] == []
        assert store.get_test_run(run_id)["status"] == "finished"
        report = tmp_path / ".m3" / "reports" / run_id / "feedback.json"
        assert json.loads(report.read_text(encoding="utf-8"))["run_id"] == run_id
        assert record["capture"]["mode"] == "fd"
    finally:
        store.close()


def test_control_plane_environment_does_not_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("M3_CONTROL_PLANE_URL", "https://unreachable.invalid")
    monkeypatch.setenv("M3_CONTROL_PLANE_TOKEN", "m3pat_not-used")
    result, database = _run(tmp_path, "def test_local_only():\n    assert True\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "control-plane" not in result.stdout
    store, run_id, _ = _manifest(database)
    try:
        assert (tmp_path / ".m3" / "reports" / run_id / "feedback.json").is_file()
        assert not (tmp_path / ".m3" / "reports" / run_id / "control-plane").exists()
    finally:
        store.close()


def test_setup_failure_is_recorded_as_error(tmp_path: Path) -> None:
    result, database = _run(
        tmp_path,
        "import pytest\n@pytest.fixture\ndef broken():\n    raise RuntimeError('fixture failed')\ndef test_broken(broken):\n    pass\n",
    )
    assert result.returncode == 1
    store, run_id, _ = _manifest(database)
    try:
        attempts = store.list_test_results(run_id)
        assert attempts[0]["outcome"] == "error"
        assert "setup:longrepr" in attempts[0]["diagnostics"]
    finally:
        store.close()


def test_caught_required_evaluation_still_blocks_and_preserves_passed_attempt(
    tmp_path: Path,
) -> None:
    source = """
from m3 import EvaluationStatus, ExecutionId, ExecutionState
from m3.evaluations import RequiredEvaluationError

def test_caught(m3_kit):
    execution_id = ExecutionId('execution-caught-required')
    m3_kit.store.create(ExecutionState(execution_id=execution_id), run_id=m3_kit.run_id)
    m3_kit.register_evaluator('required.v1', lambda _context: EvaluationStatus.ERROR)
    try:
        m3_kit.evaluate({}, 'required.v1', required=True, execution_id=execution_id)
    except RequiredEvaluationError:
        pass
"""
    result, database = _run(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr
    store, run_id, _ = _manifest(database)
    try:
        manifest = dict(store.get_test_run(run_id))
        assert manifest["exit_status"] == 1
        attempts = store.list_test_results(run_id)
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "passed"
    finally:
        store.close()


def test_caught_unlinked_required_evaluation_still_blocks(tmp_path: Path) -> None:
    source = """
from m3 import EvaluationStatus
from m3.evaluations import RequiredEvaluationError

def test_caught_without_execution(m3_kit):
    m3_kit.register_evaluator('required.v1', lambda _context: EvaluationStatus.ERROR)
    try:
        m3_kit.evaluate({}, 'required.v1', required=True)
    except RequiredEvaluationError:
        pass
"""
    result, database = _run(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr
    store, run_id, _ = _manifest(database)
    try:
        attempts = store.list_test_results(run_id)
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "passed"
        assert attempts[0]["execution_ids"] == []
        detached = attempts[0]["detached_evaluations"]
        assert len(detached) == 1
        assert detached[0]["evaluation_id"]
        assert detached[0]["name"] == "required.v1"
        assert detached[0]["status"] == "error"
        assert detached[0]["required"] is True
        report_path = tmp_path / ".m3" / "reports" / run_id / "feedback.json"
        feedback = json.loads(report_path.read_text(encoding="utf-8"))
        test = feedback["tests"][0]
        assert test["outcome"] == "passed"
        assert test["effective_verdict"] == "incomplete"
        assert test["evaluations"] == [
            {
                "kind": "evaluation",
                "evaluation_id": detached[0]["evaluation_id"],
                "execution_id": None,
                "evaluator": "required.v1",
                "status": "error",
                "required": True,
                "score": None,
                "rationale": None,
                "message": None,
                "metrics": {},
                "details": {},
            }
        ]
        assert test["evaluation_completeness"]["status"] == "not_applicable"
        assert test["evaluation_reasons"] == [
            {
                "kind": "incomplete_evaluation",
                "evaluation_id": detached[0]["evaluation_id"],
                "execution_id": None,
                "evaluator": "required.v1",
                "status": "error",
            }
        ]
        assert feedback["summary"]["effective_verdict_counts"]["incomplete"] == 1
        assert any(
            item.get("evaluation_id") == detached[0]["evaluation_id"]
            for item in feedback["failures"]
        )
        manifest_path = (
            tmp_path / ".m3" / "reports" / run_id / feedback["test_run_files"][run_id]
        )
        exported_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert exported_manifest["exit_status"] == 1
        assert exported_manifest["test_outcome_counts"] == {"passed": 1}
        assert exported_manifest["effective_verdict_counts"] == {"incomplete": 1}
    finally:
        store.close()


def test_pytest_failure_keeps_two_evaluations_independent_in_feedback(
    tmp_path: Path,
) -> None:
    source = """
from datetime import datetime, timezone

from m3 import (
    EvaluationDecision,
    EvaluationStatus,
    ExecutionId,
    ExecutionOutcome,
    ExecutionState,
    ExecutionStatus,
)

def first_evaluation(_context):
    return EvaluationDecision(status=EvaluationStatus.PASSED)

def second_evaluation(_context):
    return EvaluationDecision(status=EvaluationStatus.PASSED)

def test_evaluations_then_assertion(m3_kit):
    execution_id = ExecutionId('execution-two-passes')
    m3_kit.store.create(
        ExecutionState(
            execution_id=execution_id,
            lifecycle=ExecutionStatus.FINISHED,
            outcome=ExecutionOutcome.COMPLETED,
            finished_at=datetime.now(timezone.utc),
        ),
        run_id=m3_kit.run_id,
    )
    m3_kit.register_evaluator('quality.first', first_evaluation)
    m3_kit.register_evaluator('quality.second', second_evaluation)
    m3_kit.evaluate({}, 'quality.first', required=True, execution_id=execution_id)
    m3_kit.evaluate({}, 'quality.second', required=True, execution_id=execution_id)
    assert False, 'independent pytest failure'
"""
    result, database = _run(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr
    store, run_id, _ = _manifest(database)
    try:
        attempts = store.list_test_results(run_id)
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "failed"

        evaluations = tuple(store.evaluations("execution-two-passes"))
        assert {record.name for record in evaluations} == {
            "quality.first",
            "quality.second",
        }
        assert {record.status.value for record in evaluations} == {"passed"}
        assert {record.execution_id.root for record in evaluations} == {
            "execution-two-passes"
        }

        report_path = tmp_path / ".m3" / "reports" / run_id / "feedback.json"
        feedback = json.loads(report_path.read_text(encoding="utf-8"))
        test = feedback["tests"][0]
        assert test["outcome"] == "failed"
        assert test["verdict"] == "failed_assertion"
        assert test["effective_verdict"] == "failed"
        assert {item["evaluator"] for item in test["evaluations"]} == {
            "quality.first",
            "quality.second",
        }
        assert {item["status"] for item in test["evaluations"]} == {"passed"}
        assert {item["evaluation_id"] for item in test["evaluations"]} == {
            record.evaluation_id.root for record in evaluations
        }
        assert {item["execution_id"] for item in test["evaluations"]} == {
            "execution-two-passes"
        }
        assert test["evaluation_completeness"]["status"] == "complete"
        assert feedback["evaluation_stats"]["quality.first"]["pass_rate"] == 1.0
        assert feedback["evaluation_stats"]["quality.second"]["pass_rate"] == 1.0
    finally:
        store.close()


@pytest.mark.parametrize("status", ["ERROR", "INCONCLUSIVE", "NOT_RUN"])
def test_all_required_nonpass_statuses_block_after_being_caught(
    tmp_path: Path, status: str
) -> None:
    source = f"""
from m3 import EvaluationStatus, ExecutionId, ExecutionState
from m3.evaluations import RequiredEvaluationError

def test_required(m3_kit):
    execution_id = ExecutionId('execution-{status.lower()}')
    m3_kit.store.create(ExecutionState(execution_id=execution_id), run_id=m3_kit.run_id)
    m3_kit.register_evaluator('required.v1', lambda _context: EvaluationStatus.{status})
    try:
        m3_kit.evaluate({{}}, 'required.v1', required=True, execution_id=execution_id)
    except RequiredEvaluationError:
        pass
"""
    result, _ = _run(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize("status", ["ERROR", "FAILED"])
def test_skip_does_not_waive_required_failure(tmp_path: Path, status: str) -> None:
    source = f"""
import pytest
from m3 import EvaluationStatus, ExecutionId, ExecutionState
from m3.evaluations import RequiredEvaluationError

def test_skipped(m3_kit):
    execution_id = ExecutionId('execution-skip-{status.lower()}')
    m3_kit.store.create(ExecutionState(execution_id=execution_id), run_id=m3_kit.run_id)
    m3_kit.register_evaluator('required.v1', lambda _context: EvaluationStatus.{status})
    try:
        m3_kit.evaluate({{}}, 'required.v1', required=True, execution_id=execution_id)
    except RequiredEvaluationError:
        pass
    pytest.skip('ordinary skip')
"""
    result, _ = _run(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr


def test_valid_xfail_waives_all_linked_required_pairs(tmp_path: Path) -> None:
    source = """
import pytest
from m3 import EvaluationStatus, ExecutionId, ExecutionState
from m3.evaluations import RequiredEvaluationError

@pytest.mark.xfail(reason='expected protocol failure')
def test_expected_failure(m3_kit):
    for suffix, status in [('failed', EvaluationStatus.FAILED), ('error', EvaluationStatus.ERROR)]:
        execution_id = ExecutionId('execution-xfail-' + suffix)
        m3_kit.store.create(ExecutionState(execution_id=execution_id), run_id=m3_kit.run_id)
        m3_kit.register_evaluator('required.' + suffix, lambda _context, status=status: status)
        try:
            m3_kit.evaluate({}, 'required.' + suffix, required=True, execution_id=execution_id)
        except RequiredEvaluationError:
            pass
    assert False
"""
    result, database = _run(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr
    store, run_id, _ = _manifest(database)
    try:
        assert store.get_test_run(run_id)["exit_status"] == 0
    finally:
        store.close()


def test_xfail_with_setup_error_is_blocking(tmp_path: Path) -> None:
    source = """
import pytest

@pytest.fixture
def broken():
    raise RuntimeError('setup failed')

@pytest.mark.xfail(reason='setup is not expected to fail')
def test_setup_error(broken):
    pass
"""
    result, _ = _run(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize("strict, expected", [("False", 0), ("True", 1)])
def test_xpass_strictness(strict: str, expected: int, tmp_path: Path) -> None:
    source = f"""
import pytest
from m3 import ExecutionId, ExecutionState

@pytest.mark.xfail(reason='expected failure', strict={strict})
def test_xpass(m3_kit):
    execution_id = ExecutionId('execution-xpass')
    m3_kit.store.create(ExecutionState(execution_id=execution_id), run_id=m3_kit.run_id)
    m3_kit.register_evaluator('required.v1', lambda _context: True)
    m3_kit.evaluate({{}}, 'required.v1', required=True, execution_id=execution_id)
    assert True
"""
    result, _ = _run(tmp_path, source)
    assert result.returncode == expected, result.stdout + result.stderr


def test_xfail_helper_rejects_xpass() -> None:
    state = {"phases": {"call": {"outcome": "passed", "wasxfail": True}}}
    assert not xfail_waives_required_evaluations(state)


def test_manifest_not_run_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    source = """
def test_first():
    assert False

def test_second():
    assert True
"""
    result, database = _run(tmp_path, source, "--maxfail=1")
    assert result.returncode == 1, result.stdout + result.stderr
    store, run_id, manifest = _manifest(database)
    try:
        rows = store.list_test_results(run_id)
        not_run = [
            row for row in rows if row.get("record_source") == "manifest_not_run"
        ]
        assert len(not_run) == 1
        row = not_run[0]
        assert row["attempt_id"].startswith(f"{run_id}:manifest-not-run:")
        assert row["worker_id"] == "controller"
        assert row["outcome"] == row["verdict"] == "not_run"
        assert row["started_at"] is None
        assert row["finished_at"] == manifest["finished_at"]
        config = SimpleNamespace(_m3_manifest_write_error=False)
        assert _persist_manifest_not_run(config, store, run_id, dict(manifest))
        rows_after = store.list_test_results(run_id)
        assert [item["attempt_id"] for item in rows_after] == [
            item["attempt_id"] for item in rows
        ]
    finally:
        store.close()


def test_manifest_not_run_persistence_failure_has_no_pseudo_row() -> None:
    class FailingStore:
        def list_test_results(self, _run_id):
            return ()

        def save_test_result(self, *_args):
            raise OSError("database unavailable")

    config = SimpleNamespace(_m3_manifest_write_error=False)
    manifest = {"not_run_node_ids": ["test.py::test_missing"], "finished_at": "done"}
    assert not _persist_manifest_not_run(config, FailingStore(), "run", manifest)
    assert config._m3_manifest_write_error is True


def test_required_registration_missing_is_pending_until_terminal() -> None:
    execution = SimpleNamespace(
        execution_id=SimpleNamespace(root="execution-missing"),
        lifecycle=SimpleNamespace(value="running"),
    )
    page = SimpleNamespace(items=(execution,), total=1)
    spec = SimpleNamespace(
        evaluations=(SimpleNamespace(name="required.v1", required=True),)
    )

    class Store:
        def list_executions(self, **_kwargs):
            return page

        def get_execution_spec(self, _execution_id):
            return spec

        def evaluations(self, _execution_id):
            return ()

    attempt = ({"node_id": "test.py::test_missing", "outcome": "passed"},)
    assert _required_evaluation_issues(Store(), "run", attempt) == ()
    execution.lifecycle = SimpleNamespace(value="finished")
    issues = _required_evaluation_issues(Store(), "run", attempt)
    assert issues == ("missing required evaluation execution-missing:required.v1",)
    running_attempt = (
        {
            "node_id": "test.py::test_missing",
            "outcome": "running",
            "execution_ids": ["execution-missing"],
        },
    )
    assert _required_evaluation_issues(Store(), "run", running_attempt) == ()


def test_non_strict_xpass_with_failed_required_evaluation_is_failed() -> None:
    assert (
        feedback_module._effective_verdict(
            {
                "outcome": "passed",
                "phases": {"call": {"outcome": "passed", "wasxfail": True}},
            },
            "failed",
            valid_xfail=False,
            running=False,
        )
        == "failed"
    )


@pytest.mark.parametrize("pytest_status", [2, 3, 4])
def test_existing_pytest_exit_status_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pytest_status: int
) -> None:
    database = tmp_path / "status.sqlite"
    store = SQLiteExecutionStore(database)
    run_id = "run-existing-status"
    store.save_test_run(
        run_id,
        run_record(
            run_id,
            project_root=str(tmp_path),
            selection=(),
            capture={},
        ),
    )
    monkeypatch.setattr(
        feedback_module,
        "build_feedback",
        lambda *_args, **_kwargs: SimpleNamespace(tests=(), executions=()),
    )
    monkeypatch.setattr(
        feedback_module,
        "export_feedback",
        lambda *_args, **_kwargs: "report",
    )
    config = SimpleNamespace(
        _m3_database=str(database),
        _m3_run_id=SimpleNamespace(root=run_id),
        _m3_is_worker=False,
        _m3_manifest_store=store,
        _m3_baseline=None,
        _m3_project_root=tmp_path,
        pluginmanager=SimpleNamespace(getplugin=lambda _name: None),
    )
    session = SimpleNamespace(config=config, exitstatus=pytest_status)
    try:
        _pytest_sessionfinish(session, pytest_status)
        assert session.exitstatus == pytest_status
        assert store.get_test_run(run_id)["exit_status"] == pytest_status
    finally:
        store.close()


def test_feedback_failure_persists_terminal_manifest_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "feedback-failure.sqlite"
    store = SQLiteExecutionStore(database)
    run_id = "run-feedback-failure"
    store.save_test_run(
        run_id,
        run_record(run_id, project_root=str(tmp_path), selection=(), capture={}),
    )

    def fail_feedback(*_args, **_kwargs):
        raise RuntimeError("feedback failed")

    monkeypatch.setattr(feedback_module, "build_feedback", fail_feedback)
    config = SimpleNamespace(
        _m3_database=str(database),
        _m3_run_id=SimpleNamespace(root=run_id),
        _m3_is_worker=False,
        _m3_manifest_store=store,
        _m3_baseline=None,
        _m3_project_root=tmp_path,
        pluginmanager=SimpleNamespace(getplugin=lambda _name: None),
    )
    session = SimpleNamespace(config=config, exitstatus=0)
    try:
        _pytest_sessionfinish(session, 0)
        assert session.exitstatus == 1
        assert store.get_test_run(run_id)["exit_status"] == 1
    finally:
        store.close()


def test_counter_persistence_failure_sets_nonzero_and_finalizes_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "counter-failure.sqlite"
    backing_store = SQLiteExecutionStore(database)
    run_id = "run-counter-failure"
    backing_store.save_test_run(
        run_id,
        run_record(run_id, project_root=str(tmp_path), selection=(), capture={}),
    )

    class FailOnCounterSave:
        def __init__(self) -> None:
            self.save_count = 0

        def get_test_run(self, value):
            return backing_store.get_test_run(value)

        def save_test_run(self, value, record):
            self.save_count += 1
            if self.save_count == 2:
                raise OSError("counter persistence failed")
            return backing_store.save_test_run(value, record)

        def __getattr__(self, name):
            return getattr(backing_store, name)

    store = FailOnCounterSave()
    monkeypatch.setattr(
        feedback_module,
        "build_feedback",
        lambda *_args, **_kwargs: SimpleNamespace(tests=(), executions=()),
    )
    monkeypatch.setattr(
        feedback_module, "export_feedback", lambda *_args, **_kwargs: "report"
    )
    config = SimpleNamespace(
        _m3_database=str(database),
        _m3_run_id=SimpleNamespace(root=run_id),
        _m3_is_worker=False,
        _m3_manifest_store=store,
        _m3_baseline=None,
        _m3_project_root=tmp_path,
        pluginmanager=SimpleNamespace(getplugin=lambda _name: None),
    )
    session = SimpleNamespace(config=config, exitstatus=0)
    try:
        _pytest_sessionfinish(session, 0)
        assert session.exitstatus == 1
        assert backing_store.get_test_run(run_id)["exit_status"] == 1
        assert config._m3_manifest_write_error is True
    finally:
        backing_store.close()


def test_pytest_feedback_counts_cases_and_preserves_expected_tool_error(
    tmp_path: Path,
) -> None:
    source = (
        Path(__file__).parents[3]
        / "scripts"
        / "fixtures"
        / "verdicts"
        / "test_verdicts.py"
    ).read_text(encoding="utf-8")
    result, database = _run(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "2 failed, 2 passed, 1 error" in result.stdout
    assert (
        "M3 verdicts: 2 passed, 1 failed assertion, 1 protocol error, 1 setup error"
        in result.stdout
    )

    store, run_id, _ = _manifest(database)
    try:
        feedback_path = tmp_path / ".m3" / "reports" / run_id / "feedback.json"
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        assert feedback["summary"]["tests"] == 5
        assert feedback["summary"]["failures"] == 3
        tests = {item["node_id"].split("::")[-1]: item for item in feedback["tests"]}
        assert tests["test_expected_tool_error"]["verdict"] == "passed"
        assert tests["test_expected_tool_error"]["tool_result"] == "tool_error"
        assert tests["test_failed_matcher"]["verdict"] == "failed_assertion"
        assert tests["test_protocol_error"]["verdict"] == "protocol_error"
        assert tests["test_setup_error"]["verdict"] == "setup_error"
        assert len(feedback["failures"]) == 3
        assert any(item.get("evaluations") for item in feedback["failures"])
    finally:
        store.close()


def test_collection_error_still_writes_manifest(tmp_path: Path) -> None:
    result, database = _run(tmp_path, "def test_broken(:\n    pass\n")
    assert result.returncode != 0
    store, run_id, record = _manifest(database)
    try:
        assert record["status"] in {"finished", "interrupted", "incomplete"}
        assert record["collected_node_ids"] == []
        assert store.list_test_results(run_id) == ()
    finally:
        store.close()


def test_skipped_collection_does_not_count_as_failure(tmp_path: Path) -> None:
    result, database = _run(
        tmp_path,
        "import pytest\npytest.skip('optional dependency absent', allow_module_level=True)\n",
    )
    assert result.returncode == 5
    assert "1 skipped" in result.stdout
    store, run_id, _ = _manifest(database)
    try:
        feedback = json.loads(
            (tmp_path / ".m3" / "reports" / run_id / "feedback.json").read_text(
                encoding="utf-8"
            )
        )
        assert feedback["summary"]["failures"] == 0
        assert feedback["summary"]["collection_errors"] == 0
    finally:
        store.close()


def test_test_body_exception_is_not_labeled_as_assertion(tmp_path: Path) -> None:
    result, database = _run(
        tmp_path, "def test_crash():\n    raise RuntimeError('crashed')\n"
    )
    assert result.returncode == 1
    store, run_id, _ = _manifest(database)
    try:
        feedback = json.loads(
            (tmp_path / ".m3" / "reports" / run_id / "feedback.json").read_text(
                encoding="utf-8"
            )
        )
        assert feedback["tests"][0]["verdict"] == "pytest_error"
        assert "M3 verdicts: 1 pytest error" in result.stdout
    finally:
        store.close()


def test_plain_assertion_is_labeled_as_failed_assertion(tmp_path: Path) -> None:
    result, database = _run(tmp_path, "def test_plain():\n    assert 1 == 2\n")
    assert result.returncode == 1
    assert "AssertionError" in result.stdout
    store, run_id, _ = _manifest(database)
    try:
        feedback = json.loads(
            (tmp_path / ".m3" / "reports" / run_id / "feedback.json").read_text(
                encoding="utf-8"
            )
        )
        assert (
            feedback["tests"][0]["phases"]["call"]["exception_type"] == "AssertionError"
        )
        assert feedback["tests"][0]["verdict"] == "failed_assertion"
        assert "M3 verdicts: 1 failed assertion" in result.stdout
    finally:
        store.close()


def test_expected_protocol_error_does_not_mask_later_assertion(tmp_path: Path) -> None:
    source = """
from m3 import MCPTestKit
from m3.testing import FaultInjector
from m3.types import CallTool, DirectSpec, ServerBinding

def test_assertion_after_expected_protocol_error():
    fault = FaultInjector().protocol_error("tools/call", code=-32042)
    spec = DirectSpec(
        servers=(ServerBinding(server=fault.stdio_server(), alias="fault"),),
        operation=CallTool(server="fault", name="echo", arguments={}),
    )
    with MCPTestKit() as kit:
        result = kit.run(spec)
    if result.error is None or result.error.code.value != "protocol_error":
        raise RuntimeError("expected protocol error was not observed")
    assert False, "separate assertion failure"
"""
    result, database = _run(tmp_path, source)
    assert result.returncode == 1
    assert "separate assertion failure" in result.stdout
    store, run_id, _ = _manifest(database)
    try:
        feedback = json.loads(
            (tmp_path / ".m3" / "reports" / run_id / "feedback.json").read_text(
                encoding="utf-8"
            )
        )
        assert feedback["executions"][0]["result_kind"] == "protocol_error"
        assert feedback["tests"][0]["verdict"] == "failed_assertion"
        assert "M3 verdicts: 1 failed assertion" in result.stdout
    finally:
        store.close()


def test_s_capture_is_reported_unavailable(tmp_path: Path) -> None:
    result, database = _run(
        tmp_path, "def test_print_only():\n    print('diagnostic')\n", "-s"
    )
    assert result.returncode == 0
    store, run_id, record = _manifest(database)
    try:
        assert record["capture"]["mode"] == "no"
        assert store.list_test_results(run_id)[0]["diagnostics"] == {}
    finally:
        store.close()


def test_manifest_only_baseline_is_accepted(tmp_path: Path) -> None:
    first, database = _run(tmp_path, "def test_one():\n    pass\n")
    assert first.returncode == 0
    store, run_id, _ = _manifest(database)
    store.close()
    second, _ = _run(tmp_path, "def test_one():\n    pass\n", "--baseline", run_id)
    assert second.returncode == 0


def test_cli_filters_legacy_harness_matrix_cases_at_collection(tmp_path: Path) -> None:
    source = """
from m3.matrix import HarnessCase, HarnessMatrix, ServerCase, ToolCase
from m3.types import OpenCode, StdioServer
import sys
server = ServerCase(name="s", server=StdioServer(name="s", command=sys.executable), tools=(ToolCase(name="x"),))
matrix = HarnessMatrix.each_server(servers=(server,), harnesses=(
    HarnessCase(name="one", harness=OpenCode(model="provider/one")),
    HarnessCase(name="two", harness=OpenCode(model="provider/two")),
))
@matrix.parametrize()
def test_case(case):
    assert case.harness.harness.model == "provider/two"
"""
    result, _ = _run(tmp_path, source, "--harness", "opencode=provider/two")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "1 deselected" in result.stdout


def test_global_credential_mapping_applies_to_marked_agents_without_harness_cli(
    tmp_path: Path,
) -> None:
    source = """
import os, pytest
from m3 import StdioServer, UserMessage
os.environ["MARKED_SOURCE"] = "sentinel"
pytestmark = pytest.mark.m3(agents=[{"harness": "opencode", "models": ["vendor/model"]}])
def test_marked(agent):
    spec = agent._spec(UserMessage(content="x"), server=StdioServer(name="s", command="echo"))
    assert spec.harness.credential_references["VENDOR_KEY"].name == "MARKED_SOURCE"
"""
    result, _ = _run(tmp_path, source, "--credential-env", "VENDOR_KEY=MARKED_SOURCE")
    assert result.returncode == 0, result.stdout + result.stderr
