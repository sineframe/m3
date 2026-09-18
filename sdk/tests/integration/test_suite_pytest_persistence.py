from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _run(
    tmp_path: Path, *files: Path, xdist: bool = False, suite: str | None = None
) -> subprocess.CompletedProcess[str]:
    db = tmp_path / ("xdist.sqlite" if xdist else "results.sqlite")
    args = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "mcp_pal.pytest_plugin",
        "--mcp-pal-results-db",
        str(db),
        "-q",
    ]
    if xdist:
        args += ["-n", "2"]
    if suite is not None:
        args += [f"--mcp-pal-suite={suite}"]
    args += [str(path) for path in files]
    env = {
        "PYTHONPATH": str(Path(__file__).parents[2].resolve() / "src"),
        "SUITE_DB": str(db),
    }
    return subprocess.run(args, cwd=tmp_path, env=env, text=True, capture_output=True)


def test_two_files_share_catalog_suite_and_persist_setup_failure(
    tmp_path: Path,
) -> None:
    first = tmp_path / "catalog_one.py"
    second = tmp_path / "catalog_two.py"
    other = tmp_path / "other.py"
    first.write_text(
        "import pytest\nfrom mcp_pal.storage import SQLiteExecutionStore\nfrom mcp_pal.types import ExecutionId, ExecutionState\npytestmark=pytest.mark.mcp_pal(suite_name='catalog')\n"
        "def test_one():\n s=SQLiteExecutionStore(__import__('os').environ['SUITE_DB']); s.create(ExecutionState(execution_id=ExecutionId('catalog-one'), suite_name='catalog')); s.close()\n"
        "@pytest.fixture\ndef broken(): raise RuntimeError('setup')\n"
        "def test_setup_failure(broken): pass\n"
    )
    second.write_text(
        "import pytest\nfrom mcp_pal.storage import SQLiteExecutionStore\nfrom mcp_pal.types import ExecutionId, ExecutionState\npytestmark=pytest.mark.mcp_pal(suite_name='catalog')\ndef test_two():\n s=SQLiteExecutionStore(__import__('os').environ['SUITE_DB']); s.create(ExecutionState(execution_id=ExecutionId('catalog-two'), suite_name='catalog')); s.close()\n"
    )
    other.write_text(
        "import pytest\nfrom mcp_pal.storage import SQLiteExecutionStore\nfrom mcp_pal.types import ExecutionId, ExecutionState\npytestmark=pytest.mark.mcp_pal(suite_name='other')\ndef test_other():\n s=SQLiteExecutionStore(__import__('os').environ['SUITE_DB']); s.create(ExecutionState(execution_id=ExecutionId('other-one'), suite_name='other')); s.close()\n"
    )
    result = _run(tmp_path, first, second, other)
    assert result.returncode != 0
    db = sqlite3.connect(tmp_path / "results.sqlite")
    suites = db.execute("select id,suite_name from v2_suites order by id").fetchall()
    executions = db.execute(
        "select suite_id,snapshot_json from v2_executions"
    ).fetchall()
    attempts = db.execute("select suite_id,record_json from v2_test_results").fetchall()
    catalog_id = next(row[0] for row in suites if row[1] == "catalog")
    assert all(
        row[0] == catalog_id
        for row in attempts
        if json.loads(row[1]).get("suite_name") == "catalog"
    )
    assert any(
        json.loads(row[1]).get("outcome") == "error"
        and json.loads(row[1]).get("suite_name") == "catalog"
        for row in attempts
    )
    assert len(attempts) == 4
    assert sum(json.loads(row[1]).get("outcome") == "passed" for row in attempts) == 3
    assert all(json.loads(row[1]).get("suite_id") == row[0] for row in attempts)
    catalog_execs = [
        row for row in executions if json.loads(row[1]).get("suite_name") == "catalog"
    ]
    assert len(catalog_execs) == 2
    assert {row[0] for row in catalog_execs} == {catalog_id}
    assert {json.loads(row[1]).get("suite_id") for row in catalog_execs} == {catalog_id}
    assert any(row[1] == "other" for row in suites)


