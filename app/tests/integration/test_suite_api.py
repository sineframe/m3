from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from _local_client import TestClient

from mcp_pal import (
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    ExecutionId,
    ExecutionState,
)
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal_app.api.app import create_app
from mcp_pal_app.settings import Settings


def test_suite_execution_endpoint_and_aggregate_filter(tmp_path: Path) -> None:
    db = tmp_path / "suite-api.sqlite"
    store = SQLiteExecutionStore(db)
    catalog = store.ensure_suite("catalog")
    other = store.ensure_suite("other")
    empty = store.ensure_suite("empty")
    entries = (
        ("c1", catalog, "run-1", datetime(2026, 8, 1, tzinfo=timezone.utc), True),
        ("c2", catalog, "run-2", datetime(2026, 8, 2, tzinfo=timezone.utc), False),
        ("o1", other, "run-4", datetime(2026, 8, 2, tzinfo=timezone.utc), True),
    )
    for ident, suite, run_id, created_at, passed in entries:
        store.create(
            ExecutionState(
                execution_id=ExecutionId(ident),
                suite_id=suite.id,
                suite_name=suite.name,
                run_id=run_id,
                created_at=created_at,
            )
        )
        store.save_evaluation(
            ExecutionId(ident),
            EvaluationResult(
                evaluation_id=f"ev-{ident}",
                name="quality.v1",
                status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
                context=EvaluationContext(execution_id=ExecutionId(ident)),
            ),
        )
    app = create_app(
        Settings(database_path=str(tmp_path / "unused.sqlite")), v2_store=store
    )
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        assert "/api/v2/suites/{suite_id}/executions" in schema["paths"]
        assert client.post("/api/v2/executions", json={"spec": None}).status_code == 422
        response = client.get(f"/api/v2/suites/{catalog.id.root}/executions?limit=1")
        assert response.status_code == 200
        body = response.json()
        assert body["suite"] == {"suite_id": catalog.id.root, "suite_name": "catalog"}
        assert body["page"]["total"] == 2
        assert len(body["page"]["items"]) == 1
        assert body["page"]["items"][0]["suite_id"] == catalog.id.root
        assert body["page"]["items"][0]["suite_name"] == "catalog"
        offset = client.get(f"/api/v2/suites/{catalog.id.root}/executions?offset=1")
        assert (
            offset.status_code == 200
            and offset.json()["page"]["items"][0]["execution_id"]
            != body["page"]["items"][0]["execution_id"]
        )
        by_run = client.get(f"/api/v2/suites/{catalog.id.root}/executions?run_id=run-2")
        assert (
            by_run.status_code == 200
            and by_run.json()["page"]["items"][0]["execution_id"] == "c2"
        )
        assert (
            client.get(
                f"/api/v2/suites/{catalog.id.root}/executions?outcome=completed"
            ).status_code
            == 200
        )
        assert client.get("/api/v2/suites/999999/executions").status_code == 404
        assert (
            client.get(f"/api/v2/suites/{empty.id.root}/executions").json()["page"][
                "total"
            ]
            == 0
        )
        aggregate = client.post(
            "/api/v2/evaluations/aggregate",
            json={"filters": {"evaluator": ["quality.v1"]}, "group_by": ["suite_name"]},
        )
        assert aggregate.status_code == 200
        groups = {
            item["key"]["suite_name"]: item["values"]
            for item in aggregate.json()["aggregate"]["groups"]
        }
        assert set(groups) == {"catalog", "other"}
        assert (
            groups["catalog"]["evaluation_count"],
            groups["catalog"]["measured_count"],
            groups["catalog"]["pass_rate"],
        ) == (2, 2, 0.5)
        assert (
            groups["other"]["evaluation_count"],
            groups["other"]["measured_count"],
            groups["other"]["pass_rate"],
        ) == (1, 1, 1.0)
        daily_response = client.post(
            "/api/v2/evaluations/aggregate",
            json={
                "filters": {"suite_name": ["catalog"], "evaluator": ["quality.v1"]},
                "group_by": ["time.day"],
            },
        )
        assert daily_response.status_code == 200
        daily = daily_response.json()["aggregate"]["groups"]
        assert [
            (
                item["key"]["time.day"],
                item["values"]["evaluation_count"],
                item["values"]["pass_rate"],
            )
            for item in daily
        ] == [("2026-08-01", 1, 1.0), ("2026-08-02", 1, 0.0)]
        direct = client.get("/api/v2/executions/c1")
        assert direct.status_code == 200 and direct.json()["spec"] is None
        report_response = client.get("/api/v2/executions/c1/report")
        assert report_response.status_code in {404, 409}
