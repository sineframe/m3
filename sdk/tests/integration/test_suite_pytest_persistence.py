from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _run(
    tmp_path: Path,
    *files: Path,
    xdist: bool = False,
    suite: str | None = None,
    ci: bool = False,
    run_id: str | None = None,
    ci_metadata: str | None = None,
    persist: bool = True,
) -> subprocess.CompletedProcess[str]:
    db = tmp_path / ("xdist.sqlite" if xdist else "results.sqlite")
    args = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "m3.pytest_plugin",
        "-q",
    ]
    if persist:
        args += ["--results-db", str(db)]
    if xdist:
        args += ["-n", "2"]
    if suite is not None:
        args += [f"--suite={suite}"]
    if ci:
        args.append("--m3-ci")
    if run_id is not None:
        args += ["--m3-run-id", run_id]
    if ci_metadata is not None:
        args += ["--m3-ci-metadata", ci_metadata]
    args += [str(path) for path in files]
    env = {
        "PYTHONPATH": str(Path(__file__).parents[2].resolve() / "src"),
        "SUITE_DB": str(db),
    }
    return subprocess.run(args, cwd=tmp_path, env=env, text=True, capture_output=True)


def test_ci_policy_and_metadata_are_saved_to_manifest_and_feedback(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_ci.py"
    test_file.write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.m3(suite_name='ci')\n"
        "def test_selected(): pass\n"
        "@pytest.mark.m3(ci=False)\n"
        "def test_ignored(): assert False\n",
        encoding="utf-8",
    )
    run_id = "run-cli-owned-integration"
    ci_metadata = json.dumps(
        {
            "provider": "github",
            "repository": "owner/repo",
            "commit": "abc123",
            "pr_number": "42",
            "attempt": "2",
        },
        separators=(",", ":"),
    )
    result = _run(
        tmp_path,
        test_file,
        xdist=True,
        ci=True,
        run_id=run_id,
        ci_metadata=ci_metadata,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "M3 CI excluded 1 test(s)" in result.stdout
    db = sqlite3.connect(tmp_path / "xdist.sqlite")
    record = json.loads(
        db.execute(
            "select record_json from v2_test_runs where run_id=?", (run_id,)
        ).fetchone()[0]
    )
    assert record["ci_excluded_count"] == 1
    assert record["ci"] == json.loads(ci_metadata)
    feedback_path = tmp_path / ".m3" / "reports" / run_id / "feedback.json"
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    assert feedback["ci"] == json.loads(ci_metadata)


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("", "test_missing"),
        ("import pytest\n@pytest.mark.m3(suite_name='   ')\n", "test_missing"),
    ],
)
def test_persisted_pytest_requires_suite_name(
    tmp_path: Path, marker: str, expected: str
) -> None:
    test_file = tmp_path / "test_missing_suite.py"
    test_file.write_text(f"{marker}def test_missing(): pass\n")
    result = _run(tmp_path, test_file)
    assert result.returncode != 0
    assert "Persisted pytest tests require a non-empty suite name" in result.stderr
    assert "pytest.mark.m3(suite_name=" in result.stderr
    assert expected in result.stderr
    with sqlite3.connect(tmp_path / "results.sqlite") as db:
        assert db.execute("select count(*) from v2_test_results").fetchone()[0] == 0


