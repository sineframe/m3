from __future__ import annotations

import json
import sqlite3
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
