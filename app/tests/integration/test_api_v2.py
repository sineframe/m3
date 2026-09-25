import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _local_client import TestClient
from pydantic import TypeAdapter

from m3 import (
    ACPAgent,
    ClaudeCode,
    EvaluationId,
    EvaluationResult,
    EvaluationSource,
    EvaluationStatus,
    EvidenceRef,
    ExecutionPage,
    ExecutionReport,
    ExecutionSpec,
    ExecutionState,
    InProcessServer,
    MCPTestKit,
    OpenCode,
    RawEvidenceIntegrityError,
    RawEvidenceUnavailable,
    SecretReference,
    TextContent,
    TraceUnavailable,
    TraceView,
    UserMessage,
)
from m3._types.specs import AgentSpec, FullToolPolicy
from m3.storage import InMemoryExecutionStore, SQLiteExecutionStore, StorageError
from m3.types import (
    CallTool,
    DirectSpec,
    HTTPServer,
    ListTools,
    ServerBinding,
    StdioServer,
)
from m3_app.api.app import create_app
from m3_app.api.v2 import V2RunSummary, V2SuiteRef, _group_run_page, _visible_spec
from m3_app.api.wire import internalize_request
from m3_app.settings import Settings


def _payload(run_id=None, suite_name=None, project_id=None, project_name=None):
    spec = DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="echo",
                    command=sys.executable,
                    args=("-m", "m3.fixtures.echo_server"),
                )
            ),
        ),
        operation=CallTool(server="echo", name="echo", arguments={"text": "hello"}),
        run_id=run_id,
        suite_name=suite_name,
        project_id=project_id,
        project_name=project_name,
    )
    return {"spec": spec.model_dump(mode="json")}


def _slow_payload(project_id=None):
    # Keep the child alive well beyond the cancellation request so scheduling
    # cannot make this fixture finish before cancellation is exercised.
    spec = DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="echo",
                    command=sys.executable,
                    args=("-c", "import time; time.sleep(300)"),
                ),
            ),
        ),
        operation=ListTools(server="echo"),
        timeout_seconds=300.0,
        project_id=project_id,
    )
    return {"spec": spec.model_dump(mode="json")}


def _agent_spec(harness):
    return AgentSpec(
        harness=harness,
        servers=(
            ServerBinding(
                server=StdioServer(name="echo", command=sys.executable),
            ),
        ),
        message=UserMessage(content=(TextContent(text="only validate this spec"),)),
    )


