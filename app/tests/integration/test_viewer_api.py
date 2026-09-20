from pathlib import Path

from _local_client import TestClient

from m3 import EvaluationQuery
from m3_app.api.app import create_app, create_viewer_app
from m3_app.api.v2 import (
    V2CapabilitiesOut,
    V2EvidenceRead,
    V2ExecutionCreate,
    V2HarnessCreate,
    V2HarnessRevisionCreate,
    V2ProbeCreate,
    V2ProfileCreate,
    V2ProfileRevisionCreate,
)
from m3_app.settings import Settings


def test_viewer_allows_history_reads_and_semantic_evidence_post(tmp_path: Path) -> None:
    settings = Settings(database_path=str(tmp_path / "viewer.sqlite"))
    application = create_viewer_app(settings)
    assert application.state.v2_kit._embedded_worker is False
    with TestClient(application) as client:
        assert client.get("/api/v2/executions").status_code == 200
        assert (
            client.get(
                "/api/v2/executions", headers={"host": "attacker.example"}
            ).status_code
            == 400
        )
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
            client.post("/api/v2/profiles", json={}),
            client.delete("/api/v2/executions/history", params={"confirm": "true"}),
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


def test_openapi_matches_standard_and_viewer_surfaces(tmp_path: Path) -> None:
    standard = create_app(Settings(database_path=str(tmp_path / "standard.sqlite")))
    viewer = create_viewer_app(
        Settings(database_path=str(tmp_path / "viewer-schema.sqlite"))
    )
    standard_schema = standard.openapi()
    viewer_schema = viewer.openapi()
    standard_ops = {
        (method, path)
        for path, item in standard_schema["paths"].items()
        for method in item
        if method in {"get", "post", "patch", "delete"}
    }
    viewer_ops = {
        (method, path)
        for path, item in viewer_schema["paths"].items()
        for method in item
        if method in {"get", "post", "patch", "delete"}
    }
    assert len(standard_ops) == 32
    expected_viewer_ops = {
        ("get", path)
        for path in (
            "/api/v2/profiles",
            "/api/v2/profiles/{profile_id}",
            "/api/v2/harness-profiles",
            "/api/v2/harness-profiles/{profile_id}",
            "/api/v2/harness-profiles/{profile_id}/export",
            "/api/v2/capabilities",
            "/api/v2/readiness",
            "/api/v2/health",
            "/api/v2/harness-profiles/{profile_id}/probes",
            "/api/v2/executions",
            "/api/v2/executions/{execution_id}",
            "/api/v2/executions/{execution_id}/report",
            "/api/v2/suites/{suite_id}/executions",
            "/api/v2/feedback/{run_id}",
        )
    } | {
        ("post", "/api/v2/evidence/read"),
        ("post", "/api/v2/evaluations/aggregate"),
    }
    assert viewer_ops == expected_viewer_ops
    assert ("post", "/api/v2/profiles") in standard_ops
    assert ("post", "/api/v2/profiles") not in viewer_ops
    assert standard_schema["paths"]["/api/v2/profiles"]["post"]["responses"]["422"][
        "content"
    ]["application/json"]["schema"]["$ref"].endswith("/V2ErrorEnvelope")
    assert "loopback" in standard_schema["info"]["description"]
    assert standard_schema["servers"] == [
        {"url": "/", "description": "Local app origin"}
    ]
    assert "422" not in standard_schema["paths"]["/api/v2/health"]["get"]["responses"]
    assert (
        "500" not in standard_schema["paths"]["/api/v2/executions"]["get"]["responses"]
    )
    assert (
        "405" not in standard_schema["paths"]["/api/v2/executions"]["get"]["responses"]
    )
    execution_examples = standard_schema["paths"]["/api/v2/executions"]["post"][
        "requestBody"
    ]["content"]["application/json"]["examples"]
    assert {"safe", "agent"} <= set(execution_examples)
    assert (
        "safe"
        in standard_schema["paths"]["/api/v2/harness-profiles"]["post"]["requestBody"][
            "content"
        ]["application/json"]["examples"]
    )
    assert (
        "safe"
        in standard_schema["paths"]["/api/v2/harness-profiles/{profile_id}/probes"][
            "post"
        ]["requestBody"]["content"]["application/json"]["examples"]
    )
    assert any(
        parameter.get("name") == "after_sequence" and parameter.get("example") == 120
        for parameter in standard_schema["paths"][
            "/api/v2/executions/{execution_id}/report"
        ]["get"]["parameters"]
    )
    assert any(
        parameter.get("example") == "run-baseline"
        for parameter in standard_schema["paths"]["/api/v2/feedback/{run_id}"]["get"][
            "parameters"
        ]
    )
    with TestClient(standard) as client:
        capabilities = client.get("/api/v2/capabilities")
        assert capabilities.status_code == 200
        V2CapabilitiesOut.model_validate(capabilities.json())