def test_unpersisted_pytest_does_not_require_suite_name(tmp_path: Path) -> None:
    test_file = tmp_path / "test_unpersisted.py"
    test_file.write_text("def test_plain(): pass\n")
    result = _run(tmp_path, test_file, persist=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_not_run_attempt_keeps_collected_suite(tmp_path: Path) -> None:
    test_file = tmp_path / "test_stopped.py"
    test_file.write_text(
        "import pytest\npytestmark=pytest.mark.m3(suite_name='stopped')\n"
        "def test_first(): pytest.exit('stopped')\n"
        "def test_second(): pass\n"
    )
    result = _run(tmp_path, test_file)
    assert result.returncode != 0
    with sqlite3.connect(tmp_path / "results.sqlite") as db:
        attempts = db.execute(
            "select suite_id,record_json from v2_test_results"
        ).fetchall()
    assert any(
        row["outcome"] == "not_run" and row["suite_name"] == "stopped" and suite_id
        for suite_id, value in attempts
        if (row := json.loads(value))
    )


def test_two_files_share_catalog_suite_and_persist_setup_failure(
    tmp_path: Path,
) -> None:
    first = tmp_path / "catalog_one.py"
    second = tmp_path / "catalog_two.py"
    other = tmp_path / "other.py"
    first.write_text(
        "import pytest\nfrom m3.storage import SQLiteExecutionStore\nfrom m3.types import ExecutionId, ExecutionState\npytestmark=pytest.mark.m3(suite_name='catalog')\n"
        "def test_one():\n s=SQLiteExecutionStore(__import__('os').environ['SUITE_DB']); s.create(ExecutionState(execution_id=ExecutionId('catalog-one'), suite_name='catalog')); s.close()\n"
        "@pytest.fixture\ndef broken(): raise RuntimeError('setup')\n"
        "def test_setup_failure(broken): pass\n"
    )
    second.write_text(
        "import pytest\nfrom m3.storage import SQLiteExecutionStore\nfrom m3.types import ExecutionId, ExecutionState\npytestmark=pytest.mark.m3(suite_name='catalog')\ndef test_two():\n s=SQLiteExecutionStore(__import__('os').environ['SUITE_DB']); s.create(ExecutionState(execution_id=ExecutionId('catalog-two'), suite_name='catalog')); s.close()\n"
    )
    other.write_text(
        "import pytest\nfrom m3.storage import SQLiteExecutionStore\nfrom m3.types import ExecutionId, ExecutionState\npytestmark=pytest.mark.m3(suite_name='other')\ndef test_other():\n s=SQLiteExecutionStore(__import__('os').environ['SUITE_DB']); s.create(ExecutionState(execution_id=ExecutionId('other-one'), suite_name='other')); s.close()\n"
    )
    result = _run(tmp_path, first, second, other)
    assert result.returncode != 0
    db = sqlite3.connect(tmp_path / "results.sqlite")
    persisted_runs = db.execute("select run_id, run_label from v2_test_runs").fetchall()
    assert len(persisted_runs) == 1
    assert persisted_runs[0][1] == "Run #1"
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
        "pytestmark=pytest.mark.m3(suite_name='docs')\n"
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


def test_manual_kit_inherits_project_identity_from_pytest_manifest(
    tmp_path: Path,
) -> None:
    project_id = "44444444-4444-4444-8444-444444444444"
    (tmp_path / "m3.toml").write_text(
        f' schema_version = 1\nproject_id = "{project_id}"\nproject_name = "Manual Kit"\n',
        encoding="utf-8",
    )
    test_file = tmp_path / "manual.py"
    test_file.write_text(
        "import sys\n"
        "import pytest\n"
        "pytestmark = pytest.mark.m3(suite_name='manual')\n"
        "from m3 import MCPTestKit\n"
        "from m3.types import CallTool, DirectSpec, ServerBinding, StdioServer\n"
        "def test_manual_kit():\n"
        "    spec = DirectSpec(servers=(ServerBinding(server=StdioServer(name='echo', command=sys.executable, args=('-m', 'm3.fixtures.echo_server'))),), operation=CallTool(server='echo', name='echo', arguments={'text': 'ok'}))\n"
        "    with MCPTestKit() as kit:\n"
        "        result = kit.run(spec)\n"
        "    assert result.snapshot.project_id.root == '" + project_id + "'\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, test_file)
    assert result.returncode == 0, result.stdout + result.stderr
    db = sqlite3.connect(tmp_path / "results.sqlite")
    run_id = db.execute("select run_id from v2_test_runs").fetchone()[0]
    row = db.execute("select id,run_id,project_id from v2_executions").fetchone()
    assert row == (row[0], run_id, project_id)
    assert (
        db.execute(
            "select count(*) from v2_executions where project_id=?", (project_id,)
        ).fetchone()[0]
        == 1
    )


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
    from m3.storage import SQLiteExecutionStore

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
                "select s.suite_name from v2_test_results r "
                "join v2_suites s on s.id=r.suite_id where r.attempt_id='attempt'"
            ).fetchone()[0]
            == "Legacy unassigned"
        )
        assert connection.execute("pragma foreign_key_check").fetchall() == []
        assert (
            next(
                row[3]
                for row in connection.execute("pragma table_info(v2_test_results)")
                if row[1] == "suite_id"
            )
            == 1
        )
    store.close()


