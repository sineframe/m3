from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from mcp_pal import (
    ACPAgent,
    AgentExecutionSpec,
    ClaudeCode,
    ExecutionPage,
    ExecutionSpec,
    EvaluationId,
    EvaluationProvenance,
    EvaluationResult,
    EvaluationStatus,
    MCPTestKit,
    OpenCode,
    PersistedExecutionReport,
    RawEvidenceIntegrityError,
    RawEvidenceRef,
    RawEvidenceUnavailable,
    TextContent,
    TraceView,
    TraceUnavailable,
    UserMessage,
)
from mcp_pal.storage import SQLiteExecutionStore, StorageError
from mcp_pal.types import CallToolOperation, DirectExecutionSpec, ListToolsOperation, ServerBinding, StdioServer
from mcp_pal_app.api.app import create_app
from mcp_pal_app.settings import Settings


def _payload(run_id=None):
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="echo", command=sys.executable, args=("-m", "mcp_pal.fixtures.echo_server"))),),
        operation=CallToolOperation(server="echo", name="echo", arguments={"text": "hello"}),
        run_id=run_id,
    )
    return {"spec": spec.model_dump(mode="json")}


def _slow_payload():
    spec = DirectExecutionSpec(
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="echo",
                    command=sys.executable,
                    args=("-c", "import time; time.sleep(300)"),
                ),
            ),
        ),
        operation=ListToolsOperation(server="echo"),
        timeout_seconds=300.0,
    )
    return {"spec": spec.model_dump(mode="json")}


def _agent_spec(harness):
    return AgentExecutionSpec(
        harness=harness,
        servers=(
            ServerBinding(
                server=StdioServer(name="echo", command=sys.executable),
            ),
        ),
        message=UserMessage(content=(TextContent(text="only validate this spec"),)),
    )


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
    application = create_app(Settings(database_path=str(database)))
    with TestClient(application) as client:
        payload = _payload(run_id="api-run")
        created = client.post("/api/v2/executions", json=payload)
        assert created.status_code == 202
        body = created.json()
        assert body["version"] == "v2"
        execution_id = body["execution_id"]
        assert TypeAdapter(ExecutionSpec).validate_python(body["spec"]) == DirectExecutionSpec.model_validate(payload["spec"])
        assert body["spec"]["run_id"] == "api-run"
        assert body["snapshot"]["run_id"] == "api-run"
        assert body["snapshot"]["lifecycle"] in {"created", "queued", "starting", "finished"}
        listed = client.get("/api/v2/executions")
        assert TypeAdapter(ExecutionPage).validate_python(listed.json()["page"]).total == 1
        finished = _wait_finished(client, execution_id)
        assert finished["snapshot"]["outcome"] == "completed"
        page = client.get("/api/v2/executions", params={"limit": 1, "outcome": "completed"})
        typed_page = TypeAdapter(ExecutionPage).validate_python(page.json()["page"])
        assert typed_page.total == 1 and len(typed_page.items) == 1
        fetched = client.get(f"/api/v2/executions/{execution_id}")
        assert fetched.status_code == 200
        assert TypeAdapter(ExecutionSpec).validate_python(fetched.json()["spec"]) == TypeAdapter(ExecutionSpec).validate_python(body["spec"])
        assert fetched.json()["snapshot"]["run_id"] == "api-run"
        report = client.get(f"/api/v2/executions/{execution_id}/report")
        assert report.status_code == 200
        parsed_report = TypeAdapter(PersistedExecutionReport).validate_python(report.json()["report"])
        full_trace = TypeAdapter(TraceView).validate_python(report.json()["trace"])
        assert report.json()["report"]["direct_result"]["kind"] == "call_tool"
        assert report.json()["report"]["evidence"]["completeness"] == "partial"
        assert report.json()["trace"]["schema_version"] == "1.1"
        bounded = client.get(f"/api/v2/executions/{execution_id}/report", params={"event_limit": 2})
        assert bounded.json()["report"]["events_truncated"] is True
        assert TypeAdapter(TraceView).validate_python(bounded.json()["trace"]) == full_trace
        next_cursor = bounded.json()["report"]["next_after_sequence"]
        assert next_cursor == bounded.json()["report"]["events"][-1]["sequence"]
        follow_up = client.get(f"/api/v2/executions/{execution_id}/report", params={"after_sequence": next_cursor, "event_limit": 2})
        assert all(event["sequence"] > next_cursor for event in follow_up.json()["report"]["events"])
        assert not ({event["sequence"] for event in bounded.json()["report"]["events"]} & {event["sequence"] for event in follow_up.json()["report"]["events"]})
        artifact_limited = client.get(f"/api/v2/executions/{execution_id}/report", params={"artifact_limit": 1})
        assert artifact_limited.json()["report"]["artifact_count"] == 0
        assert artifact_limited.json()["report"]["artifacts_truncated"] is False

        application.state.v2_store.save_evaluation(
            execution_id,
            EvaluationResult(
                evaluation_id=EvaluationId("api-evaluation"),
                name="local.quality.v1",
                status=EvaluationStatus.PASSED,
                score=0.91,
                rationale="The local echo response matched the request.",
                metrics={"quality": 0.91},
                provenance=EvaluationProvenance(
                    kind="local-rule",
                    provider="test-suite",
                    model="fixture",
                    rubric_id="echo-quality",
                    rubric_version="1",
                ),
                context={
                    "execution_id": execution_id,
                    "subject": {"secret": "must not be returned"},
                    "metadata": {"source": "api-test"},
                },
            ),
        )
        evaluated_report = client.get(f"/api/v2/executions/{execution_id}/report")
        assert evaluated_report.status_code == 200
        evaluated_parsed = TypeAdapter(PersistedExecutionReport).validate_python(evaluated_report.json()["report"])
        saved_evaluation = evaluated_report.json()["report"]["evaluations"][0]
        assert saved_evaluation["score"] == 0.91
        assert saved_evaluation["rationale"] == "The local echo response matched the request."
        assert saved_evaluation["metrics"] == {"quality": 0.91}
        assert saved_evaluation["provenance"]["provider"] == "test-suite"
        assert saved_evaluation["provenance"]["rubric_id"] == "echo-quality"
        assert saved_evaluation["run_id"] == "api-run"
        assert "subject" not in saved_evaluation
        assert "context" not in saved_evaluation
        assert "callback" not in saved_evaluation
        assert "must not be returned" not in str(evaluated_report.json())
    reopened = SQLiteExecutionStore(database)
    try:
        assert reopened.get_snapshot(execution_id) == parsed_report.snapshot
        assert reopened.get_execution_spec(execution_id) == TypeAdapter(ExecutionSpec).validate_python(body["spec"])
        assert reopened.get_report(execution_id) == evaluated_parsed
        assert reopened.get_trace_view(execution_id) == full_trace
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
        cancelled_spec = TypeAdapter(ExecutionSpec).validate_python(cancelled.json()["spec"])
        assert isinstance(cancelled_spec, DirectExecutionSpec)
        cancelled_report = client.get(f"/api/v2/executions/{execution_id}/report")
        assert cancelled_report.status_code == 200
        assert TypeAdapter(TraceView).validate_python(cancelled_report.json()["trace"]).outcome == "cancelled"
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


