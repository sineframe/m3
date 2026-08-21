import tempfile, time
from fastapi.testclient import TestClient
from mcp_pal.api import create_app
from mcp_pal.config import Settings

def test_profile_revision_and_run_api():
    settings=Settings(database_path=tempfile.mktemp(suffix=".db"), anthropic_api_key="key", claude_model_ids=["test-model"], claude_executable="/definitely/missing/claude")
    client=TestClient(create_app(settings))
    profile=client.post("/api/v1/profiles",json={"name":"demo","mcp_json":{"mcpServers":{"srv":{"command":"echo"}}}}).json()
    assert profile["revisions"][0]["revision_number"] == 1
    revision=profile["current_revision_id"]
    updated=client.post(f"/api/v1/profiles/{profile['id']}/revisions",json={"mcp_json":{"mcpServers":{"srv":{"command":"printf"}}}})
    assert updated.status_code == 201 and updated.json()["current_revision_id"] != revision
    run=client.post("/api/v1/runs",json={"model":"test-model","prompt":"exact prompt","expected_output":"goal","profile_revision_id":revision,"enabled_server":"srv"})
    assert run.status_code == 202
    run_id=run.json()["id"]
    for _ in range(30):
        state=client.get(f"/api/v1/runs/{run_id}").json()
        if state["status"] not in {"queued","running"}: break
        time.sleep(.02)
    assert state["status"] == "failed"
    assert client.get(f"/api/v1/runs/{run_id}/report").status_code == 200
