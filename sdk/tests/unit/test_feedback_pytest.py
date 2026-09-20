from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
