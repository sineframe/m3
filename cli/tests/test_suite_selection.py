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
    assert "2 passed" in result.stdout, result.stdout + result.stderr
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


def test_cli_ci_excludes_inherited_false_and_allows_closest_true(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_ci_selection.py"
    test_file.write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.m3(suite_name='ci', ci=False)\n"
        "def test_module_excluded(): assert False\n"
        "@pytest.mark.m3(ci=True)\n"
        "def test_override_included(): pass\n"
        "@pytest.mark.m3(ci=False)\n"
        "def test_function_excluded(): assert False\n"
        "@pytest.mark.m3(ci=True)\n"
        "@pytest.mark.parametrize('value', [pytest.param(1, marks=pytest.mark.m3(ci=False)), 2])\n"
        "def test_parameter_override(value): pass\n"
    )
    db = tmp_path / "ci.sqlite"
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
            "ci",
            "test",
            "--python",
            sys.executable,
            "--results-db",
            str(db),
            "--",
            "-q",
            str(test_file),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout, result.stdout + result.stderr
    assert "M3 CI excluded 3 test(s)" in result.stdout
    connection = sqlite3.connect(db)
    run_id, serialized_record = connection.execute(
        "select run_id, record_json from v2_test_runs"
    ).fetchone()
    assert run_id.startswith("run-")
    assert f"M3 run {run_id}" in result.stdout
    import json

    assert json.loads(serialized_record)["ci_excluded_count"] == 3


def test_cli_ci_excludes_agent_test_before_harness_validation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_ci_agent.py"
    test_file.write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.m3(suite_name='ci')\n"
        "def test_plain_pytest(): pass\n"
        "@pytest.mark.m3(ci=False)\n"
        "def test_agent(agent): pass\n"
    )
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
            "ci",
            "test",
            "--python",
            sys.executable,
            "--",
            "-q",
            str(test_file),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "agent test requires --harness" not in result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "M3 CI excluded 1 test(s)" in result.stdout


def test_cli_ci_excludes_parameter_marked_agent_without_harness(tmp_path: Path) -> None:
    test_file = tmp_path / "test_ci_agent_parameter.py"
    test_file.write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.m3(suite_name='ci')\n"
        "def test_plain_pytest(): pass\n"
        "@pytest.mark.m3(ci=True)\n"
        "@pytest.mark.parametrize('case', [pytest.param(1, marks=pytest.mark.m3(ci=False))])\n"
        "def test_agent(agent, case): assert False\n"
    )
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
            "ci",
            "test",
            "--python",
            sys.executable,
            "--",
            "-q",
            str(test_file),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "upload inspection unavailable" not in result.stderr
    assert "1 passed" in result.stdout
    assert "M3 CI excluded 1 test(s)" in result.stdout


def test_cli_ci_kept_agent_still_requires_harness(tmp_path: Path) -> None:
    test_file = tmp_path / "test_ci_agent_parameter.py"
    test_file.write_text(
        "import pytest\n"
        "@pytest.mark.m3(suite_name='ci', ci=True)\n"
        "@pytest.mark.parametrize('case', [pytest.param(1, marks=pytest.mark.m3(ci=False)), 2])\n"
        "def test_agent(agent, case): pass\n"
    )
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
            "ci",
            "test",
            "--python",
            sys.executable,
            "--",
            "-q",
            str(test_file),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 4
    assert "agent test requires --harness" in result.stdout + result.stderr


def test_cli_ci_parameter_true_overrides_inherited_false_for_agent(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_ci_agent_parameter_override.py"
    test_file.write_text(
        "import pytest, sys\n"
        "pytestmark = pytest.mark.m3(suite_name='ci', ci=False, agents=[{'harness':'acp','models':['marker'], 'manifest':{'command':sys.executable,'args':['fixture-agent'],'protocol':'acp','protocol_version':1}}], servers=[{'type':'stdio','command':'echo'}])\n"
        "@pytest.mark.parametrize('case', [pytest.param(1, marks=pytest.mark.m3(ci=True)), 2])\n"
        "def test_agent(agent, server, case): assert agent.model == 'marker' and server.command == 'echo'\n"
    )
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
            "ci",
            "test",
            "--python",
            sys.executable,
            "--",
            "-q",
            str(test_file),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "M3 CI excluded 1 test(s)" in result.stdout


def test_cli_ci_excludes_inherited_false_agent_without_validating_matrix(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_ci_agent_malformed_excluded.py"
    test_file.write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.m3(ci=False, agents='malformed', servers='malformed')\n"
        "def test_agent_and_server(agent, server): assert False\n"
    )
    plain_test_file = tmp_path / "test_plain.py"
    plain_test_file.write_text(
        "import pytest\npytestmark = pytest.mark.m3(suite_name='ci')\n"
        "def test_plain_pytest(): pass\n"
    )
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
            "ci",
            "test",
            "--python",
            sys.executable,
            "--",
            "-q",
            str(test_file),
            str(plain_test_file),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "M3 CI excluded 1 test(s)" in result.stdout


def test_cli_ci_parameter_true_overrides_inherited_false_for_server(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_ci_server_parameter_override.py"
    test_file.write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.m3(suite_name='ci', ci=False, servers=[{'type':'stdio','command':'echo'}])\n"
        "@pytest.mark.parametrize('case', [pytest.param(1, marks=pytest.mark.m3(ci=True)), 2])\n"
        "def test_server(server, case): assert server.command == 'echo'\n"
    )
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
            "ci",
            "test",
            "--python",
            sys.executable,
            "--",
            "-q",
            str(test_file),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "M3 CI excluded 1 test(s)" in result.stdout


def test_cli_ci_requires_boolean_marker_values(tmp_path: Path) -> None:
    test_file = tmp_path / "test_ci_invalid_marker.py"
    test_file.write_text(
        "import pytest\n@pytest.mark.m3(ci='false')\ndef test_invalid_marker(): pass\n"
    )
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
            "ci",
            "test",
            "--python",
            sys.executable,
            "--",
            "-q",
            str(test_file),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "m3(ci=...) must be a Boolean" in result.stdout + result.stderr


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