def test_v2_accepts_every_serializable_execution_spec_variant(tmp_path):
    """The HTTP boundary accepts SDK JSON, without starting external harnesses."""

    database = Path(tmp_path).resolve() / "spec-variants.sqlite"
    store = SQLiteExecutionStore(database)
    kit = MCPTestKit(store=store, embedded_worker=False)
    specs = (
        DirectExecutionSpec.model_validate(_payload()["spec"]),
        _agent_spec(ClaudeCode(model="fixture", executable="not-started-claude")),
        _agent_spec(OpenCode(model="fixture", executable="not-started-opencode")),
        _agent_spec(ACPAgent(model="fixture", manifest={"command": "not-started-acp"})),
    )
    try:
        app = create_app(
            Settings(database_path=str(Path(tmp_path).resolve() / "unused.sqlite")),
            v2_store=store,
            v2_kit=kit,
        )
        with TestClient(app) as client:
            for submitted in specs:
                response = client.post(
                    "/api/v2/executions", json={"spec": submitted.model_dump(mode="json")}
                )
                assert response.status_code == 202
                response_spec = TypeAdapter(ExecutionSpec).validate_python(response.json()["spec"])
                assert response_spec == submitted
                assert store.get_execution_spec(response.json()["execution_id"]) == submitted
    finally:
        kit.close()
        store.close()


def test_v2_validation_codes_are_shape_stable_and_value_free(tmp_path):
    database = Path(tmp_path).resolve() / "validation-shapes.sqlite"
    canary = "v2-validation-secret-canary"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        cases = (
            ({}, "invalid_request"),
            ({"spec": None}, "invalid_request"),
            ({"spec": {}}, "invalid_execution_spec"),
            ({"spec": {"kind": canary}}, "invalid_execution_spec"),
            ({"spec": {"kind": "direct"}}, "invalid_execution_spec"),
            ({"spec": _payload()["spec"], "unexpected": canary}, "invalid_request"),
        )
        for payload, code in cases:
            response = client.post("/api/v2/executions", json=payload)
            assert response.status_code == 422
            body = response.content
            assert canary.encode() not in body
            assert response.json()["error"]["code"] == code


def test_v2_rejects_unsafe_spec_shapes_and_invalid_filters_without_echoing_values(tmp_path):
    database = Path(tmp_path).resolve() / "unsafe-shapes.sqlite"
    canary = "v2-unsafe-shape-secret"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        base = _payload()["spec"]
        unsafe_cases = (
            {"spec": {**base, "kind": "unknown-kind"}},
            {"spec": {"kind": "agent", "servers": base["servers"]}},
            {"spec": {**base, "servers": [{"server": {"kind": "in_process", "name": "runtime", "factory": {"pickle": canary}}}]}},
            {"spec": {**base, "metadata": {"callback": {"__callable__": canary}}}},
        )
        for payload in unsafe_cases:
            response = client.post("/api/v2/executions", json=payload)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "invalid_execution_spec"
            assert canary.encode() not in response.content
        for params in (
            {"lifecycle": "not-a-lifecycle"},
            {"outcome": "not-an-outcome"},
            {"limit": 0},
            {"offset": -1},
        ):
            response = client.get("/api/v2/executions", params=params)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "invalid_request"


