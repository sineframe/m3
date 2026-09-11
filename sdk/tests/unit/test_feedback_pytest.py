from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from mcp_pal.storage import SQLiteExecutionStore


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
            "mcp_pal.pytest_plugin",
            "--mcp-pal-results-db",
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
    assert "\nMCP Pal run " in result.stdout
    store, run_id, record = _manifest(database)
    try:
        attempts = store.list_test_results(run_id)
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "passed"
        assert attempts[0]["execution_ids"] == []
        assert store.get_test_run(run_id)["status"] == "finished"
        report = tmp_path / ".mcp-pal" / "reports" / run_id / "feedback.json"
        assert json.loads(report.read_text(encoding="utf-8"))["run_id"] == run_id
        assert record["capture"]["mode"] == "fd"
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
    second, _ = _run(
        tmp_path, "def test_one():\n    pass\n", "--mcp-pal-baseline", run_id
    )
    assert second.returncode == 0