def test_test_result_suite_id_is_required_and_legacy_names_are_backfilled(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old-results.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "create table v2_test_runs(run_id text primary key,record_json text,created_at text,updated_at text);"
        "create table v2_test_results(run_id text,attempt_id text,record_json text,created_at text,updated_at text,suite_id integer,primary key(run_id,attempt_id));"
        "insert into v2_test_runs values ('run','{}','now','now');"
        """insert into v2_test_results values ('run','old','{"suite_name":"catalog"}','now','now',null);"""
    )
    db.close()
    from m3.storage import SQLiteExecutionStore

    store = SQLiteExecutionStore(path)
    with store._connect() as connection:
        assert (
            connection.execute(
                "select s.suite_name from v2_test_results r "
                "join v2_suites s on s.id=r.suite_id where r.attempt_id='old'"
            ).fetchone()[0]
            == "catalog"
        )
        assert connection.execute("pragma foreign_key_check").fetchall() == []
    store.close()
    reopened = SQLiteExecutionStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("pragma foreign_keys=on")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "insert into v2_test_results(run_id,attempt_id,record_json,created_at,updated_at,suite_id) "
                "values ('run','null','{}','now','now',null)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "insert into v2_test_results(run_id,attempt_id,record_json,created_at,updated_at,suite_id) "
                "values ('run','orphan','{}','now','now',99999)"
            )
    with pytest.raises((TypeError, ValueError), match="suite_name"):
        reopened.save_test_result("run", "missing", {"node_id": "test_missing"})
    assert reopened.list_test_results("run") == ({"suite_name": "catalog"},)
    reopened.close()


def test_legacy_unique_suite_table_rebuild_preserves_foreign_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-unique.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "PRAGMA foreign_keys=ON;"
        "create table v2_suites(id integer primary key autoincrement, suite_name text not null unique);"
        "create table v2_executions(id text primary key,snapshot_json text not null,specification_json text,provenance_json text,parent_execution_id text,created_at text,deleted_at text,run_id text,suite_id integer references v2_suites(id));"
        "create table v2_test_runs(run_id text primary key,record_json text,created_at text,updated_at text);"
        "create table v2_test_results(run_id text,attempt_id text,record_json text,created_at text,updated_at text,suite_id integer references v2_suites(id),primary key(run_id,attempt_id));"
        "insert into v2_suites(suite_name) values ('catalog');"
        "insert into v2_executions values ('old','{}',null,null,null,'now',null,'run',1);"
        "insert into v2_test_runs values ('run','{}','now','now');"
        "insert into v2_test_results values ('run','attempt','{}','now','now',1);"
    )
    db.close()
    from m3.storage import SQLiteExecutionStore

    store = SQLiteExecutionStore(path)
    store.ensure_project("11111111-1111-4111-8111-111111111111", "one")
    store.ensure_project("22222222-2222-4222-8222-222222222222", "two")
    first = store.ensure_suite("catalog", "11111111-1111-4111-8111-111111111111")
    second = store.ensure_suite("catalog", "22222222-2222-4222-8222-222222222222")
    assert first.id != second.id
    with store._connect() as connection:
        assert (
            connection.execute(
                "select suite_id from v2_executions where id='old'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("pragma foreign_key_check").fetchall() == []
    store.close()


def test_xdist_suite_rows_when_available(tmp_path: Path) -> None:
    pytest.importorskip("xdist")
    test_file = tmp_path / "many.py"
    test_file.write_text(
        "import pytest\npytestmark=pytest.mark.m3(suite_name='catalog')\n"
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
        "import pytest\npytestmark=pytest.mark.m3(suite_name=' catalog ')\n"
        "def test_plain(): pass\n"
        "@pytest.mark.m3(agents=[{'harness':'opencode','models':['model']}])\n"
        "def test_agent(agent): assert agent.model == 'model'\n"
    )
    other = tmp_path / "other.py"
    other.write_text(
        "import pytest\npytestmark=pytest.mark.m3(suite_name='other')\n"
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
