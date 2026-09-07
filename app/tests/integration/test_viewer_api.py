from pathlib import Path

from fastapi.testclient import TestClient

from mcp_pal_app.api.app import create_app, create_viewer_app
from mcp_pal_app.settings import Settings


def test_viewer_allows_history_reads_and_semantic_evidence_post(tmp_path: Path) -> None:
    settings = Settings(database_path=str(tmp_path / "viewer.sqlite"))
    application = create_viewer_app(settings)
    assert application.state.v2_kit._embedded_worker is False
    with TestClient(application) as client:
        assert client.get("/api/v2/executions").status_code == 200
        # A structurally invalid evidence request reaches normal validation;
        # it is not rejected by the read-only boundary.
        assert client.post("/api/v2/evidence/read", json={}).status_code == 422


def test_viewer_rejects_v1_and_v2_mutations(tmp_path: Path) -> None:
    settings = Settings(database_path=str(tmp_path / "viewer.sqlite"))
    with TestClient(create_viewer_app(settings)) as client:
        attempts = (
            client.post("/api/v2/executions", json={}),
            client.post("/api/v2/executions/not-present/cancel"),
            client.delete("/api/v2/executions/not-present"),
            client.post("/api/v1/profiles", json={}),
            client.delete("/api/v1/runs/history", params={"confirm": "true"}),
        )
    for response in attempts:
        assert response.status_code == 405
        assert response.json() == {"detail": "viewer API is read-only"}


def test_normal_app_mutation_behavior_is_unchanged(tmp_path: Path) -> None:
    settings = Settings(database_path=str(tmp_path / "normal.sqlite"))
    application = create_app(settings)
    assert application.state.v2_kit._embedded_worker is True
    with TestClient(application) as client:
        # Normal routing/validation handles this request; viewer middleware
        # must not be installed on the stable application factory.
        response = client.post("/api/v2/executions", json={})
    assert response.status_code == 422
    assert response.json() != {"detail": "viewer API is read-only"}
