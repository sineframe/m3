from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_cli_suite_selects_only_marked_files(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.py"
    other = tmp_path / "other.py"
    catalog.write_text(
        "import pytest\npytestmark=pytest.mark.m3(suite_name='catalog')\ndef test_a(): pass\ndef test_b(): pass\n"
    )
    other.write_text(
        "import pytest\npytestmark=pytest.mark.m3(suite_name='other')\ndef test_c(): pass\n"
    )
    db = tmp_path / "results.sqlite"
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).parents[2] / "src")
        + os.pathsep
        + str(Path(__file__).parents[2].parent / "sdk/src"),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "m3_cli",
            "test",
            "--suite=catalog",
            "--results-db",
            str(db),
            "--",
            "-q",
            str(catalog),
            str(other),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
    connection = sqlite3.connect(db)
    assert connection.execute("select count(*) from v2_test_results").fetchone()[0] == 2


def test_cli_blank_suite_is_usage_error(tmp_path: Path) -> None:
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).parents[2] / "src")
        + os.pathsep
        + str(Path(__file__).parents[2].parent / "sdk/src"),
    )
    result = subprocess.run(
        [sys.executable, "-m", "m3_cli", "test", "--suite=", "--", "-q"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2


def test_cli_suite_intersects_path_k_and_marker_selectors(tmp_path: Path) -> None:
    first = tmp_path / "catalog_one.py"
    second = tmp_path / "catalog_two.py"
    first.write_text(
        "import pytest\npytestmark=pytest.mark.m3(suite_name='catalog')\n@pytest.mark.fast\ndef test_keep(): pass\ndef test_drop(): pass\n"
    )
    second.write_text(
        "import pytest\npytestmark=pytest.mark.m3(suite_name='catalog')\n@pytest.mark.fast\ndef test_other_file(): pass\n"
    )
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).parents[2] / "src")
        + os.pathsep
        + str(Path(__file__).parents[2].parent / "sdk/src"),
    )
    db = tmp_path / "intersection.sqlite"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "m3_cli",
            "test",
            "--suite",
            "catalog",
            "--results-db",
            str(db),
            "--",
            "-q",
            "-m",
            "fast",
            "-k",
            "keep",
            str(first),
            str(second),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_cli_suite_matrix_with_marker_manifests(tmp_path: Path) -> None:
    files = []
    for name, suite in (
        ("catalog_one", "catalog"),
        ("catalog_two", "catalog"),
        ("other", "other"),
    ):
        path = tmp_path / f"{name}.py"
        agent_source = (
            "@pytest.mark.m3(agents=[{'harness':'acp','models':['marker'], 'manifest':{'command':sys.executable,'args':['fixture-agent'],'protocol':'acp','protocol_version':1}}, {'harness':'opencode','models':['marker']}])\ndef test_agent(agent): assert agent.model\n"
            if suite != "other"
            else ""
        )
        marker_source = f"pytestmark=pytest.mark.m3(suite_name='{suite}')\n"
        path.write_text(
            "import os, sys, pytest\n"
            + marker_source
            + "from m3.storage import SQLiteExecutionStore\nfrom m3.types import ExecutionId, ExecutionState\n"
            f"def test_direct():\n s=SQLiteExecutionStore(os.environ['SUITE_DB']); s.create(ExecutionState(execution_id=ExecutionId('{name}'), suite_name='{suite}')); s.close()\n"
            + agent_source
        )
        files.append(path)

    def run_case(label: str, *options: str, expected: int, code: int = 0) -> None:
        db = tmp_path / f"{label}.sqlite"
        env = dict(
            os.environ,
            PYTHONPATH=str(Path(__file__).parents[2] / "src")
            + os.pathsep
            + str(Path(__file__).parents[2].parent / "sdk/src"),
            SUITE_DB=str(db),
        )
        command = [
            sys.executable,
            "-m",
            "m3_cli",
            "test",
            "--results-db",
            str(db),
            *options,
            "--",
            "-q",
            *(str(path) for path in files),
        ]
        result = subprocess.run(
            command, cwd=tmp_path, env=env, text=True, capture_output=True
        )
        assert result.returncode == code, result.stdout + result.stderr
        if code == 0:
            assert f"{expected} passed" in result.stdout
        connection = sqlite3.connect(db)
        assert (
            connection.execute("select count(*) from v2_test_results").fetchone()[0]
            == expected
        )
        assert connection.execute("select count(*) from v2_executions").fetchone()[
            0
        ] == (
            3
            if expected == 7
            else 2
            if expected in {6, 4, 10}
            else 1
            if expected == 1
            else 0
        )
        if expected >= 4:
            rows = connection.execute(
                "select suite_id,record_json from v2_test_results where json_extract(record_json,'$.suite_name')='catalog'"
            ).fetchall()
            assert len({row[0] for row in rows}) == 1

    run_case("all", expected=7)
    run_case("catalog", "--suite", "catalog", expected=6)
    run_case("acp", "--suite=catalog", "--harness", "acp=a", expected=4)
    run_case(
        "both",
        "--suite=catalog",
        "--harness",
        "acp=a",
        "--harness",
        "opencode=o",
        expected=6,
    )
    run_case(
        "trials",
        "--suite=catalog",
        "--harness",
        "acp=a,b",
        "--trials",
        "2",
        expected=10,
    )
    run_case("other", "--suite=other", "--harness", "acp=a", expected=1)
    run_case("missing", "--suite=missing", expected=0, code=5)