def test_openapi_request_examples_validate_against_models(tmp_path: Path) -> None:
    application = create_app(Settings(database_path=str(tmp_path / "examples.sqlite")))
    schema = application.openapi()
    models = {
        "/api/v2/executions": V2ExecutionCreate,
        "/api/v2/profiles": V2ProfileCreate,
        "/api/v2/profiles/{profile_id}/revisions": V2ProfileRevisionCreate,
        "/api/v2/harness-profiles": V2HarnessCreate,
        "/api/v2/harness-profiles/{profile_id}/revisions": V2HarnessRevisionCreate,
        "/api/v2/harness-profiles/{profile_id}/probes": V2ProbeCreate,
        "/api/v2/evidence/read": V2EvidenceRead,
        "/api/v2/evaluations/aggregate": EvaluationQuery,
    }
    for path, model in models.items():
        examples = schema["paths"][path]["post"]["requestBody"]["content"][
            "application/json"
        ]["examples"]
        for example in examples.values():
            model.model_validate(example["value"])


def test_openapi_profile_examples_create_and_revise(tmp_path: Path) -> None:
    application = create_app(
        Settings(database_path=str(tmp_path / "profile-examples.sqlite"))
    )
    schema = application.openapi()
    with TestClient(application) as client:
        profile_example = schema["paths"]["/api/v2/profiles"]["post"]["requestBody"][
            "content"
        ]["application/json"]["examples"]["safe"]["value"]
        created = client.post("/api/v2/profiles", json=profile_example)
        assert created.status_code == 201, created.text
        profile_id = created.json()["id"]
        revision_example = schema["paths"]["/api/v2/profiles/{profile_id}/revisions"][
            "post"
        ]["requestBody"]["content"]["application/json"]["examples"]["safe"]["value"]
        revised = client.post(
            f"/api/v2/profiles/{profile_id}/revisions", json=revision_example
        )
        assert revised.status_code == 201, revised.text

        harness_example = schema["paths"]["/api/v2/harness-profiles"]["post"][
            "requestBody"
        ]["content"]["application/json"]["examples"]["safe"]["value"]
        harness = client.post("/api/v2/harness-profiles", json=harness_example)
        assert harness.status_code == 201, harness.text
        harness_id = harness.json()["id"]
        harness_revision = schema["paths"][
            "/api/v2/harness-profiles/{profile_id}/revisions"
        ]["post"]["requestBody"]["content"]["application/json"]["examples"]["safe"][
            "value"
        ]
        revised_harness = client.post(
            f"/api/v2/harness-profiles/{harness_id}/revisions", json=harness_revision
        )
        assert revised_harness.status_code == 201, revised_harness.text
        exported = client.get(f"/api/v2/harness-profiles/{harness_id}/export")
        assert exported.status_code == 200, exported.text
        assert set(exported.json()) == {"name", "description", "manifest"}
        import_example = schema["paths"]["/api/v2/harness-profiles/import"]["post"][
            "requestBody"
        ]["content"]["application/json"]["examples"]["safe"]["value"]
        imported = client.post("/api/v2/harness-profiles/import", json=import_example)
        assert imported.status_code == 201, imported.text