def _wait_finished(client, execution_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/v2/executions/{execution_id}")
        assert response.status_code == 200
        body = response.json()
        if body["snapshot"]["lifecycle"] == "finished":
            return body
        time.sleep(0.05)
    raise AssertionError("execution did not become terminal")


def _cancel_slow_execution(client, execution_id):
    response = client.post(f"/api/v2/executions/{execution_id}/cancel")
    if response.status_code == 409:
        # Cancellation can outlast the API's settling window on a busy runner.
        # Accept that conflict only if the same execution finishes cancelled.
        assert response.json()["error"]["code"] == "cancellation_conflict"
        body = _wait_finished(client, execution_id)
    else:
        assert response.status_code == 200
        body = response.json()
    assert body["snapshot"]["lifecycle"] == "finished"
    assert body["snapshot"]["outcome"] == "cancelled"
    return body


def test_v2_execution_preserves_colliding_metadata_keys(tmp_path):
    database = Path(tmp_path).resolve() / "metadata-collision.sqlite"
    payload = _payload()
    metadata = {"m3.run_id": "internal-looking", "run_id": "user-owned"}
    payload["spec"]["metadata"] = metadata
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        created = client.post("/api/v2/executions", json=payload)
        assert created.status_code == 202
        body = created.json()
        assert body["spec"]["metadata"] == metadata
        fetched = client.get(f"/api/v2/executions/{body['execution_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["spec"]["metadata"] == metadata


def test_v2_execution_lifecycle_and_reopen(tmp_path):
    database = Path(tmp_path).resolve() / "v2.sqlite"
    application = create_app(Settings(database_path=str(database)))
    with TestClient(application) as client:
        payload = _payload(run_id="api-run", suite_name="catalog")
        created = client.post("/api/v2/executions", json=payload)
        assert created.status_code == 202
        body = created.json()
        assert body["version"] == "v2"
        execution_id = body["execution_id"]
        assert TypeAdapter(ExecutionSpec).validate_python(
            body["spec"]
        ) == DirectSpec.model_validate(payload["spec"])
        assert body["spec"]["run_id"] == "api-run"
        assert body["snapshot"]["run_id"] == "api-run"
        assert body["snapshot"]["lifecycle"] in {
            "created",
            "queued",
            "starting",
            "finished",
        }
        listed = client.get("/api/v2/executions")
        assert (
            TypeAdapter(ExecutionPage).validate_python(listed.json()["page"]).total == 1
        )
        finished = _wait_finished(client, execution_id)
        assert finished["snapshot"]["outcome"] == "completed"
        page = client.get(
            "/api/v2/executions", params={"limit": 1, "outcome": "completed"}
        )
        typed_page = TypeAdapter(ExecutionPage).validate_python(page.json()["page"])
        assert typed_page.total == 1 and len(typed_page.items) == 1
        assert typed_page.items[0].tool_call_count == 1
        fetched = client.get(f"/api/v2/executions/{execution_id}")
        assert fetched.status_code == 200
        assert TypeAdapter(ExecutionSpec).validate_python(
            fetched.json()["spec"]
        ) == TypeAdapter(ExecutionSpec).validate_python(body["spec"])
        assert fetched.json()["snapshot"]["run_id"] == "api-run"
        assert fetched.json()["snapshot"]["tool_call_count"] == 1
        report = client.get(f"/api/v2/executions/{execution_id}/report")
        assert report.status_code == 200
        parsed_report = TypeAdapter(ExecutionReport).validate_python(
            internalize_request("/api/v2/executions/report", report.json()["report"])
        )
        full_trace = TypeAdapter(TraceView).validate_python(
            internalize_request(
                "/api/v2/executions/report", {"trace": report.json()["trace"]}
            )["trace"]
        )
        assert report.json()["report"]["direct_result"]["kind"] == "call_tool"
        assert report.json()["report"]["evidence"]["completeness"] == "partial"
        assert report.json()["trace"]["schema_version"] == "1.1"
        bounded = client.get(
            f"/api/v2/executions/{execution_id}/report", params={"event_limit": 2}
        )
        assert bounded.json()["report"]["events_truncated"] is True
        assert (
            TypeAdapter(TraceView).validate_python(
                internalize_request(
                    "/api/v2/executions/report", {"trace": bounded.json()["trace"]}
                )["trace"]
            )
            == full_trace
        )
        next_cursor = bounded.json()["report"]["next_after_sequence"]
        assert next_cursor == bounded.json()["report"]["events"][-1]["sequence"]
        follow_up = client.get(
            f"/api/v2/executions/{execution_id}/report",
            params={"after_sequence": next_cursor, "event_limit": 2},
        )
        assert all(
            event["sequence"] > next_cursor
            for event in follow_up.json()["report"]["events"]
        )
        assert not (
            {event["sequence"] for event in bounded.json()["report"]["events"]}
            & {event["sequence"] for event in follow_up.json()["report"]["events"]}
        )
        artifact_limited = client.get(
            f"/api/v2/executions/{execution_id}/report", params={"artifact_limit": 1}
        )
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
                provenance=EvaluationSource(
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
        evaluated_parsed = TypeAdapter(ExecutionReport).validate_python(
            internalize_request(
                "/api/v2/executions/report", evaluated_report.json()["report"]
            )
        )
        saved_evaluation = evaluated_report.json()["report"]["evaluations"][0]
        assert saved_evaluation["score"] == 0.91
        assert (
            saved_evaluation["rationale"]
            == "The local echo response matched the request."
        )
        assert saved_evaluation["metrics"] == {"quality": 0.91}
        assert saved_evaluation["provenance"]["provider"] == "test-suite"
        assert saved_evaluation["provenance"]["rubric_id"] == "echo-quality"
        assert saved_evaluation["run_id"] == "api-run"
        assert saved_evaluation["suite_id"] == finished["snapshot"]["suite_id"]
        assert saved_evaluation["suite_name"] == "catalog"
        assert "subject" not in saved_evaluation
        assert "context" not in saved_evaluation
        assert "callback" not in saved_evaluation
        assert "must not be returned" not in str(evaluated_report.json())
    reopened = SQLiteExecutionStore(database)
    try:
        assert reopened.get_snapshot(execution_id) == parsed_report.snapshot
        assert reopened.get_execution_spec(execution_id) == TypeAdapter(
            ExecutionSpec
        ).validate_python(body["spec"])
        assert reopened.get_report(execution_id) == evaluated_parsed
        assert reopened.get_trace_view(execution_id) == full_trace
    finally:
        reopened.close()


def test_v2_execution_report_links_saved_pytest_results(tmp_path):
    database = Path(tmp_path).resolve() / "test-results.sqlite"
    application = create_app(Settings(database_path=str(database)))
    with TestClient(application) as client:
        ids = []
        for _ in range(2):
            created = client.post(
                "/api/v2/executions", json=_payload(run_id="pytest-run")
            )
            assert created.status_code == 202
            execution_id = created.json()["execution_id"]
            _wait_finished(client, execution_id)
            ids.append(execution_id)

        first_url = f"/api/v2/executions/{ids[0]}/report"
        second_url = f"/api/v2/executions/{ids[1]}/report"
        assert client.get(first_url).json()["test_results"] == []

        store = application.state.v2_store
        store.save_test_result(
            "pytest-run",
            "attempt-a",
            {
                "attempt_id": "attempt-a",
                "suite_name": "catalog",
                "node_id": "tests/test_catalog.py::test_a",
                "description": "Checks the catalog entry.",
                "outcome": "failed",
                "phases": {
                    "call": {
                        "outcome": "failed",
                        "exception_type": "builtins.AssertionError",
                    }
                },
                "duration_seconds": 0.42,
                "execution_ids": ids,
            },
        )
        store.save_test_result(
            "pytest-run",
            "attempt-b",
            {
                "attempt_id": "attempt-b",
                "suite_name": "catalog",
                "node_id": "tests/test_catalog.py::test_b",
                "outcome": "passed",
                "duration_seconds": None,
                "execution_ids": [ids[0]],
            },
        )
        store.save_test_result(
            "pytest-run",
            "attempt-unrelated",
            {
                "attempt_id": "attempt-unrelated",
                "suite_name": "catalog",
                "node_id": "tests/test_catalog.py::test_unrelated",
                "description": "Unrelated test.",
                "outcome": "passed",
                "execution_ids": ["another-execution"],
            },
        )

        first = client.get(first_url)
        assert first.status_code == 200
        assert first.json()["report"]["snapshot"]["outcome"] == "completed"
        assert first.json()["test_results"] == [
            {
                "attempt_id": "attempt-a",
                "node_id": "tests/test_catalog.py::test_a",
                "description": "Checks the catalog entry.",
                "outcome": "failed",
                "verdict": "failed_assertion",
                "effective_verdict": "failed",
                "duration_seconds": 0.42,
            },
            {
                "attempt_id": "attempt-b",
                "node_id": "tests/test_catalog.py::test_b",
                "description": "",
                "outcome": "passed",
                "verdict": "passed",
                "effective_verdict": "passed",
                "duration_seconds": None,
            },
        ]
        assert client.get(second_url).json()["test_results"] == [
            first.json()["test_results"][0]
        ]
        assert (
            client.get(first_url, params={"event_limit": 1}).json()["test_results"]
            == first.json()["test_results"]
        )


def test_v2_finalized_direct_report_has_nullable_spec_and_suite_identity(tmp_path):
    from mcp.server.lowlevel import Server
    from mcp.types import ListToolsResult, Tool

    database = Path(tmp_path).resolve() / "direct-suite.sqlite"
    store = SQLiteExecutionStore(database)

    def server_factory():
        async def list_tools(_context, _params):
            return ListToolsResult(
                tools=[Tool(name="lookup", inputSchema={"type": "object"})]
            )

        return Server("catalog", on_list_tools=list_tools)

    with MCPTestKit(store=store, suite_name="catalog") as kit:
        client = kit.direct(InProcessServer(name="catalog", factory=server_factory))
        with client:
            client.list_tools()
        execution_id = client.final_trace.execution_id
    app = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(app) as http:
        response = http.get(f"/api/v2/executions/{execution_id.root}/report")
        assert response.status_code == 200
        body = response.json()
        assert body["spec"] is None
        assert body["report"]["snapshot"]["suite_name"] == "catalog"
        assert body["report"]["snapshot"]["suite_id"] is not None
    store.close()


def test_v2_feedback_reads_manifest_and_optional_baseline(tmp_path):
    database = Path(tmp_path).resolve() / "feedback.sqlite"
    store = SQLiteExecutionStore(database)
    store.save_test_run(
        "baseline-run", {"run_id": "baseline-run", "status": "finished"}
    )
    store.save_test_run("current-run", {"run_id": "current-run", "status": "finished"})
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(application) as client:
        response = client.get(
            "/api/v2/feedback/current-run", params={"baseline_run_id": "baseline-run"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "v2"
        assert body["run_label"] == "Run #2"
        assert body["feedback"]["run_id"] == "current-run"
        assert body["feedback"]["run_label"] == "Run #2"
        assert body["feedback"]["comparison"]["baseline_run_id"] == "baseline-run"
        assert body["feedback"]["comparison"]["baseline_run_label"] == "Run #1"
        assert body["feedback"]["comparison"]["current_run_label"] == "Run #2"
        missing = client.get(
            "/api/v2/feedback/current-run", params={"baseline_run_id": "missing"}
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "feedback_baseline_not_found"
        unknown = client.get("/api/v2/feedback/missing")
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "feedback_not_found"
    store.close()


def test_v2_runs_searches_label_and_technical_id_before_paging(tmp_path):
    database = Path(tmp_path).resolve() / "search.sqlite"
    store = SQLiteExecutionStore(database)
    for number in range(4):
        run_id = f"technical-{number}"
        store.save_test_run(run_id, {"run_id": run_id})
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(application) as client:
        for query, expected in (
            ("run #1", "technical-0"),
            ("TECHNICAL-0", "technical-0"),
        ):
            response = client.get("/api/v2/runs", params={"q": query, "limit": 1})
            assert response.status_code == 200
            assert response.json()["total"] == 1
            assert [run["run_id"] for run in response.json()["runs"]] == [expected]
        grouped = client.get(
            "/api/v2/runs", params={"q": "Run #1", "group": "date", "limit": 1}
        )
        assert grouped.status_code == 200
        assert grouped.json()["total"] == 1
    store.close()


def test_v2_runs_and_feedback_accept_older_injected_store(tmp_path):
    database = Path(tmp_path).resolve() / "older-store.sqlite"
    backing = SQLiteExecutionStore(database)
    backing.save_test_run("older-run", {"run_id": "older-run"})
    backing.save_test_run("older-extra", {"run_id": "older-extra"})
    backing.save_test_run("Café", {"run_id": "Café"})
    backing.create(ExecutionState(execution_id="older-execution", run_id="older-run"))

    class OlderStore:
        def __getattr__(self, name):
            if name == "get_test_run":
                raise AttributeError(name)
            return getattr(backing, name)

        def list_test_run_page(
            self, *, limit=None, offset=0, suite_id=None, project_id=None
        ):
            return backing.list_test_run_page(
                limit=limit, offset=offset, suite_id=suite_id, project_id=project_id
            )

    application = create_app(
        Settings(database_path=str(database)), v2_store=OlderStore()
    )
    with TestClient(application) as client:
        runs = client.get("/api/v2/runs")
        assert runs.status_code == 200
        assert {run["run_id"] for run in runs.json()["runs"]} == {
            "Café",
            "older-run",
            "older-extra",
        }
        for query, expected in (
            ("Run #1", "older-run"),
            ("OLDER-EXTRA", "older-extra"),
            ("CAFÉ", "Café"),
        ):
            found = client.get("/api/v2/runs", params={"q": query, "limit": 1})
            assert found.status_code == 200
            assert found.json()["total"] == 1
            assert [run["run_id"] for run in found.json()["runs"]] == [expected]
        second = client.get(
            "/api/v2/runs", params={"q": "older", "limit": 1, "offset": 1}
        )
        assert second.status_code == 200
        assert second.json()["total"] == 2
        assert [run["run_id"] for run in second.json()["runs"]] == ["older-extra"]
        feedback = client.get("/api/v2/feedback/older-run")
        assert feedback.status_code == 200
        assert feedback.json()["run_label"] is None
    backing.close()


def test_v2_runs_lists_safe_manifests_in_newest_order_including_empty_run(tmp_path):
    database = Path(tmp_path).resolve() / "runs.sqlite"
    store = SQLiteExecutionStore(database)
    store.save_test_run(
        "old-run",
        {
            "run_id": "old-run",
            "created_at": "2026-09-18T10:00:00+00:00",
            "status": "finished",
            "project_root": "/private/project",
            "selection": ["tests/test_secret.py"],
            "capture": {"verbose": 3},
            "collected_node_ids": ["tests/test_old.py::test_one"],
            "test_outcome_counts": {
                "passed": 1,
                "negative": -1,
                "boolean": True,
                "": 9,
            },
            "effective_verdict_counts": {
                "passed": 1,
                "negative": -1,
                "boolean": False,
                "": 9,
            },
        },
    )
    store.save_test_run(
        "empty-run",
        {
            "run_id": "empty-run",
            "created_at": "2026-09-19T10:00:00Z",
            "finished_at": None,
            "status": "finished",
            "project_name": "demo",
            "collection_count": 0,
            "collected_node_ids": [],
        },
    )
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(application) as client:
        response = client.get("/api/v2/runs")
    assert response.status_code == 200
    assert response.json() == {
        "version": "v2",
        "runs": [
            {
                "run_id": "empty-run",
                "run_label": "Run #2",
                "created_at": "2026-09-19T10:00:00Z",
                "finished_at": None,
                "status": "finished",
                "project_id": None,
                "project_name": "demo",
                "test_count": 0,
                "test_outcome_counts": {},
                "effective_verdict_counts": {},
                "suites": [],
            },
            {
                "run_id": "old-run",
                "run_label": "Run #1",
                "created_at": "2026-09-18T10:00:00+00:00",
                "finished_at": None,
                "status": "finished",
                "project_id": None,
                "project_name": None,
                "test_count": 1,
                "test_outcome_counts": {"passed": 1},
                "effective_verdict_counts": {"passed": 1},
                "suites": [],
            },
        ],
        "total": 2,
        "limit": None,
        "offset": 0,
    }
    assert "project_root" not in response.text
    assert "selection" not in response.text
    assert "capture" not in response.text
    store.close()


def test_v2_runs_page_lists_every_suite_and_filters_by_suite(tmp_path):
    database = Path(tmp_path).resolve() / "suites.sqlite"
    store = SQLiteExecutionStore(database)
    for run_id, day, suite_names in (
        ("run-a", "2026-09-18", ("alpha",)),
        ("run-b", "2026-09-19", ("alpha", "beta")),
        ("run-c", "2026-09-20", ()),
    ):
        store.save_test_run(
            run_id, {"run_id": run_id, "created_at": f"{day}T10:00:00+00:00"}
        )
        for index, suite_name in enumerate(suite_names):
            store.save_test_result(
                run_id,
                f"{run_id}-{index}",
                {"node_id": f"test_{index}", "suite_name": suite_name},
            )
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(application) as client:
        suites = client.get("/api/v2/suites").json()["suites"]
        by_name = {item["suite_name"]: item["suite_id"] for item in suites}
        assert set(by_name) == {"alpha", "beta"}

        page = client.get("/api/v2/runs", params={"limit": 2}).json()
        assert [run["run_id"] for run in page["runs"]] == ["run-c", "run-b"]
        assert (page["total"], page["limit"], page["offset"]) == (3, 2, 0)
        assert [s["suite_name"] for s in page["runs"][1]["suites"]] == [
            "alpha",
            "beta",
        ]
        assert page["runs"][0]["suites"] == []

        alpha = client.get("/api/v2/runs", params={"suite_id": by_name["alpha"]})
        assert [run["run_id"] for run in alpha.json()["runs"]] == ["run-b", "run-a"]
        beta = client.get("/api/v2/runs", params={"suite_id": by_name["beta"]})
        assert [run["run_id"] for run in beta.json()["runs"]] == ["run-b"]
        assert client.get("/api/v2/runs", params={"limit": 0}).status_code == 422
    store.close()


@pytest.mark.parametrize("in_memory", [False, True])
def test_v2_runs_groups_selected_page_without_inventing_suite_coverage(
    tmp_path, in_memory
):
    database = tmp_path / "grouped-runs.sqlite"
    store = InMemoryExecutionStore() if in_memory else SQLiteExecutionStore(database)
    project_a = "11111111-1111-4111-8111-111111111111"
    project_b = "22222222-2222-4222-8222-222222222222"
    store.ensure_project(project_a, "Shop")
    store.ensure_project(project_b, "Warehouse")
    for run_id, created_at, project_id, suites in (
        ("first", "2026-09-20T00:30:00+02:00", project_a, ("catalog",)),
        ("second", "2026-09-19T19:00:00Z", project_a, ("catalog", "shipping")),
        ("third", "2026-09-19T18:00:00Z", project_b, ("catalog",)),
        ("unassigned", "bad-date", project_a, ()),
    ):
        store.save_test_run(
            run_id,
            {
                "run_id": run_id,
                "created_at": created_at,
                "project_id": project_id,
                "status": "finished",
                "collected_node_ids": [f"{run_id}-test"],
            },
        )
        for suite_name in suites:
            store.save_test_result(
                run_id,
                f"{run_id}-{suite_name}",
                {
                    "node_id": f"{run_id}-test",
                    "suite_name": suite_name,
                    "project_id": project_id,
                },
            )
        if run_id == "first":
            # A run may contain same-named suites from different projects.
            store.save_test_result(
                run_id,
                "first-catalog-other-project",
                {
                    "node_id": "first-other-test",
                    "suite_name": "catalog",
                    "project_id": project_b,
                },
            )
    catalog_id = store.get_suite_by_name("catalog", project_a).id.root
    other_catalog_id = store.get_suite_by_name("catalog", project_b).id.root
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(application) as client:
        schema = client.get("/openapi.json").json()["paths"]["/api/v2/runs"]["get"]
        assert "group" in {parameter["name"] for parameter in schema["parameters"]}
        plain = client.get("/api/v2/runs").json()
        assert plain["limit"] is None and len(plain["runs"]) == 4
        assert plain["runs"][0]["suites"] == [
            {"suite_id": catalog_id, "suite_name": "catalog", "project_id": project_a},
            {
                "suite_id": other_catalog_id,
                "suite_name": "catalog",
                "project_id": project_b,
            },
        ]
        suite_list = client.get("/api/v2/suites").json()["suites"]
        assert {
            (suite["suite_id"], suite["project_id"])
            for suite in suite_list
            if suite["suite_name"] == "catalog"
        } == {(catalog_id, project_a), (other_catalog_id, project_b)}
        grouped = client.get("/api/v2/runs", params={"group": "suite_name"}).json()
        assert (
            grouped["group"],
            grouped["total"],
            grouped["limit"],
            grouped["offset"],
        ) == (
            "suite_name",
            4,
            50,
            0,
        )
        assert [
            (item["key"], [run["run_id"] for run in item["runs"]], item["run_count"])
            for item in grouped["groups"]
        ] == [
            (
                {"project_id": project_a, "suite_name": "catalog"},
                ["first", "second"],
                2,
            ),
            ({"project_id": project_b, "suite_name": "catalog"}, ["first", "third"], 2),
            ({"project_id": project_a, "suite_name": "shipping"}, ["second"], 1),
            ({"project_id": project_a, "suite_name": None}, ["unassigned"], 1),
        ]
        assert grouped["groups"][0]["runs"][0]["test_count"] == 1
        assert all(
            item["run_count"] == len({run["run_id"] for run in item["runs"]})
            for item in grouped["groups"]
        )
        by_id = client.get("/api/v2/runs", params={"group": "suite_id"}).json()
        assert by_id["groups"][0]["key"] == {
            "suite_id": catalog_id,
            "suite_name": "catalog",
            "project_id": project_a,
        }
        assert by_id["groups"][1]["key"] == {
            "suite_id": other_catalog_id,
            "suite_name": "catalog",
            "project_id": project_b,
        }
        assert by_id["groups"][-1]["key"] == {
            "suite_id": None,
            "suite_name": None,
            "project_id": project_a,
        }
        filtered = client.get(
            "/api/v2/runs",
            params={
                "group": "suite_name",
                "suite_id": catalog_id,
                "project_id": project_a,
            },
        ).json()
        assert filtered["total"] == 2
        assert [item["key"] for item in filtered["groups"]] == [
            {"project_id": project_a, "suite_name": "catalog"},
            {"project_id": project_b, "suite_name": "catalog"},
            {"project_id": project_a, "suite_name": "shipping"},
        ]
        for group, expected in (
            ("date", ["2026-09-19", None]),
            ("month", ["2026-09", None]),
            ("project_id", [project_a, project_b]),
            ("status", ["finished"]),
        ):
            response = client.get("/api/v2/runs", params={"group": group}).json()
            assert [item["key"][group] for item in response["groups"]] == expected
        assert client.get("/api/v2/runs", params={"group": "run_id"}).status_code == 422
    store.close()


def test_v2_suite_group_deduplicates_equal_memberships_within_a_run():
    suite = V2SuiteRef(suite_id=7, suite_name="catalog")
    run = V2RunSummary(run_id="r", suites=(suite, suite))
    for group in ("suite_name", "suite_id"):
        groups = _group_run_page((run,), group)
        assert len(groups) == 1
        assert groups[0].run_count == 1
        assert [item.run_id for item in groups[0].runs] == ["r"]


@pytest.mark.parametrize("in_memory", [False, True])
def test_v2_grouped_runs_limit_and_offset_count_distinct_runs(tmp_path, in_memory):
    database = tmp_path / "grouped-page.sqlite"
    store = InMemoryExecutionStore() if in_memory else SQLiteExecutionStore(database)
    for index in range(51):
        run_id = f"run-{index:02d}"
        store.save_test_run(
            run_id,
            {"run_id": run_id, "created_at": f"2026-09-19T12:{index:02d}:00Z"},
        )
        store.save_test_result(
            run_id,
            run_id,
            {"node_id": run_id, "suite_name": "catalog"},
        )
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(application) as client:
        first = client.get("/api/v2/runs", params={"group": "suite_name"}).json()
        assert (first["total"], first["limit"], first["offset"]) == (51, 50, 0)
        assert first["groups"][0]["run_count"] == 50
        assert first["groups"][0]["runs"][0]["run_id"] == "run-50"
        second = client.get(
            "/api/v2/runs", params={"group": "suite_name", "offset": 50}
        ).json()
        assert (second["total"], second["groups"][0]["run_count"]) == (51, 1)
        assert second["groups"][0]["runs"][0]["run_id"] == "run-00"
        assert (
            client.get(
                "/api/v2/runs", params={"group": "suite_name", "limit": 0}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v2/runs", params={"group": "suite_name", "limit": 101}
            ).status_code
            == 422
        )
        assert (
            client.get("/api/v2/runs", params={"group": "date", "offset": 51}).json()[
                "groups"
            ]
            == []
        )
    store.close()


def test_v2_grouping_preserves_cli_partial_suite_selection(tmp_path):
    test_file = tmp_path / "test_catalog.py"
    test_file.write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.m3(suite_name='catalog')\n"
        "def test_keep(): pass\n"
        "def test_skip_selection(): pass\n",
        encoding="utf-8",
    )
    database = tmp_path / "partial.sqlite"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (
            str(Path(__file__).parents[3] / "sdk" / "src"),
            str(Path(__file__).parents[3] / "cli" / "src"),
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "m3_cli",
            "test",
            "--suite",
            "catalog",
            "--results-db",
            str(database),
            "--",
            "-q",
            "-k",
            "keep",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    application = create_app(Settings(database_path=str(database)))
    with TestClient(application) as client:
        response = client.get("/api/v2/runs", params={"group": "suite_name"}).json()
    assert (response["total"], response["limit"]) == (1, 50)
    assert response["groups"][0]["key"] == {
        "project_id": None,
        "suite_name": "catalog",
    }
    run = response["groups"][0]["runs"][0]
    assert run["test_count"] == 1
    assert len(run["suites"]) == 1
    assert run["suites"][0]["suite_name"] == "catalog"


def test_v2_manifest_not_run_feedback_and_run_list_counts(tmp_path):
    source = """
import pytest
pytestmark = pytest.mark.m3(suite_name="manifest")
def test_first():
    assert False

def test_second():
    assert True
"""
    test_file = tmp_path / "test_manifest.py"
    test_file.write_text(source, encoding="utf-8")
    database = tmp_path / "manifest.sqlite"
    sdk_source = str(Path(__file__).parents[3] / "sdk" / "src")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = (
        sdk_source + os.pathsep + environment.get("PYTHONPATH", "")
    )

    def run(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "m3.pytest_plugin",
                "--results-db",
                str(database),
                "--rootdir",
                str(tmp_path),
                *extra,
                str(test_file),
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    baseline_result = run()
    assert baseline_result.returncode == 1, (
        baseline_result.stdout + baseline_result.stderr
    )
    store = SQLiteExecutionStore(database)
    try:
        baseline_run_id = str(store.list_test_runs()[0]["run_id"])
    finally:
        store.close()

    current_result = run("--maxfail=1", "--baseline", baseline_run_id)
    assert current_result.returncode == 1, current_result.stdout + current_result.stderr

    store = SQLiteExecutionStore(database)
    try:
        run_ids = {str(item["run_id"]) for item in store.list_test_runs()}
        current_run_id = (run_ids - {baseline_run_id}).pop()
        manifest = dict(store.get_test_run(current_run_id))
        rows = store.list_test_results(current_run_id)
        not_run_rows = [
            row for row in rows if row.get("record_source") == "manifest_not_run"
        ]
        assert len(not_run_rows) == 1
        assert not_run_rows[0]["outcome"] == "not_run"
        assert manifest["test_outcome_counts"]["not_run"] == 1
        assert manifest["effective_verdict_counts"]["incomplete"] == 1

        report_path = tmp_path / ".m3" / "reports" / current_run_id / "feedback.json"
        feedback = json.loads(report_path.read_text(encoding="utf-8"))
        not_run_tests = [
            test for test in feedback["tests"] if test["outcome"] == "not_run"
        ]
        assert len(not_run_tests) == 1
        assert not_run_tests[0]["record_source"] == "manifest_not_run"
        assert not_run_tests[0]["effective_verdict"] == "incomplete"
        assert feedback["summary"]["not_run_tests"] == [not_run_tests[0]["node_id"]]
        assert feedback["summary"]["test_outcome_counts"]["not_run"] == 1
        assert feedback["summary"]["effective_verdict_counts"]["incomplete"] == 1

        comparison = feedback["comparison"]
        second_change = next(
            item
            for item in comparison["test_changes"]
            if str(item["node_id"]).endswith("test_manifest.py::test_second")
        )
        assert second_change["baseline"] == [
            {"outcome": "passed", "effective_verdict": "passed"}
        ]
        assert second_change["current"] == [
            {"outcome": "not_run", "effective_verdict": "incomplete"}
        ]
    finally:
        store.close()

    application = create_app(Settings(database_path=str(database)))
    with TestClient(application) as client:
        response = client.get("/api/v2/runs")
    assert response.status_code == 200
    current_summary = next(
        item for item in response.json()["runs"] if item["run_id"] == current_run_id
    )
    assert current_summary["test_outcome_counts"]["not_run"] == 1
    assert current_summary["effective_verdict_counts"]["incomplete"] == 1


def test_v2_feedback_reads_real_two_run_interface_and_score_changes(tmp_path):
    """Exercise pytest plugin -> SQLite -> HTTP feedback without fake rows."""

    source = """
import os
import pytest

from mcp.server.lowlevel import Server
from mcp.types import ListToolsResult, Tool
from m3 import (
    EvaluationDecision,
    EvaluationSource,
    EvaluationStatus,
    InProcessServer,
    MCPTestKit,
    expect,
)

pytestmark = pytest.mark.m3(suite_name="catalog")

def _server():
    async def list_tools(_context, _params):
        return ListToolsResult(tools=[Tool(
            name="find_order",
            description=os.environ["TEST_TOOL_DESCRIPTION"],
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "Customer-visible order identifier",
                    }
                },
                "required": ["order_id"],
            },
        )])
    return Server("orders", on_list_tools=list_tools)


def test_order_tool_catalog():
    score = float(os.environ["TEST_EVAL_SCORE"])
    with MCPTestKit(suite_name="catalog") as kit:
        kit.register_evaluator(
            "project.tool-description-quality.v1",
            lambda _context: EvaluationDecision(
                status=(EvaluationStatus.PASSED if score >= 0.5 else EvaluationStatus.FAILED),
                score=score,
                rationale="deterministic fixture score",
                provenance=EvaluationSource(
                    kind="deterministic",
                    rubric_id="tool-description-quality",
                    rubric_version="1",
                ),
            ),
        )
        client = kit.direct(InProcessServer(name="orders", factory=_server))
        with client:
            tools = client.list_tools()
            assert tools.tools[0].name == "find_order"
        expect(client.final_trace).to_have_trace()
        kit.evaluate(client.final_trace, "project.tool-description-quality.v1")
"""
    test_file = tmp_path / "test_tool_feedback.py"
    test_file.write_text(source, encoding="utf-8")
    database = tmp_path / "feedback-flow.sqlite"
    sdk_source = str(Path(__file__).parents[3] / "sdk" / "src")

    def run(description, score, baseline=None):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = (
            sdk_source + os.pathsep + environment.get("PYTHONPATH", "")
        )
        environment["TEST_TOOL_DESCRIPTION"] = description
        environment["TEST_EVAL_SCORE"] = str(score)
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--results-db",
            str(database),
            "--rootdir",
            str(tmp_path),
        ]
        if baseline is not None:
            command.extend(("--baseline", baseline))
        command.append(str(test_file))
        return subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    first = run("Find an order", 0.3)
    assert first.returncode == 0, first.stdout + first.stderr
    store = SQLiteExecutionStore(database)
    try:
        baseline_run_id = str(store.list_test_runs()[0]["run_id"])
    finally:
        store.close()

    second = run(
        "Find an order by its customer-visible order ID.",
        0.9,
        baseline_run_id,
    )
    assert second.returncode == 0, second.stdout + second.stderr

    store = SQLiteExecutionStore(database)
    run_ids = {str(item["run_id"]) for item in store.list_test_runs()}
    current_run_id = (run_ids - {baseline_run_id}).pop()
    application = create_app(Settings(database_path=str(database)), v2_store=store)
    with TestClient(application) as client:
        current = client.get(f"/api/v2/feedback/{current_run_id}")
        assert current.status_code == 200
        assert current.json()["feedback"]["comparison"] is None

        response = client.get(
            f"/api/v2/feedback/{current_run_id}",
            params={"baseline_run_id": baseline_run_id},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "v2"
        feedback = body["feedback"]
    store.close()

    assert feedback["run_id"] == current_run_id
    assert feedback["suites"] == [
        {"suite_id": feedback["executions"][0]["suite_id"], "suite_name": "catalog"}
    ]
    assert feedback["executions"][0]["suite_name"] == "catalog"
    assert feedback["tests"][0]["suite_name"] == "catalog"
    assert feedback["summary"] == {
        "executions": 1,
        "tests": 1,
        "failures": 0,
        "passed_tests": 1,
        "failed_tests": 0,
        "error_tests": 0,
        "skipped_tests": 0,
        "test_outcome_counts": {
            "passed": 1,
            "failed": 0,
            "error": 0,
            "skipped": 0,
            "not_run": 0,
        },
        "effective_verdict_counts": {
            "pending": 0,
            "passed": 1,
            "failed": 0,
            "incomplete": 0,
            "skipped": 0,
        },
        "not_run_tests": [],
        "collection_errors": 0,
        "terminal_executions": 1,
        "run_status": "finished",
    }
    assert feedback["tests"][0]["execution_ids"] == [
        feedback["executions"][0]["execution_id"]
    ]
    comparison = feedback["comparison"]
    assert comparison["baseline_suites"] == feedback["suites"]
    assert comparison["current_suites"] == feedback["suites"]
    assert comparison["baseline_run_id"] == baseline_run_id
    assert comparison["current_run_id"] == current_run_id
    assert comparison["test_changes"] == []
    assert comparison["coverage"] == {
        "baseline_executions": 1,
        "current_executions": 1,
        "baseline_tests": 1,
        "current_tests": 1,
    }

    interface_change = next(
        item
        for item in comparison["interface_changes"]
        if item.get("tool") == "find_order"
    )
    assert interface_change["server"] == "orders"
    assert interface_change["before"]["description"] == "Find an order"
    assert interface_change["after"]["description"] == (
        "Find an order by its customer-visible order ID."
    )
    assert (
        interface_change["before"]["inputSchema"]
        == interface_change["after"]["inputSchema"]
    )

    evaluation_change = next(
        item
        for item in comparison["evaluation_changes"]
        if item.get("evaluator") == "project.tool-description-quality.v1"
    )
    assert evaluation_change["configuration_label_before"] == ["direct"]
    assert evaluation_change["configuration_label_after"] == ["direct"]
    assert evaluation_change["comparable"] is True
    assert evaluation_change["before"]["stats"]["average_score"] == 0.3
    assert evaluation_change["after"]["stats"]["average_score"] == 0.9
    assert evaluation_change["delta"]["average_score"] == 0.6
    assert evaluation_change["delta"]["pass_rate"] == 1.0


def test_v2_feedback_distinguishes_pytest_and_evaluation_evidence(tmp_path):
    """Persist and serve the three independent evidence combinations."""
    source = """
import os
import sys
from pathlib import Path

import pytest

from m3 import (
    ACPAgent,
    EvaluationDecision,
    EvaluationStatus,
    FullToolPolicy,
    MCPTestKit,
    ServerBinding,
    StdioServer,
    TextContent,
    UserMessage,
)
from m3._types.specs import AgentSpec

pytestmark = pytest.mark.m3(suite_name="blind-evidence")
_PROMPT = "Inspect the order summary and return a concise response."


def _trace_view(context):
    if context.trace is None:
        return None
    return context.trace.view()


def _contains_order_summary(block):
    text = getattr(block, "text", None)
    return (
        isinstance(text, str)
        and bool(text.strip())
        and "order summary" in text.lower()
    )


def _expected_content(context):
    view = _trace_view(context)
    found = bool(
        view
        and any(
            _contains_order_summary(block)
            for message in view.messages
            if message.role.value == "assistant"
            for block in message.content
        )
    )
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if found else EvaluationStatus.FAILED,
        rationale="checked assistant response content for the order summary",
        details={"evidence_basis": "trace.messages.assistant_text"},
    )


def _successful_tool_call(context):
    view = _trace_view(context)
    found = bool(
        view
        and any(
            call.tool.value == "echo"
            and call.tool_status.value == "success"
            and call.result.value is not None
            and not call.result.value.is_error
            and any(
                _contains_order_summary(block)
                for block in call.result.value.content
            )
            for call in view.tool_calls
        )
    )
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if found else EvaluationStatus.FAILED,
        rationale="checked the successful echo result for the order summary",
        details={"evidence_basis": "trace.tool_calls.echo.successful_result"},
    )


def _run_evaluations(names):
    fixtures = Path(os.environ["M3_FIXTURES"])
    repository_root = Path(os.environ["M3_REPOSITORY_ROOT"])
    spec = AgentSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={
                "schema_version": "m3.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(fixtures / "acp_scenario_agent.py"), "normal"],
                "env": {},
            },
        ),
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="e2e-mcp",
                    command=sys.executable,
                    args=(str(fixtures / "matrix_stdio_server.py"),),
                    cwd=str(repository_root),
                ),
                alias="e2e-mcp",
            ),
        ),
        tool_policy=FullToolPolicy(acknowledge_risk=True),
        message=UserMessage(content=(TextContent(text=_PROMPT),)),
    )
    with MCPTestKit(suite_name="blind-evidence", cwd=str(repository_root)) as kit:
        evaluators = {
            "response.expected_content.v1": _expected_content,
            "tool_call.succeeded.v1": _successful_tool_call,
        }
        for name in names:
            kit.register_evaluator(name, evaluators[name])
        result = kit.run(spec)
        trace = result.trace
        if trace is None:
            raise RuntimeError("agent execution did not produce trace evidence")
        for name in names:
            kit.evaluate(
                trace,
                name,
                required=True,
                trace=trace,
                execution_id=result.snapshot.execution_id,
            )


def test_pytest_only():
    assert 2 + 2 == 4


def test_evaluation_only():
    _run_evaluations(("response.expected_content.v1",))


def test_combined():
    _run_evaluations(
        ("response.expected_content.v1", "tool_call.succeeded.v1")
    )
    assert False
"""
    test_file = tmp_path / "test_blind_evidence.py"
    test_file.write_text(source, encoding="utf-8")
    database = tmp_path / "blind-evidence.sqlite"
    (tmp_path / "m3.toml").write_text(
        'schema_version = 1\nproject_id = "11111111-1111-4111-8111-111111111111"\nproject_name = "Blind Evidence"\n',
        encoding="utf-8",
    )
    sdk_source = str(Path(__file__).parents[3] / "sdk" / "src")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = (
        sdk_source + os.pathsep + environment.get("PYTHONPATH", "")
    )
    environment["M3_FIXTURES"] = str(
        Path(__file__).parents[3] / "sdk" / "tests" / "fixtures"
    )
    environment["M3_REPOSITORY_ROOT"] = str(Path(__file__).parents[3])
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "m3.pytest_plugin",
            "--results-db",
            str(database),
            "--rootdir",
            str(tmp_path),
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed, 2 passed" in result.stdout

    store = SQLiteExecutionStore(database)
    try:
        run_id = str(store.list_test_runs()[0]["run_id"])
        attempts = {
            str(row["node_id"]).rsplit("::", 1)[-1]: row
            for row in store.list_test_results(run_id)
        }
        assert set(attempts) == {
            "test_pytest_only",
            "test_evaluation_only",
            "test_combined",
        }
        assert attempts["test_pytest_only"]["execution_ids"] == []
        assert len(attempts["test_evaluation_only"]["execution_ids"]) == 1
        assert len(attempts["test_combined"]["execution_ids"]) == 1
        for name in ("test_evaluation_only", "test_combined"):
            execution_id = attempts[name]["execution_ids"][0]
            trace = store.get_trace_view(execution_id)
            assert trace.tool_calls
            assert {call.tool.value for call in trace.tool_calls} == {"echo"}
            assert all(call.tool_status.value == "success" for call in trace.tool_calls)
            assert all(call.result.value is not None for call in trace.tool_calls)
            evaluation_rows = store.evaluations(execution_id)
            expected_names = (
                ("response.expected_content.v1",)
                if name == "test_evaluation_only"
                else ("response.expected_content.v1", "tool_call.succeeded.v1")
            )
            assert [row.name for row in evaluation_rows] == list(expected_names)
            assert all(row.status is EvaluationStatus.PASSED for row in evaluation_rows)
            assert all(row.required for row in evaluation_rows)

        application = create_app(Settings(database_path=str(database)), v2_store=store)
        with TestClient(application) as client:
            response = client.get(f"/api/v2/feedback/{run_id}")
            aggregate_response = client.post(
                "/api/v2/evaluations/aggregate",
                json={
                    "group_by": ["evaluator"],
                    "filters": {"run_id": [run_id]},
                },
            )
        assert response.status_code == 200
        assert aggregate_response.status_code == 200
        feedback = response.json()["feedback"]
        aggregate = aggregate_response.json()["aggregate"]
    finally:
        store.close()

    tests = {
        str(test["node_id"]).rsplit("::", 1)[-1]: test for test in feedback["tests"]
    }
    assert tests["test_pytest_only"]["outcome"] == "passed"
    assert tests["test_pytest_only"]["execution_ids"] == []
    assert tests["test_pytest_only"]["evaluations"] == []
    assert tests["test_pytest_only"]["tool_calls"] == {
        "total": 0,
        "successful": 0,
        "failed": 0,
    }
    assert tests["test_evaluation_only"]["outcome"] == "passed"
    assert tests["test_evaluation_only"]["effective_verdict"] == "passed"
    assert len(tests["test_evaluation_only"]["execution_ids"]) == 1
    evaluation_only = tests["test_evaluation_only"]["evaluations"]
    assert [item["evaluator"] for item in evaluation_only] == [
        "response.expected_content.v1"
    ]
    assert [item["status"] for item in evaluation_only] == ["passed"]
    assert all(item["evaluation_id"] for item in evaluation_only)
    assert tests["test_evaluation_only"]["tool_calls"]["total"] > 0
    assert (
        tests["test_evaluation_only"]["tool_calls"]["successful"]
        == tests["test_evaluation_only"]["tool_calls"]["total"]
    )
    assert tests["test_evaluation_only"]["tool_calls"]["failed"] == 0
    assert evaluation_only[0]["details"] == {
        "evidence_basis": "trace.messages.assistant_text"
    }
    assert tests["test_combined"]["outcome"] == "failed"
    assert tests["test_combined"]["effective_verdict"] == "failed"
    assert len(tests["test_combined"]["execution_ids"]) == 1
    combined = tests["test_combined"]["evaluations"]
    assert {item["evaluator"] for item in combined} == {
        "response.expected_content.v1",
        "tool_call.succeeded.v1",
    }
    assert {item["status"] for item in combined} == {"passed"}
    assert len({item["evaluation_id"] for item in combined}) == 2
    assert tests["test_combined"]["tool_calls"]["total"] > 0
    assert tests["test_combined"]["tool_calls"]["failed"] == 0
    assert {item["details"]["evidence_basis"] for item in combined} == {
        "trace.messages.assistant_text",
        "trace.tool_calls.echo.successful_result",
    }
    aggregate_groups = {
        group["key"]["evaluator"]: group["values"] for group in aggregate["groups"]
    }
    assert set(aggregate_groups) == {
        "response.expected_content.v1",
        "tool_call.succeeded.v1",
    }
    assert all(
        values["health"]["tool_calls"]["total"] > 0
        for values in aggregate_groups.values()
    )
    assert feedback["summary"]["tests"] == 3
    assert feedback["summary"]["executions"] == 2


def test_v2_errors_and_deletion_constraints(tmp_path):
    database = Path(tmp_path).resolve() / "errors.sqlite"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        missing = client.get("/api/v2/executions/no-such")
        assert missing.status_code == 404
        assert missing.json()["version"] == "v2"
        assert missing.json()["error"]["code"] == "execution_not_found"
        unsafe = client.post(
            "/api/v2/executions", json={"spec": {"pickle": "arbitrary"}}
        )
        assert unsafe.status_code == 422
        assert unsafe.json()["error"]["code"] == "invalid_execution_spec"
        created = client.post("/api/v2/executions", json=_slow_payload()).json()
        execution_id = created["execution_id"]
        blocked = client.delete(f"/api/v2/executions/{execution_id}")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "execution_active"
        cancelled = _cancel_slow_execution(client, execution_id)
        cancelled_spec = TypeAdapter(ExecutionSpec).validate_python(cancelled["spec"])
        assert isinstance(cancelled_spec, DirectSpec)
        cancelled_report = client.get(f"/api/v2/executions/{execution_id}/report")
        assert cancelled_report.status_code == 200
        assert (
            TypeAdapter(TraceView)
            .validate_python(
                internalize_request(
                    "/api/v2/executions/report",
                    {"trace": cancelled_report.json()["trace"]},
                )["trace"]
            )
            .outcome
            == "cancelled"
        )
        terminal_cancel = client.post(f"/api/v2/executions/{execution_id}/cancel")
        assert terminal_cancel.status_code == 409
        deleted = client.delete(f"/api/v2/executions/{execution_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {
            "version": "v2",
            "execution_id": execution_id,
            "deleted": True,
        }
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
        unsafe_spec["servers"][0]["server"] = {
            "name": "runtime",
            "kind": "in_process",
            "factory": {"module": "not-callable"},
        }
        unsafe = client.post("/api/v2/executions", json={"spec": unsafe_spec})
        assert unsafe.status_code == 422
        assert unsafe.json()["error"]["code"] == "invalid_execution_spec"


def test_v2_accepts_every_serializable_execution_spec_variant(tmp_path):
    """The HTTP boundary accepts SDK JSON, without starting external harnesses."""

    database = Path(tmp_path).resolve() / "spec-variants.sqlite"
    store = SQLiteExecutionStore(database)
    kit = MCPTestKit(store=store, embedded_worker=False)
    specs = (
        DirectSpec.model_validate(_payload()["spec"]),
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
                    "/api/v2/executions",
                    json={"spec": submitted.model_dump(mode="json")},
                )
                assert response.status_code == 202
                response_spec = TypeAdapter(ExecutionSpec).validate_python(
                    internalize_request("/api/v2/executions", response.json()["spec"])
                )
                assert response_spec == submitted
                assert (
                    store.get_execution_spec(response.json()["execution_id"])
                    == submitted
                )
    finally:
        kit.close()
        store.close()


def test_v2_evaluation_aggregate_endpoint(tmp_path):
    database = Path(tmp_path).resolve() / "aggregate-api.sqlite"
    store = SQLiteExecutionStore(database)
    kit = MCPTestKit(store=store, embedded_worker=False)
    try:
        execution_id = "aggregate-execution"
        store.create(ExecutionState(execution_id=execution_id, run_id="aggregate-run"))
        store.save_evaluation(
            execution_id,
            EvaluationResult(
                evaluation_id=EvaluationId("aggregate-evaluation"),
                name="quality.v1",
                status=EvaluationStatus.PASSED,
                context={"execution_id": execution_id, "case_id": "case-1"},
            ),
        )
        app = create_app(
            Settings(database_path=str(Path(tmp_path).resolve() / "unused.sqlite")),
            v2_store=store,
            v2_kit=kit,
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/v2/evaluations/aggregate",
                json={
                    "group_by": ["run_id", "case_id", "evaluator"],
                    "filters": {"evaluator": "quality.v1"},
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["version"] == "v2"
            assert body["aggregate"]["groups"][0]["values"]["pass_rate"] == 1
            assert body["aggregate"]["groups"][0]["key"]["case_id"] == "case-1"
    finally:
        kit.close()
        store.close()


def test_v2_evaluation_aggregate_validation_and_openapi(tmp_path):
    database = Path(tmp_path).resolve() / "aggregate-validation.sqlite"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        invalid = client.post(
            "/api/v2/evaluations/aggregate", json={"group_by": ["not-a-label"]}
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "invalid_evaluation_aggregate_query"
        for payload in (
            {"group_by": ["run_id"]},
            {
                "group_by": ["run_id"],
                "filters": {"evaluator": ["quality.v1", "judge.v1"]},
            },
        ):
            mixed = client.post("/api/v2/evaluations/aggregate", json=payload)
            assert mixed.status_code == 422
            assert mixed.json()["error"]["code"] == "invalid_evaluation_aggregate_query"
        schema = client.get("/openapi.json").json()
        operation = schema["paths"]["/api/v2/evaluations/aggregate"]["post"]
        assert operation["responses"]["200"]["content"]["application/json"]
        assert "EvaluationReport" in schema["components"]["schemas"]
        stats = schema["components"]["schemas"]["EvaluationStats"]
        properties = stats["properties"]
        assert "expected_count" in properties
        assert "missing_required_count" in properties
        assert "pending_required_count" in properties
        assert "measured_count" not in properties
        report_fields = schema["components"]["schemas"]["V2TestResultSummary"][
            "properties"
        ]
        assert "verdict" in report_fields
        assert "effective_verdict" in report_fields


def test_v2_capability_doc_lists_every_route(tmp_path):
    source = Path(__file__).parents[2] / "docs" / "api-v2.md"
    text = source.read_text()
    import re

    documented = set(re.findall(r"`(GET|POST|DELETE|PUT|PATCH) (/api/v2/[^`]+)`", text))
    with TestClient(
        create_app(Settings(database_path=str(tmp_path / "unused-doc-test.sqlite")))
    ) as client:
        schema = client.get("/openapi.json").json()
    actual = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        if path.startswith("/api/v2/")
        for method in operations
        if method.upper() in {"GET", "POST", "DELETE", "PUT", "PATCH"}
    }
    assert actual <= documented
    assert documented <= actual
    prose = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    backticked = set(re.findall(r"`([^`]+)`", prose))
    assert {
        "direct",
        "agent",
        "ExecutionState",
        "events",
        "next_after_sequence",
        "evaluations",
        "TraceView",
        "Observation",
        "max_bytes",
        "group_by",
        "filters",
        "health",
    } <= backticked


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


def test_v2_rejects_unsafe_spec_shapes_and_invalid_filters_without_echoing_values(
    tmp_path,
):
    database = Path(tmp_path).resolve() / "unsafe-shapes.sqlite"
    canary = "v2-unsafe-shape-secret"
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        base = _payload()["spec"]
        unsafe_cases = (
            {"spec": {**base, "kind": "unknown-kind"}},
            {"spec": {"kind": "agent", "servers": base["servers"]}},
            {
                "spec": {
                    **base,
                    "servers": [
                        {
                            "server": {
                                "kind": "in_process",
                                "name": "runtime",
                                "factory": {"pickle": canary},
                            }
                        }
                    ],
                }
            },
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


def test_v2_service_failures_keep_stable_http_errors_and_hide_store_text(
    tmp_path, monkeypatch
):
    database = Path(tmp_path).resolve() / "service-failures.sqlite"
    store = SQLiteExecutionStore(database)
    kit = MCPTestKit(store=store, embedded_worker=False)
    canary = "underlying-store-secret"
    try:
        app = create_app(
            Settings(database_path=str(Path(tmp_path).resolve() / "unused.sqlite")),
            v2_store=store,
            v2_kit=kit,
        )
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
            reference = EvidenceRef(evidence_id="synthetic-evidence")
            for failure, status_code, code in (
                (RawEvidenceUnavailable(canary), 404, "raw_evidence_not_found"),
                (
                    RawEvidenceIntegrityError(canary),
                    500,
                    "raw_evidence_integrity_error",
                ),
            ):
                monkeypatch.setattr(
                    store,
                    "read_raw_evidence",
                    lambda _reference, *, max_bytes, failure=failure: (
                        _ for _ in ()
                    ).throw(failure),
                )
                response = client.post(
                    "/api/v2/evidence/read",
                    json={"reference": reference.model_dump(mode="json")},
                )
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
        execution_id = client.post("/api/v2/executions", json=_payload()).json()[
            "execution_id"
        ]
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
    app = create_app(
        Settings(database_path=str(Path(tmp_path).resolve() / "openapi.sqlite"))
    )
    document = app.openapi()
    schemas = document["components"]["schemas"]
    spec_schema = schemas["V2ExecutionCreate"]["properties"]["spec"]
    assert set(spec_schema["discriminator"]["mapping"]) == {"direct", "agent"}
    assert "oneOf" in spec_schema and spec_schema.get("type") != "object"
    report_schema = schemas["V2ExecutionReportEnvelope"]["properties"]
    assert report_schema["report"]["$ref"].endswith("/ExecutionReport")
    assert report_schema["trace"]["$ref"].endswith("/TraceView")
    trace_schema = schemas["TraceView"]["properties"]
    assert trace_schema["runtime"]["discriminator"]["propertyName"] == "kind"
    assert trace_schema["timeline"]["items"]["discriminator"]["mapping"][
        "transport"
    ].endswith("/TransportEntry")
    assert schemas["V2EvidenceRead"]["properties"]["reference"]["$ref"].endswith(
        "/EvidenceRef"
    )
    assert {"reference", "content", "truncated", "redacted"} <= set(
        schemas["RawEvidence"]["properties"]
    )

    def visit(value):
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith(
                "#/components/schemas/"
            ):
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
        app = create_app(
            Settings(database_path=str(Path(tmp_path).resolve() / "unused.sqlite")),
            v2_store=store,
            v2_kit=kit,
        )
        assert app.state.v2_store is store
        assert app.state.v2_kit is kit
        with TestClient(app) as client:
            assert client.get("/api/v2/executions").json()["page"]["total"] == 0
    finally:
        kit.close()
        store.close()


def test_v2_module_has_no_legacy_orm_or_run_manager_imports():
    source = Path(__file__).parents[2] / "src/m3_app/api/v2.py"
    text = source.read_text()
    assert "RunManager" not in text
    assert "m3_app.persistence" not in text
    assert "v2_executions" not in text


def test_v2_project_name_is_returned_on_execution_envelopes(tmp_path):
    project_id = "11111111-1111-4111-8111-111111111111"
    database = Path(tmp_path).resolve() / "project-api.sqlite"
    store = SQLiteExecutionStore(database)
    store.ensure_project(project_id, "Orders")
    kit = MCPTestKit(store=store, embedded_worker=False)
    app = create_app(
        Settings(database_path=str(tmp_path / "unused.sqlite")),
        v2_store=store,
        v2_kit=kit,
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v2/executions",
            json=_payload(run_id="project-api-run", project_id=project_id),
        )
        assert created.status_code == 202
        execution_id = created.json()["execution_id"]
        assert created.json()["project_name"] == "Orders"
        assert (
            client.get(f"/api/v2/executions/{execution_id}").json()["project_name"]
            == "Orders"
        )
        slow = client.post(
            "/api/v2/executions",
            json=_slow_payload(project_id=project_id),
        )
        assert slow.status_code == 202
        cancelled = _cancel_slow_execution(client, slow.json()["execution_id"])
        assert cancelled["project_name"] == "Orders"
    kit.close()
    store.close()


def test_v2_create_registers_project_on_fresh_store(tmp_path):
    project_id = "55555555-5555-4555-8555-555555555555"
    store = SQLiteExecutionStore(Path(tmp_path) / "fresh-project-api.sqlite")
    kit = MCPTestKit(store=store, embedded_worker=False)
    app = create_app(
        Settings(database_path=str(tmp_path / "unused.sqlite")),
        v2_store=store,
        v2_kit=kit,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/executions",
            json=_payload(
                run_id="fresh-project-api-run",
                project_id=project_id,
                project_name="Fresh Orders",
            ),
        )
        assert response.status_code == 202
        assert response.json()["project_name"] == "Fresh Orders"
    assert store.get_project(project_id) == (project_id, "Fresh Orders")
    kit.close()
    store.close()


_REPLAY_FIXTURES = Path(__file__).parents[3] / "sdk" / "tests" / "fixtures"
_REPLAY_ROOT = str(Path(__file__).parents[3])


def _record_pytest_agent_tool_call(store, environment):
    """Record an agent execution shaped like a pytest test-run attempt."""
    spec = AgentSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={
                "schema_version": "m3.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(_REPLAY_FIXTURES / "acp_scenario_agent.py"), "normal"],
                "env": {},
            },
        ),
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="e2e-mcp",
                    command=sys.executable,
                    args=(str(_REPLAY_FIXTURES / "matrix_stdio_server.py"),),
                    cwd=_REPLAY_ROOT,
                    environment=environment,
                ),
                alias="e2e-mcp",
            ),
        ),
        tool_policy=FullToolPolicy(acknowledge_risk=True),
        message=UserMessage(content=(TextContent(text="hello replay"),)),
        run_id="pytest-replay-run",
        case_id="tests/test_orders.py::test_lookup",
        suite_name="orders",
    )
    with MCPTestKit(store=store) as kit:
        result = kit.run(spec)
    execution_id = result.snapshot.execution_id.root
    trace = store.get_trace_view(execution_id)
    tool_entry = trace.tool_calls[0].entry_id
    other_entry = next(
        item.entry_id for item in trace.timeline if item.kind != "tool_call"
    )
    return execution_id, tool_entry, other_entry


def _assert_replay_spec_is_safe(text):
    for private in (
        sys.executable,
        _REPLAY_ROOT,
        "matrix_stdio_server.py",
        "literal-secret-value",
        "replay-token-value",
    ):
        assert private not in text


def test_v2_replays_pytest_agent_tool_call_as_direct_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAY_TOKEN", "replay-token-value")
    database = Path(tmp_path).resolve() / "replay.sqlite"
    store = SQLiteExecutionStore(database)
    source_id, entry_id, _other = _record_pytest_agent_tool_call(
        store, {"REPLAY_LITERAL": "literal-secret-value"}
    )
    app = create_app(Settings(database_path=str(database)), v2_store=store)
    url = f"/api/v2/executions/{source_id}/tool-calls/{entry_id}/replay"
    with TestClient(app) as client:
        assert client.get("/api/v2/capabilities").json()["tool_call_replay"] is True

        replayed = client.post(url)
        assert replayed.status_code == 202, replayed.text
        body = replayed.json()
        _assert_replay_spec_is_safe(replayed.text)
        replay_id = body["execution_id"]
        assert replay_id != source_id
        # The app kit scopes its own run; the replay never joins the test run.
        assert body["snapshot"]["run_id"] != "pytest-replay-run"
        assert body["snapshot"]["suite_id"] is None
        assert body["spec"]["kind"] == "direct"
        assert body["spec"]["run_id"] is None
        assert body["spec"]["case_id"] is None
        assert body["spec"]["suite_name"] is None
        assert body["spec"]["metadata"] == {
            "replayed_from.execution_id": source_id,
            "replayed_from.entry_id": entry_id,
        }
        assert body["spec"]["operation"] == {
            "kind": "call_tool",
            "server": "e2e-mcp",
            "name": "echo",
            "arguments": {"text": "hello replay"},
        }
        assert body["spec"]["servers"][0]["server"]["environment"] == {
            "REPLAY_LITERAL": "redacted"
        }

        finished = _wait_finished(client, replay_id)
        _assert_replay_spec_is_safe(client.get(f"/api/v2/executions/{replay_id}").text)
        assert finished["snapshot"]["outcome"] == "completed"
        report = client.get(f"/api/v2/executions/{replay_id}/report").json()
        _assert_replay_spec_is_safe(json.dumps(report["spec"]))
        calls = [
            item for item in report["trace"]["timeline"] if item["kind"] == "tool_call"
        ]
        assert calls[0]["arguments"]["value"] == {"text": "hello replay"}
        listed = client.get("/api/v2/executions").json()["page"]["items"]
        assert {item["execution_id"] for item in listed} == {source_id, replay_id}

        overridden = client.post(url, json={"arguments": {"text": "override"}})
        assert overridden.status_code == 202, overridden.text
        assert overridden.json()["spec"]["operation"]["arguments"] == {
            "text": "override"
        }
        override_id = overridden.json()["execution_id"]
        _wait_finished(client, override_id)
        override_report = client.get(f"/api/v2/executions/{override_id}/report").json()
        override_calls = [
            item
            for item in override_report["trace"]["timeline"]
            if item["kind"] == "tool_call"
        ]
        assert override_calls[0]["arguments"]["value"] == {"text": "override"}
    store.close()


def test_v2_tool_call_replay_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAY_TOKEN", "replay-token-value")
    database = Path(tmp_path).resolve() / "replay-errors.sqlite"
    store = SQLiteExecutionStore(database)
    reference = SecretReference(source="environment", name="REPLAY_TOKEN")
    source_id, entry_id, other_entry = _record_pytest_agent_tool_call(
        store, {"REPLAY_TOKEN": reference}
    )
    app = create_app(Settings(database_path=str(database)), v2_store=store)

    def replay(execution_id, entry, **kwargs):
        return client.post(
            f"/api/v2/executions/{execution_id}/tool-calls/{entry}/replay", **kwargs
        )

    with TestClient(app) as client:
        # References are copied as references and resolved only at run time.
        accepted = replay(source_id, entry_id)
        assert accepted.status_code == 202, accepted.text
        _assert_replay_spec_is_safe(accepted.text)
        assert accepted.json()["spec"]["servers"][0]["server"]["environment"] == {
            "REPLAY_TOKEN": {"source": "environment", "name": "REPLAY_TOKEN"}
        }
        stored = store.get_execution_spec(accepted.json()["execution_id"])
        assert stored.servers[0].server.environment == {"REPLAY_TOKEN": reference}
        reference_id = accepted.json()["execution_id"]
        finished = _wait_finished(client, reference_id)
        assert finished["snapshot"]["outcome"] == "completed", finished
        assert finished["snapshot"]["tool_call_count"] == 1
        # The resolved value is bound for redaction, never persisted.
        report = client.get(f"/api/v2/executions/{reference_id}/report")
        assert "replay-token-value" not in report.text
        assert all(
            "replay-token-value" not in event.model_dump_json()
            for event in store.iter_events(reference_id)
        )

        not_tool = replay(source_id, other_entry)
        assert not_tool.status_code == 409
        assert not_tool.json()["error"]["code"] == "tool_call_not_replayable"

        missing_entry = replay(source_id, "entry:missing")
        assert missing_entry.status_code == 404
        assert missing_entry.json()["error"]["code"] == "tool_call_not_found"

        missing_execution = replay("execution-missing", entry_id)
        assert missing_execution.status_code == 404
        assert missing_execution.json()["error"]["code"] == "execution_not_found"

        bad_body = replay(source_id, entry_id, json={"arguments": ["not", "object"]})
        assert bad_body.status_code == 422

        def legacy_spec(_execution_id):
            raise StorageError("persisted execution specification is invalid")

        monkeypatch.setattr(store, "get_execution_spec", legacy_spec)
        legacy = replay(source_id, entry_id)
        assert legacy.status_code == 422
        assert legacy.json()["error"]["code"] == "replay_source_unavailable"
    store.close()


def test_v2_tool_call_replay_requires_override_for_redacted_arguments(
    tmp_path, monkeypatch
):
    # A credential-named variable holding the argument text makes the
    # recorder store the call as {"text": "[REDACTED]"}.
    monkeypatch.setenv("REPLAY_TOKEN", "hello replay")
    database = Path(tmp_path).resolve() / "replay-redacted.sqlite"
    store = SQLiteExecutionStore(database)
    source_id, entry_id, _other = _record_pytest_agent_tool_call(store, {})
    recorded = next(
        item
        for item in store.get_trace_view(source_id).tool_calls
        if item.entry_id == entry_id
    )
    assert recorded.arguments.value == {"text": "[REDACTED]"}
    app = create_app(Settings(database_path=str(database)), v2_store=store)
    url = f"/api/v2/executions/{source_id}/tool-calls/{entry_id}/replay"
    with TestClient(app) as client:
        rejected = client.post(url)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "tool_call_not_replayable"
        # Nothing was submitted, so the placeholder never reached a server.
        listed = client.get("/api/v2/executions").json()["page"]["items"]
        assert [item["execution_id"] for item in listed] == [source_id]

        overridden = client.post(url, json={"arguments": {"text": "explicit"}})
        assert overridden.status_code == 202, overridden.text
        assert overridden.json()["spec"]["operation"]["arguments"] == {
            "text": "explicit"
        }
        _wait_finished(client, overridden.json()["execution_id"])
    store.close()


def test_v2_tool_call_replay_rejects_redacted_tool_name_even_with_override(
    tmp_path, monkeypatch
):
    # A configured secret equal to the tool name records it as "[REDACTED]".
    monkeypatch.setenv("REPLAY_TOKEN", "echo")
    database = Path(tmp_path).resolve() / "replay-redacted-tool.sqlite"
    store = SQLiteExecutionStore(database)
    source_id, entry_id, _other = _record_pytest_agent_tool_call(store, {})
    recorded = next(
        item
        for item in store.get_trace_view(source_id).tool_calls
        if item.entry_id == entry_id
    )
    assert recorded.tool.value == "[REDACTED]"
    app = create_app(Settings(database_path=str(database)), v2_store=store)
    url = f"/api/v2/executions/{source_id}/tool-calls/{entry_id}/replay"
    with TestClient(app) as client:
        rejected = client.post(url, json={"arguments": {"text": "explicit"}})
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "tool_call_not_replayable"
        listed = client.get("/api/v2/executions").json()["page"]["items"]
        assert [item["execution_id"] for item in listed] == [source_id]
    store.close()


def test_v2_replay_spec_hides_http_url_and_literal_headers():
    reference = SecretReference(source="environment", name="REPLAY_TOKEN")
    server = HTTPServer(
        name="private-http",
        url="https://internal.example.test/private/mcp",
        headers={"X-Api-Key": "header-literal", "Authorization": reference},
    )

    def spec(metadata):
        return DirectSpec(
            servers=(ServerBinding(server=server, alias="private-http"),),
            operation=CallTool(server="private-http", name="echo", arguments={}),
            metadata=metadata,
        )

    visible = _visible_spec(
        spec(
            {
                "replayed_from.execution_id": "execution-src",
                "replayed_from.entry_id": "e",
            }
        )
    )
    shown = visible.servers[0].server
    assert shown.url == "redacted"
    assert shown.headers == {"X-Api-Key": "redacted", "Authorization": reference}
    # Only replay executions are redacted; other specs are returned as stored.
    assert _visible_spec(spec({})).servers[0].server == server


def test_v2_tool_call_replay_is_rejected_by_read_only_viewer(tmp_path):
    from m3_app.api.app import create_viewer_app

    database = Path(tmp_path).resolve() / "replay-viewer.sqlite"
    with TestClient(create_viewer_app(Settings(database_path=str(database)))) as client:
        response = client.post(
            "/api/v2/executions/execution-any/tool-calls/entry:any/replay"
        )
        assert response.status_code == 405
        assert client.get("/api/v2/capabilities").json()["tool_call_replay"] is False
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v2/executions/{execution_id}/tool-calls/{entry_id}/replay" not in (
            paths
        )
