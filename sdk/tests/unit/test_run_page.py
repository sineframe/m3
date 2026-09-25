from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from m3.storage import InMemoryExecutionStore, SQLiteExecutionStore

# Saved in scrambled order. Text order differs from UTC order for r-ist.
RUNS = {
    "r-ist": "2026-09-18T15:00:00+05:30",  # 09:30Z
    "r-missing": None,
    "r-z2": "2026-09-18T10:30:00Z",
    "r-tie-a": "2026-09-17T00:00:00+00:00",
    "r-bad": "not-a-date",
    "r-utc": "2026-09-18T10:00:00+00:00",
    "r-z": "2026-09-18T10:30:00.000001Z",  # 1 microsecond after r-z2
    "r-tie-b": "2026-09-17T00:00:00Z",
}
NEWEST_FIRST = [
    "r-z",
    "r-z2",
    "r-utc",
    "r-ist",
    "r-tie-b",
    "r-tie-a",
    "r-missing",
    "r-bad",
]


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path):
    value = (
        SQLiteExecutionStore(Path(tmp_path) / "runs.sqlite")
        if request.param == "sqlite"
        else InMemoryExecutionStore()
    )
    yield value
    value.close()


def test_run_page_orders_by_utc_with_undated_runs_last(store):
    for run_id, created_at in RUNS.items():
        manifest = {"run_id": run_id}
        if created_at is not None:
            manifest["created_at"] = created_at
        store.save_test_run(run_id, manifest)

    runs, total = store.list_test_run_page()
    assert [run["run_id"] for run in runs] == NEWEST_FIRST
    assert total == len(RUNS)

    paged: list[str] = []
    for offset in range(0, len(RUNS), 3):
        page, page_total = store.list_test_run_page(limit=3, offset=offset)
        assert page_total == len(RUNS)
        paged.extend(run["run_id"] for run in page)
    assert paged == NEWEST_FIRST


def test_run_labels_are_unique_immutable_and_searchable_across_pages(store):
    for number in range(12):
        run_id = f"opaque-{number}"
        store.save_test_run(run_id, {"run_id": run_id})
    store.save_test_run(
        "opaque-0",
        {"run_id": "opaque-0", "status": "finished", "run_label": "Run #999"},
    )

    manifests = store.list_test_runs()
    labels = {item["run_id"]: item["run_label"] for item in manifests}
    assert len(labels) == len(set(labels.values())) == 12
    assert labels["opaque-0"] == "Run #1"
    assert store.get_test_run("opaque-0")["run_label"] == "Run #1"
    for query, expected in (
        ("RUN #12", "opaque-11"),
        ("RUN #1", "opaque-0"),
        ("OPAQUE-11", "opaque-11"),
    ):
        page, total = store.list_test_run_page(limit=2, q=query)
        assert total == 1
        assert [item["run_id"] for item in page] == [expected]


def test_sqlite_backfills_legacy_runs_in_history_order(tmp_path):
    path = Path(tmp_path) / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE v2_test_runs (run_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        for run_id, created_at in (
            ("newer", "2026-09-19T00:00:00Z"),
            ("older", "2026-09-18T00:00:00Z"),
        ):
            connection.execute(
                "INSERT INTO v2_test_runs VALUES (?, ?, ?, ?)",
                (run_id, json.dumps({"run_id": run_id}), created_at, created_at),
            )
    store = SQLiteExecutionStore(path)
    assert store.get_test_run("older")["run_label"] == "Run #1"
    assert store.get_test_run("newer")["run_label"] == "Run #2"
    store.close()
    reopened = SQLiteExecutionStore(path)
    reopened.save_test_run("third", {"run_id": "third"})
    assert reopened.get_test_run("older")["run_label"] == "Run #1"
    assert reopened.get_test_run("third")["run_label"] == "Run #3"
    reopened.close()


def test_sqlite_manifest_updates_and_test_results_do_not_consume_run_numbers(tmp_path):
    store = SQLiteExecutionStore(Path(tmp_path) / "updates.sqlite")
    store.save_test_run("first", {"run_id": "first"})
    for number in range(5):
        store.save_test_run("first", {"run_id": "first", "updated": number})
        store.save_test_result(
            "first", f"attempt-{number}", {"node_id": f"test_{number}"}
        )
    store.save_test_run("second", {"run_id": "second"})
    assert store.get_test_run("first")["run_label"] == "Run #1"
    assert store.get_test_run("second")["run_label"] == "Run #2"
    store.close()


def test_sqlite_allocates_run_labels_without_concurrent_collisions(tmp_path):
    path = Path(tmp_path) / "concurrent.sqlite"
    store = SQLiteExecutionStore(path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda n: store.save_test_run(f"run-{n}", {"run_id": f"run-{n}"}),
                range(24),
            )
        )
    labels = [run["run_label"] for run in store.list_test_runs()]
    assert len(set(labels)) == len(labels) == 24
    store.close()
    with sqlite3.connect(path) as connection:
        target = connection.execute(
            "SELECT run_id FROM v2_test_runs WHERE run_label != 'Run #1' LIMIT 1"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE v2_test_runs SET run_label='Run #1' WHERE run_id=?", (target,)
            )


def test_sqlite_unpaginated_run_page_exceeds_sql_variable_limit(tmp_path):
    path = Path(tmp_path) / "large.sqlite"
    store = SQLiteExecutionStore(path)
    count = 33_000  # above SQLite's default 32,766 bound variables
    connection = sqlite3.connect(path)
    with connection:
        connection.executemany(
            "INSERT INTO v2_test_runs(run_id,record_json,created_at,updated_at)"
            " VALUES(?,?,?,?)",
            (
                (f"run-{i:05d}", json.dumps({"run_id": f"run-{i:05d}"}), "", "")
                for i in range(count)
            ),
        )
    connection.close()
    store.save_test_result(
        "run-00000", "attempt-1", {"node_id": "test_one", "suite_name": "alpha"}
    )

    runs, total = store.list_test_run_page()
    assert total == count
    assert len(runs) == count
    suites = {run["run_id"]: run["suites"] for run in runs}
    assert [item["suite_name"] for item in suites["run-00000"]] == ["alpha"]
    store.close()
