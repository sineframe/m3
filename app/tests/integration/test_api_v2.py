from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient

from mcp_pal import MCPTestKit
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal.types import CallToolOperation, DirectExecutionSpec, ListToolsOperation, ServerBinding, StdioServer
from mcp_pal_app.api.app import create_app
from mcp_pal_app.settings import Settings


def _payload():
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="echo", command=sys.executable, args=("-m", "mcp_pal.fixtures.echo_server"))),),
        operation=CallToolOperation(server="echo", name="echo", arguments={"text": "hello"}),
    )
    return {"spec": spec.model_dump(mode="json")}


def _slow_payload():
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="echo", command="sleep", args=("30",))),),
        operation=ListToolsOperation(server="echo"),
    )
    return {"spec": spec.model_dump(mode="json")}


def _wait_finished(client, execution_id):
    for _ in range(60):
        response = client.get(f"/api/v2/executions/{execution_id}")
        assert response.status_code == 200
        body = response.json()
        if body["snapshot"]["lifecycle"] == "finished":
            return body
        time.sleep(0.05)
    raise AssertionError("execution did not become terminal")


def test_v2_execution_lifecycle_and_reopen(tmp_path):
    database = Path(tmp_path).resolve() / "v2.sqlite"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        created = client.post("/api/v2/executions", json=_payload())
        assert created.status_code == 202
        body = created.json()
        assert body["version"] == "v2"
        execution_id = body["execution_id"]
        assert body["snapshot"]["lifecycle"] in {"created", "queued", "starting", "finished"}
        assert client.get("/api/v2/executions").json()["total"] == 1
        finished = _wait_finished(client, execution_id)
        assert finished["snapshot"]["outcome"] == "completed"
        page = client.get("/api/v2/executions", params={"limit": 1, "outcome": "completed"})
        assert page.json()["total"] == 1 and len(page.json()["items"]) == 1
        report = client.get(f"/api/v2/executions/{execution_id}/report")
        assert report.status_code == 200
        assert report.json()["direct_result"]["kind"] == "call_tool"
        assert report.json()["evidence"]["completeness"] == "partial"
        bounded = client.get(f"/api/v2/executions/{execution_id}/report", params={"event_limit": 2})
        assert bounded.json()["events_truncated"] is True
        next_cursor = bounded.json()["next_after_sequence"]
        assert next_cursor == bounded.json()["events"][-1]["sequence"]
        follow_up = client.get(f"/api/v2/executions/{execution_id}/report", params={"after_sequence": next_cursor, "event_limit": 2})
        assert all(event["sequence"] > next_cursor for event in follow_up.json()["events"])
        assert not ({event["sequence"] for event in bounded.json()["events"]} & {event["sequence"] for event in follow_up.json()["events"]})
        artifact_limited = client.get(f"/api/v2/executions/{execution_id}/report", params={"artifact_limit": 1})
        assert artifact_limited.json()["artifact_count"] == 0
        assert artifact_limited.json()["artifacts_truncated"] is False
    reopened = SQLiteExecutionStore(database)
    try:
        assert reopened.get_snapshot(execution_id) is not None
        reopened_report = reopened.get_report(execution_id)
        assert reopened_report.snapshot.outcome.value == "completed"
        assert reopened_report.direct_result.kind == "call_tool"
    finally:
        reopened.close()


def test_v2_errors_and_deletion_constraints(tmp_path):
    database = Path(tmp_path).resolve() / "errors.sqlite"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        missing = client.get("/api/v2/executions/no-such")
        assert missing.status_code == 404
        assert missing.json()["version"] == "v2"
        assert missing.json()["error"]["code"] == "execution_not_found"
        unsafe = client.post("/api/v2/executions", json={"spec": {"pickle": "arbitrary"}})
        assert unsafe.status_code == 422
        assert unsafe.json()["error"]["code"] == "invalid_execution_spec"
        created = client.post("/api/v2/executions", json=_slow_payload()).json()
        execution_id = created["execution_id"]
        blocked = client.delete(f"/api/v2/executions/{execution_id}")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "execution_active"
        cancelled = client.post(f"/api/v2/executions/{execution_id}/cancel")
        assert cancelled.status_code == 200
        terminal_cancel = client.post(f"/api/v2/executions/{execution_id}/cancel")
        assert terminal_cancel.status_code == 409
        deleted = client.delete(f"/api/v2/executions/{execution_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"version": "v2", "execution_id": execution_id, "deleted": True}
        assert client.get(f"/api/v2/executions/{execution_id}").status_code == 404


def test_v2_validation_and_json_metadata(tmp_path):
    database = Path(tmp_path).resolve() / "validation.sqlite"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        invalid_query = client.get("/api/v2/executions", params={"limit": 0})
        assert invalid_query.status_code == 422
        assert invalid_query.json()["error"]["code"] == "invalid_request"
        spec = _payload()["spec"]
        spec["metadata"] = {"pickle": "inert JSON metadata"}
        created = client.post("/api/v2/executions", json={"spec": spec})
        assert created.status_code == 202
        unsafe_spec = _payload()["spec"]
        unsafe_spec["servers"][0]["server"] = {"name": "runtime", "kind": "in_process", "factory": {"module": "not-callable"}}
        unsafe = client.post("/api/v2/executions", json={"spec": unsafe_spec})
        assert unsafe.status_code == 422
        assert unsafe.json()["error"]["code"] == "invalid_execution_spec"


def test_v2_store_and_kit_can_be_injected(tmp_path):
    database = Path(tmp_path).resolve() / "injected.sqlite"
    store = SQLiteExecutionStore(database)
    kit = MCPTestKit(store=store, embedded_worker=False)
    try:
        app = create_app(Settings(database_path=str(Path(tmp_path).resolve() / "unused.sqlite")), v2_store=store, v2_kit=kit)
        assert app.state.v2_store is store
        assert app.state.v2_kit is kit
        with TestClient(app) as client:
            assert client.get("/api/v2/executions").json()["total"] == 0
    finally:
        kit.close()
        store.close()


def test_v2_module_has_no_legacy_orm_or_run_manager_imports():
    source = Path(__file__).parents[2] / "src/mcp_pal_app/api/v2.py"
    text = source.read_text()
    assert "RunManager" not in text
    assert "mcp_pal_app.persistence" not in text
    assert "v2_executions" not in text
