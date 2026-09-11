from fastapi.testclient import TestClient

from mcp_pal_app.api import create_app
from mcp_pal_app.settings import Settings


def test_harness_profile_lifecycle_and_secret_safe_export(tmp_path):
    client = TestClient(create_app(Settings(database_path=str(tmp_path / "db.sqlite"))))
    body = {
        "name": "Fixture",
        "description": "local",
        "manifest": {
            "command": "fixture-agent",
            "args": ["--acp"],
            "env": {"TOKEN": "${FIXTURE_TOKEN}"},
        },
        "trusted_unsandboxed": True,
    }
    created = client.post("/api/v1/harness-profiles", json=body)
    assert created.status_code == 201
    pid = created.json()["id"]
    detail = client.get(f"/api/v1/harness-profiles/{pid}").json()
    assert detail["revisions"][0]["manifest"]["env"] == {"TOKEN": "${FIXTURE_TOKEN}"}
    exported = client.get(f"/api/v1/harness-profiles/{pid}/export").json()
    assert "FIXTURE_TOKEN" in exported["manifest"]["env"]["TOKEN"]
    imported = client.post("/api/v1/harness-profiles/import", json=exported)
    assert imported.status_code == 201 and imported.json()["id"] != pid
    assert client.post(f"/api/v1/harness-profiles/{pid}/archive").status_code == 200
    assert (
        client.post(f"/api/v1/harness-profiles/{pid}/restore").json()["archived"]
        is False
    )


def test_raw_manifest_import_matches_documented_file_shape(tmp_path):
    client = TestClient(
        create_app(Settings(database_path=str(tmp_path / "raw.sqlite")))
    )
    response = client.post(
        "/api/v1/harness-profiles/import",
        json={"command": "local-agent", "args": ["--acp"], "env": {}},
    )
    assert response.status_code == 201
    profile = client.get(f"/api/v1/harness-profiles/{response.json()['id']}").json()
    assert profile["revisions"][0]["manifest"]["command"] == "local-agent"
    assert profile["revisions"][0]["trusted_unsandboxed"] is False
