"""Opt-in DeepWiki v2/API -> SQLite -> SDK evaluation smoke test."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from mcp_pal import (
    CallTool,
    DirectSpec,
    MCPTestKit,
    ServerBinding,
    HTTPServer,
)
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal_app.api.app import create_app
from mcp_pal_app.settings import Settings


pytestmark = [pytest.mark.e2e, pytest.mark.live]


def test_deepwiki_v2_execution_reopen_and_builtin_evaluation(tmp_path, monkeypatch) -> None:
    if os.environ.get("MCP_PAL_RUN_DEEPWIKI_LIVE") != "1":
        pytest.skip("set MCP_PAL_RUN_DEEPWIKI_LIVE=1 to run the external DeepWiki test")
    url = os.environ.get("MCP_PAL_DEEPWIKI_URL", "https://mcp.deepwiki.com/mcp")
    # These are test-runner controls, not MCP Pal settings. Capture them before
    # constructing the app because settings rejects unknown MCP_PAL_* names.
    monkeypatch.delenv("MCP_PAL_RUN_DEEPWIKI_LIVE", raising=False)
    monkeypatch.delenv("MCP_PAL_DEEPWIKI_URL", raising=False)

    database = tmp_path / "deepwiki-live.sqlite"
    base_spec = DirectSpec(
        servers=(ServerBinding(server=HTTPServer(name="deepwiki", url=url)),),
        operation=CallTool(
            server="deepwiki",
            name="read_wiki_structure",
            arguments={"repoName": "modelcontextprotocol/python-sdk"},
        ),
    )
    specs = tuple(
        base_spec.model_copy(update={"run_id": run_id, "case_id": "deepwiki/read-structure"})
        for run_id in ("deepwiki-live-run-a", "deepwiki-live-run-a", "deepwiki-live-run-b")
    )
    application = create_app(Settings(database_path=str(database)))
    execution_ids: list[str] = []
    with TestClient(application) as client:
        for spec in specs:
            created = client.post("/api/v2/executions", json={"spec": spec.model_dump(mode="json")})
            assert created.status_code == 202
            execution_ids.append(created.json()["execution_id"])
        for execution_id in execution_ids:
            for _ in range(240):
                response = client.get(f"/api/v2/executions/{execution_id}")
                assert response.status_code == 200
                if response.json()["snapshot"]["lifecycle"] == "finished":
                    break
                time.sleep(0.25)
            else:
                raise AssertionError("DeepWiki execution did not become terminal")
            report_response = client.get(f"/api/v2/executions/{execution_id}/report")
            assert report_response.status_code == 200
            report = report_response.json()["report"]
            assert report["direct_result"] is not None
            assert report["direct_result"].get("is_error") is False
            assert any(
                event.get("kind") == "transport.connected"
                and event.get("payload", {}).get("configured_transport") == "streamable_http"
                for event in report["events"]
            ), "expected Streamable HTTP transport evidence in the saved trace"
            blocks = report["direct_result"].get("content", ())
            text = "".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict) and block.get("type") == "text")
            assert text.strip(), "DeepWiki returned no text content"

    reopened = SQLiteExecutionStore(database)
    try:
        with MCPTestKit(store=reopened) as kit:
            for execution_id in execution_ids:
                persisted = reopened.get_report(execution_id)
                assert persisted is not None
                evaluation = kit.evaluate(persisted, "mcp_pal.output.has_text.v1")
                assert evaluation.status.value == "passed"
    finally:
        reopened.close()
    with TestClient(create_app(Settings(database_path=str(database)))) as report_client:
        for execution_id in execution_ids:
            evaluated_report = report_client.get(f"/api/v2/executions/{execution_id}/report")
            assert evaluated_report.status_code == 200
            evaluations = evaluated_report.json()["report"]["evaluations"]
            assert any(evaluation["name"] == "mcp_pal.output.has_text.v1" and evaluation["status"] == "passed" for evaluation in evaluations)
        now = datetime.now(timezone.utc)
        aggregate_payload = {
            "from": (now - timedelta(days=1)).isoformat(), "to": (now + timedelta(days=1)).isoformat(),
            "group_by": ["run_id", "evaluator"],
            "filters": {"evaluator": "mcp_pal.output.has_text.v1"},
        }
        aggregate = report_client.post("/api/v2/evaluations/aggregate", json=aggregate_payload)
        assert aggregate.status_code == 200
        body = aggregate.json()["aggregate"]
        assert body["totals"]["trial_count"] == 3
        assert body["totals"]["pass_rate"] == 1.0
        assert body["totals"]["health"]["execution_duration_ms"]["count"] == 3
        assert body["totals"]["health"]["server_latency_ms"]["count"] > 0
        assert {group["key"]["run_id"] for group in body["groups"]} == {"deepwiki-live-run-a", "deepwiki-live-run-b"}
        calendar = report_client.post("/api/v2/evaluations/aggregate", json={**aggregate_payload, "group_by": ["time.day", "evaluator"]})
        assert calendar.status_code == 200
        assert calendar.json()["aggregate"]["groups"]
        trial = report_client.post("/api/v2/evaluations/aggregate", json={**aggregate_payload, "group_by": ["trial_id", "evaluator"]})
        assert trial.status_code == 200
        returned_trial = trial.json()["aggregate"]["groups"][0]["key"]["trial_id"]
        drilldown = report_client.get(f"/api/v2/executions/{returned_trial}/report")
        assert drilldown.status_code == 200
