"""Suites ordered by their latest run, one-run lookup, and the attention filter."""

from __future__ import annotations

from pathlib import Path

import pytest
from _local_client import TestClient

from m3.storage import InMemoryExecutionStore, SQLiteExecutionStore
from m3_app.api.app import create_app
from m3_app.settings import Settings


def _store(tmp_path: Path, in_memory: bool):
    database = tmp_path / "suite-runs.sqlite"
    store = InMemoryExecutionStore() if in_memory else SQLiteExecutionStore(database)
    return store, database


def _save_run(store, run_id, created_at, suites=(), **manifest):
    store.save_test_run(
        run_id, {"run_id": run_id, "created_at": created_at, **manifest}
    )
    for index, suite_name in enumerate(suites):
        store.save_test_result(
            run_id,
            f"{run_id}-{index}",
            {"node_id": f"test_{index}", "suite_name": suite_name},
        )


@pytest.mark.parametrize("in_memory", [False, True])
def test_suites_are_listed_by_latest_run_with_counts(tmp_path, in_memory):
    store, database = _store(tmp_path, in_memory)
    store.ensure_suite("never-run")
    _save_run(store, "a-1", "2026-09-18T10:00:00+00:00", ("alpha",), status="finished")
    _save_run(store, "b-1", "2026-09-19T10:00:00+00:00", ("beta",))
    _save_run(
        store,
        "a-2",
        "2026-09-20T10:00:00+00:00",
        ("alpha", "gamma"),
        status="failed",
        effective_verdict_counts={"failed": 1, "passed": 2},
    )
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(application) as client:
        body = client.get("/api/v2/suites").json()
        names = [suite["suite_name"] for suite in body["suites"]]
        # alpha and gamma share the newest run; ties fall back to the name.
        assert names == ["alpha", "gamma", "beta", "never-run"]
        assert (body["total"], body["limit"], body["offset"]) == (4, None, 0)
        alpha = body["suites"][0]
        assert alpha["run_count"] == 2
        assert alpha["last_run"]["run_id"] == "a-2"
        assert alpha["last_run"]["status"] == "failed"
        assert alpha["last_run"]["effective_verdict_counts"] == {
            "failed": 1,
            "passed": 2,
        }
        assert [s["suite_name"] for s in alpha["last_run"]["suites"]] == [
            "alpha",
            "gamma",
        ]
        never = body["suites"][3]
        assert (never["run_count"], never["last_run"]) == (0, None)

        # A newer run moves its suite to the top.
        _save_run(store, "b-2", "2026-09-21T10:00:00+00:00", ("beta",))
        names = [s["suite_name"] for s in client.get("/api/v2/suites").json()["suites"]]
        assert names[0] == "beta"

        page = client.get("/api/v2/suites", params={"limit": 2, "offset": 1}).json()
        assert [s["suite_name"] for s in page["suites"]] == ["alpha", "gamma"]
        assert (page["total"], page["limit"], page["offset"]) == (4, 2, 1)

        found = client.get("/api/v2/suites", params={"q": "AMM"}).json()
        assert [s["suite_name"] for s in found["suites"]] == ["gamma"]
        assert found["total"] == 1

        one = client.get(f"/api/v2/suites/{alpha['suite_id']}")
        assert one.status_code == 200
        assert one.json()["suite"]["run_count"] == 2
        assert one.json()["suite"]["last_run"]["run_id"] == "a-2"
        missing = client.get("/api/v2/suites/999999")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "suite_not_found"
        assert client.get("/api/v2/suites", params={"limit": 101}).status_code == 422
    store.close()


@pytest.mark.parametrize("in_memory", [False, True])
def test_one_run_resolves_without_search_or_paging(tmp_path, in_memory):
    store, database = _store(tmp_path, in_memory)
    _save_run(store, "run id/part", "2026-09-01T10:00:00+00:00", ("beta", "alpha"))
    _save_run(store, "no-suite", "2026-09-02T10:00:00+00:00")
    # Push the first run far past the first list page.
    for index in range(120):
        _save_run(store, f"newer-{index:03d}", f"2026-09-10T10:{index % 60:02d}:00Z")
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(application) as client:
        first_page = client.get("/api/v2/runs", params={"limit": 100}).json()
        assert "run id/part" not in {run["run_id"] for run in first_page["runs"]}

        response = client.get("/api/v2/runs/run%20id%2Fpart")
        assert response.status_code == 200
        run = response.json()["run"]
        assert run["run_id"] == "run id/part"
        assert [s["suite_name"] for s in run["suites"]] == ["alpha", "beta"]

        bare = client.get("/api/v2/runs/no-suite").json()["run"]
        assert bare["suites"] == []

        missing = client.get("/api/v2/runs/unknown-run")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "run_not_found"
    store.close()


@pytest.mark.parametrize("in_memory", [False, True])
def test_attention_filter_pages_the_filtered_runs(tmp_path, in_memory):
    store, database = _store(tmp_path, in_memory)
    # Sixty passing runs, then failures further back than one page.
    for index in range(60):
        _save_run(
            store,
            f"pass-{index:02d}",
            f"2026-09-20T10:{index:02d}:00Z",
            ("alpha",),
            status="finished",
            effective_verdict_counts={"passed": 1},
        )
    _save_run(
        store,
        "failed-case",
        "2026-09-01T10:00:00Z",
        ("alpha",),
        status="finished",
        effective_verdict_counts={"failed": 1},
    )
    _save_run(
        store, "interrupted", "2026-09-02T10:00:00Z", ("alpha",), status="Interrupted"
    )
    _save_run(
        store,
        "legacy-error",
        "2026-09-03T10:00:00Z",
        ("alpha",),
        status="finished",
        test_outcome_counts={"error": 2},
    )
    _save_run(
        store,
        "other-suite-failure",
        "2026-09-04T10:00:00Z",
        ("beta",),
        status="failed",
    )
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(application) as client:
        alpha = client.get("/api/v2/suites", params={"q": "alpha"}).json()["suites"][0]
        params = {"suite_id": alpha["suite_id"], "limit": 50}

        everything = client.get("/api/v2/runs", params=params).json()
        assert (everything["total"], everything["attention_total"]) == (63, 3)

        first = client.get("/api/v2/runs", params={**params, "attention": True}).json()
        assert [run["run_id"] for run in first["runs"]] == [
            "legacy-error",
            "interrupted",
            "failed-case",
        ]
        assert (first["total"], first["attention_total"]) == (3, 3)

        second = client.get(
            "/api/v2/runs",
            params={**params, "attention": True, "limit": 2, "offset": 2},
        ).json()
        assert [run["run_id"] for run in second["runs"]] == ["failed-case"]
        assert second["total"] == 3

        searched = client.get("/api/v2/runs", params={**params, "q": "pass-0"}).json()
        assert (searched["total"], searched["attention_total"]) == (10, 0)
    store.close()