def test_pytest_docstrings_are_cleaned_into_attempt_records(tmp_path: Path) -> None:
    test_file = tmp_path / "descriptions.py"
    test_file.write_text(
        "import pytest\n"
        "pytestmark=pytest.mark.mcp_pal(suite_name='docs')\n"
        "def test_multiline():\n"
        "    '''\n    A useful summary.\n\n    With detail.\n    '''\n"
        "    pass\n"
        "@pytest.fixture\n"
        "def broken(): raise RuntimeError('setup')\n"
        "def test_setup_failure(broken):\n"
        "    '''Setup still has a description.'''\n"
        "    pass\n"
        "@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_parameterized(value):\n"
        "    '''Each parameter gets this description.'''\n"
        "    assert value > 0\n"
        "def test_without_docstring(): pass\n"
    )
    result = _run(tmp_path, test_file)
    assert result.returncode != 0
    db = sqlite3.connect(tmp_path / "results.sqlite")
    rows = [
        json.loads(value)
        for (value,) in db.execute("select record_json from v2_test_results")
    ]
    by_node = {row["node_id"].split("::")[-1]: row for row in rows}
    assert (
        by_node["test_multiline"]["description"] == "A useful summary.\n\nWith detail."
    )
    assert (
        by_node["test_setup_failure"]["description"] == "Setup still has a description."
    )
    assert by_node["test_setup_failure"]["outcome"] == "error"
    parameterized = [row for row in rows if "::test_parameterized[" in row["node_id"]]
    assert len(parameterized) == 2
    assert {row["description"] for row in parameterized} == {
        "Each parameter gets this description."
    }
    assert by_node["test_without_docstring"]["description"] == ""


def test_old_schema_rows_survive_suite_migration(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "create table v2_executions(id text primary key,snapshot_json text not null,specification_json text,provenance_json text,parent_execution_id text,created_at text,deleted_at text,run_id text);"
        "create table v2_test_runs(run_id text primary key,record_json text,created_at text,updated_at text);"
        "create table v2_test_results(run_id text,attempt_id text,record_json text,created_at text,updated_at text,primary key(run_id,attempt_id));"
        "insert into v2_executions values ('old','{}',null,null,null,'now',null,null);"
        "insert into v2_test_runs values ('run','{}','now','now');"
        "insert into v2_test_results values ('run','attempt','{}','now','now');"
    )
    db.commit()
    db.close()
    from mcp_pal.storage import SQLiteExecutionStore

    store = SQLiteExecutionStore(path)
    with store._connect() as connection:
        assert (
            connection.execute(
                "select suite_id from v2_executions where id='old'"
            ).fetchone()[0]
            is None
        )
        assert (
            connection.execute(
                "select suite_id from v2_test_results where attempt_id='attempt'"
            ).fetchone()[0]
            is None
        )
    store.close()


def test_xdist_suite_rows_when_available(tmp_path: Path) -> None:
    pytest.importorskip("xdist")
    test_file = tmp_path / "many.py"
    test_file.write_text(
        "import pytest\npytestmark=pytest.mark.mcp_pal(suite_name='catalog')\n"
        + "\n".join(f"def test_{i}(): pass" for i in range(8))
    )
    result = _run(tmp_path, test_file, xdist=True)
    assert result.returncode == 0, result.stdout + result.stderr
    db = sqlite3.connect(tmp_path / "xdist.sqlite")
    rows = db.execute("select suite_id,record_json from v2_test_results").fetchall()
    assert len(rows) == 8 and len({row[0] for row in rows}) == 1


def test_suite_selection_normalizes_marker_for_plain_and_agent_tests(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "selected.py"
    selected.write_text(
        "import pytest\npytestmark=pytest.mark.mcp_pal(suite_name=' catalog ')\n"
        "def test_plain(): pass\n"
        "@pytest.mark.mcp_pal(agents=[{'harness':'opencode','models':['model']}])\n"
        "def test_agent(agent): assert agent.model == 'model'\n"
    )
    other = tmp_path / "other.py"
    other.write_text(
        "import pytest\npytestmark=pytest.mark.mcp_pal(suite_name='other')\n"
        "def test_other(): pass\n"
    )
    result = _run(tmp_path, selected, other, suite="catalog")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
    db = sqlite3.connect(tmp_path / "results.sqlite")
    attempts = db.execute("select suite_id,record_json from v2_test_results").fetchall()
    assert len(attempts) == 2
    assert {json.loads(row[1])["suite_name"] for row in attempts} == {"catalog"}
    assert len({row[0] for row in attempts}) == 1