def test_v2_service_failures_keep_stable_http_errors_and_hide_store_text(tmp_path, monkeypatch):
    database = Path(tmp_path).resolve() / "service-failures.sqlite"
    store = SQLiteExecutionStore(database)
    kit = MCPTestKit(store=store, embedded_worker=False)
    canary = "underlying-store-secret"
    try:
        app = create_app(Settings(database_path=str(Path(tmp_path).resolve() / "unused.sqlite")), v2_store=store, v2_kit=kit)
        with TestClient(app) as client:
            created = client.post("/api/v2/executions", json=_payload())
            execution_id = created.json()["execution_id"]
            monkeypatch.setattr(
                store,
                "get_execution_spec",
                lambda _execution_id: (_ for _ in ()).throw(StorageError(canary)),
            )
            unavailable = client.get(f"/api/v2/executions/{execution_id}")
            assert unavailable.status_code == 500
            assert unavailable.json()["error"]["code"] == "execution_data_unavailable"
            assert canary.encode() not in unavailable.content
            monkeypatch.undo()
            reference = RawEvidenceRef(evidence_id="synthetic-evidence")
            for failure, status_code, code in (
                (RawEvidenceUnavailable(canary), 404, "raw_evidence_not_found"),
                (RawEvidenceIntegrityError(canary), 500, "raw_evidence_integrity_error"),
            ):
                monkeypatch.setattr(
                    store,
                    "read_raw_evidence",
                    lambda _reference, *, max_bytes, failure=failure: (_ for _ in ()).throw(failure),
                )
                response = client.post("/api/v2/evidence/read", json={"reference": reference.model_dump(mode="json")})
                assert response.status_code == status_code
                assert response.json()["error"]["code"] == code
                assert canary.encode() not in response.content
                monkeypatch.undo()
    finally:
        kit.close()
        store.close()


def test_v2_trace_store_failure_is_a_sanitized_http_error(tmp_path, monkeypatch):
    database = Path(tmp_path).resolve() / "trace-failure.sqlite"
    canary = "trace-store-secret"
    app = create_app(Settings(database_path=str(database)))
    with TestClient(app) as client:
        execution_id = client.post("/api/v2/executions", json=_payload()).json()["execution_id"]
        _wait_finished(client, execution_id)
        monkeypatch.setattr(
            app.state.v2_store,
            "get_trace_view",
            lambda _execution_id: (_ for _ in ()).throw(TraceUnavailable(canary)),
        )
        response = client.get(f"/api/v2/executions/{execution_id}/report")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "trace_unavailable"
        assert canary.encode() not in response.content


def test_v2_openapi_preserves_sdk_discriminators(tmp_path):
    app = create_app(Settings(database_path=str(Path(tmp_path).resolve() / "openapi.sqlite")))
    document = app.openapi()
    schemas = document["components"]["schemas"]
    spec_schema = schemas["V2ExecutionCreate"]["properties"]["spec"]
    assert set(spec_schema["discriminator"]["mapping"]) == {"direct", "agent"}
    assert "oneOf" in spec_schema and spec_schema.get("type") != "object"
    report_schema = schemas["V2ExecutionReportEnvelope"]["properties"]
    assert report_schema["report"]["$ref"].endswith("/PersistedExecutionReport")
    assert report_schema["trace"]["$ref"].endswith("/TraceView")
    trace_schema = schemas["TraceView"]["properties"]
    assert trace_schema["runtime"]["discriminator"]["propertyName"] == "kind"
    assert trace_schema["timeline"]["items"]["discriminator"]["mapping"]["transport"].endswith("/TransportEntry")
    assert schemas["V2EvidenceRead"]["properties"]["reference"]["$ref"].endswith("/RawEvidenceRef")
    assert {"reference", "content", "truncated", "redacted"} <= set(schemas["RawEvidence"]["properties"])

    def visit(value):
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
                assert reference.removeprefix("#/components/schemas/") in schemas
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(document)


def test_v2_store_and_kit_can_be_injected(tmp_path):
    database = Path(tmp_path).resolve() / "injected.sqlite"
    store = SQLiteExecutionStore(database)
    kit = MCPTestKit(store=store, embedded_worker=False)
    try:
        app = create_app(Settings(database_path=str(Path(tmp_path).resolve() / "unused.sqlite")), v2_store=store, v2_kit=kit)
        assert app.state.v2_store is store
        assert app.state.v2_kit is kit
        with TestClient(app) as client:
            assert client.get("/api/v2/executions").json()["page"]["total"] == 0
    finally:
        kit.close()
        store.close()


def test_v2_module_has_no_legacy_orm_or_run_manager_imports():
    source = Path(__file__).parents[2] / "src/mcp_pal_app/api/v2.py"
    text = source.read_text()
    assert "RunManager" not in text
    assert "mcp_pal_app.persistence" not in text
    assert "v2_executions" not in text
