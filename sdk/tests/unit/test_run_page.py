from __future__ import annotations

import hashlib
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


def test_run_labels_are_derived_from_ids_unique_immutable_and_searchable(store):
    uuid_ids = [f"run-{number:07x}{'a' * 25}" for number in range(3)]
    for run_id in uuid_ids:
        store.save_test_run(run_id, {"run_id": run_id})
    for number in range(12):
        run_id = f"opaque-{number}"
        store.save_test_run(run_id, {"run_id": run_id})
    store.save_test_run(
        "opaque-0",
        {"run_id": "opaque-0", "status": "finished", "run_label": "Run #999"},
    )

    manifests = store.list_test_runs()
    labels = {item["run_id"]: item["run_label"] for item in manifests}
    assert len(labels) == len(set(labels.values())) == 15
    for run_id in uuid_ids:
        assert labels[run_id] == f"Run {run_id[4:11]}"
    opaque_label = "Run " + hashlib.sha256(b"opaque-0").hexdigest()[:7]
    assert labels["opaque-0"] == opaque_label
    assert store.get_test_run("opaque-0")["run_label"] == opaque_label
    for query, expected in (
        (labels[uuid_ids[1]].upper(), uuid_ids[1]),
        (uuid_ids[2][4:11], uuid_ids[2]),
        ("OPAQUE-11", "opaque-11"),
    ):
        page, total = store.list_test_run_page(limit=2, q=query)
        assert total == 1
        assert [item["run_id"] for item in page] == [expected]


def test_run_label_is_extended_when_the_short_prefix_is_taken(store):
    first = "run-abcdef0" + "1" * 25
    second = "run-abcdef0" + "2" * 25
    store.save_test_run(first, {"run_id": first})
    store.save_test_run(second, {"run_id": second})
    assert store.get_test_run(first)["run_label"] == "Run abcdef0"
    assert store.get_test_run(second)["run_label"] == "Run abcdef02"
    store.save_test_run(second, {"run_id": second, "status": "finished"})
    assert store.get_test_run(second)["run_label"] == "Run abcdef02"


def test_run_search_casefolds_unicode_ids(store):
    store.save_test_run("Café", {"run_id": "Café"})
    kelvin_id = "\N{KELVIN SIGN}ernel"
    store.save_test_run(kelvin_id, {"run_id": kelvin_id})
    for query, expected in (("CAFÉ", "Café"), ("kernel", kelvin_id)):
        page, total = store.list_test_run_page(limit=1, q=query)
        assert total == 1
        assert [item["run_id"] for item in page] == [expected]


def test_sqlite_backfills_null_labels_and_keeps_legacy_numbered_labels(tmp_path):
    path = Path(tmp_path) / "legacy.sqlite"
    unlabeled = "run-" + "0123456789abcdef" * 2
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE v2_test_runs (run_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, project_id TEXT, "
            "run_label TEXT)"
        )
        for run_id, created_at, label in (
            (unlabeled, "2026-09-19T00:00:00Z", None),
            ("older", "2026-09-18T00:00:00Z", "Run #3"),
        ):
            connection.execute(
                "INSERT INTO v2_test_runs VALUES (?, ?, ?, ?, NULL, ?)",
                (run_id, json.dumps({"run_id": run_id}), created_at, created_at, label),
            )
    store = SQLiteExecutionStore(path)
    assert store.get_test_run(unlabeled)["run_label"] == "Run 0123456"
    assert store.get_test_run("older")["run_label"] == "Run #3"
    store.close()
    reopened = SQLiteExecutionStore(path)
    reopened.save_test_run("older", {"run_id": "older", "updated": True})
    assert reopened.get_test_run("older")["run_label"] == "Run #3"
    reopened.close()


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
        first = labels[0]
        target = connection.execute(
            "SELECT run_id FROM v2_test_runs WHERE run_label != ? LIMIT 1", (first,)
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE v2_test_runs SET run_label=? WHERE run_id=?", (first, target)
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
