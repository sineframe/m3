"""Opt-in DeepWiki v2/API -> SQLite -> SDK evaluation smoke test."""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from mcp_pal import (
    CallToolOperation,
    DirectExecutionSpec,
    MCPTestKit,
    ServerBinding,
    StreamableHTTPServer,
)
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal_app.api.app import create_app
from mcp_pal_app.settings import Settings


pytestmark = [pytest.mark.e2e, pytest.mark.live]


def test_deepwiki_v2_execution_reopen_and_builtin_evaluation(tmp_path) -> None:
    if os.environ.get("MCP_PAL_RUN_DEEPWIKI_LIVE") != "1":
        pytest.skip("set MCP_PAL_RUN_DEEPWIKI_LIVE=1 to run the external DeepWiki test")
    url = os.environ.get("MCP_PAL_DEEPWIKI_URL", "https://mcp.deepwiki.com/mcp")

    database = tmp_path / "deepwiki-live.sqlite"
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=StreamableHTTPServer(name="deepwiki", url=url)),),
        operation=CallToolOperation(
            server="deepwiki",
            name="read_wiki_structure",
            arguments={"repoName": "modelcontextprotocol/python-sdk"},
        ),
    )
    application = create_app(Settings(database_path=str(database)))
    with TestClient(application) as client:
        created = client.post("/api/v2/executions", json={"spec": spec.model_dump(mode="json")})
        assert created.status_code == 202
        execution_id = created.json()["execution_id"]
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
        events = report["events"]
        assert any(
            event.get("kind") == "transport.connected"
            and event.get("payload", {}).get("configured_transport") == "streamable_http"
            for event in events
        ), "expected Streamable HTTP transport evidence in the saved trace"
        blocks = report["direct_result"].get("content", ())
        text = "".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        assert text.strip(), "DeepWiki returned no text content"

    reopened = SQLiteExecutionStore(database)
    try:
        persisted = reopened.get_report(execution_id)
        assert persisted is not None
        with MCPTestKit(store=reopened) as kit:
            evaluation = kit.evaluate(persisted, "mcp_pal.output.has_text.v1")
            assert evaluation.status.value == "passed"
    finally:
        reopened.close()
    with TestClient(create_app(Settings(database_path=str(database)))) as report_client:
        evaluated_report = report_client.get(f"/api/v2/executions/{execution_id}/report")
        assert evaluated_report.status_code == 200
        evaluations = evaluated_report.json()["report"]["evaluations"]
        assert any(
            evaluation["name"] == "mcp_pal.output.has_text.v1"
            and evaluation["status"] == "passed"
            for evaluation in evaluations
        )
