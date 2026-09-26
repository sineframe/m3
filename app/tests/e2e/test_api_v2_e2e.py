"""Black-box v2 API workflows over real local stdio subprocesses."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from _local_client import TestClient
from pydantic import TypeAdapter

from m3 import (
    ACPAgent,
    CallTool,
    DirectSpec,
    ExecutionReport,
    ExecutionSpec,
    RawEvidence,
    RestrictiveToolPolicy,
    ServerBinding,
    StdioServer,
    TextContent,
    TraceView,
    UserMessage,
)
from m3._types.specs import AgentSpec
from m3.storage import SQLiteExecutionStore
from m3_app.api.app import create_app
from m3_app.api.wire import internalize_request
from m3_app.settings import Settings

pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]


def _wait_terminal(client: TestClient, execution_id: str) -> dict:
    for _ in range(200):
        body = client.get(f"/api/v2/executions/{execution_id}").json()
        if body["snapshot"]["lifecycle"] == "finished":
            return body
        time.sleep(0.05)
    raise AssertionError("v2 execution did not become terminal")


def _direct_spec() -> DirectSpec:
    return DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="echo",
                    command=sys.executable,
                    args=("-m", "m3.fixtures.echo_server"),
                )
            ),
        ),
        operation=CallTool(server="echo", name="echo", arguments={"text": "api-e2e"}),
    )


def test_v2_direct_real_stdio_and_sqlite_reopen(tmp_path: Path) -> None:
    database = tmp_path / "direct.sqlite"
    spec = _direct_spec()
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        created = client.post(
            "/api/v2/executions", json={"spec": spec.model_dump(mode="json")}
        )
        assert created.status_code == 202
        execution_id = created.json()["execution_id"]
        terminal = _wait_terminal(client, execution_id)
        assert (
            TypeAdapter(ExecutionSpec).validate_python(
                internalize_request("/api/v2/executions", terminal["spec"])
            )
            == spec
        )
        report_response = client.get(
            f"/api/v2/executions/{execution_id}/report", params={"event_limit": 1000}
        )
        assert report_response.status_code == 200
        report_body = report_response.json()
        assert report_body["trace"]["schema_id"] == "trace_view"
        assert report_body["report"]["events"]
        assert all(
            event["schema"] == "event" for event in report_body["report"]["events"]
        )
        report = TypeAdapter(ExecutionReport).validate_python(
            internalize_request("/api/v2/executions/report", report_body["report"])
        )
        trace = TypeAdapter(TraceView).validate_python(
            internalize_request(
                "/api/v2/executions/report", {"trace": report_body["trace"]}
            )["trace"]
        )
        assert (
            report.direct_result is not None
            and report.direct_result.kind == "call_tool"
        )
        assert trace.schema_version == "1.1"
        assert trace.tool_calls and trace.transports and trace.protocol
        assert trace.summary.timing.duration_ms >= 0
        assert trace.tool_calls[0].arguments.value == {"text": "api-e2e"}
    reopened = SQLiteExecutionStore(database)
    try:
        assert reopened.get_snapshot(execution_id) == report.snapshot
        assert reopened.get_execution_spec(execution_id) == spec
        assert reopened.get_report(execution_id, event_limit=1000) == report
        assert reopened.get_trace_view(execution_id) == trace
    finally:
        reopened.close()


def test_v2_direct_missing_executable_exposes_a_terminal_failed_trace(
    tmp_path: Path,
) -> None:
    database = tmp_path / "direct-failed.sqlite"
    spec = DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(name="missing", command="m3-no-such-executable")
            ),
        ),
        operation=CallTool(server="missing", name="echo", arguments={}),
    )
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        created = client.post(
            "/api/v2/executions", json={"spec": spec.model_dump(mode="json")}
        )
        assert created.status_code == 202
        execution_id = created.json()["execution_id"]
        terminal = _wait_terminal(client, execution_id)
        assert terminal["snapshot"]["outcome"] == "failed"
        response = client.get(f"/api/v2/executions/{execution_id}/report")
        assert response.status_code == 200
        assert (
            TypeAdapter(ExecutionReport)
            .validate_python(
                internalize_request(
                    "/api/v2/executions/report", response.json()["report"]
                )
            )
            .snapshot.outcome
            == "failed"
        )
        assert (
            TypeAdapter(TraceView)
            .validate_python(
                internalize_request(
                    "/api/v2/executions/report",
                    {"trace": response.json()["trace"]},
                )["trace"]
            )
            .outcome
            == "failed"
        )


def test_v2_acp_real_agent_and_mcp_stdio_with_raw_evidence(tmp_path: Path) -> None:
    database = tmp_path / "acp.sqlite"
    fixtures = Path(__file__).parents[3] / "sdk" / "tests" / "fixtures"
    acp_marker = tmp_path / "acp.jsonl"
    manifest = {
        "schema_version": "m3.harness.v1",
        "protocol": "acp",
        "protocol_version": 1,
        "command": sys.executable,
        "args": [
            str(fixtures / "observing_acp_agent.py"),
            "--observation-marker",
            str(acp_marker),
            "--target",
            sys.executable,
            "--target-args-json",
            '["-m","m3.fixtures.structured_cli"]',
        ],
        "env": {},
    }
    spec = AgentSpec(
        harness=ACPAgent(model="agent-default", manifest=manifest),
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="e2e-mcp",
                    command=sys.executable,
                    args=(str(fixtures / "matrix_stdio_server.py"),),
                    cwd=str(fixtures.parents[1]),
                ),
                alias="e2e-mcp",
            ),
        ),
        tool_policy=RestrictiveToolPolicy(allowed_tools=("e2e-mcp:echo",)),
        message=UserMessage(content=(TextContent(text="api-acp-e2e"),)),
    )
    with TestClient(create_app(Settings(database_path=str(database)))) as client:
        created = client.post(
            "/api/v2/executions", json={"spec": spec.model_dump(mode="json")}
        )
        assert created.status_code == 202
        execution_id = created.json()["execution_id"]
        terminal = _wait_terminal(client, execution_id)
        assert (
            TypeAdapter(ExecutionSpec).validate_python(
                internalize_request("/api/v2/executions", terminal["spec"])
            )
            == spec
        )
        response = client.get(
            f"/api/v2/executions/{execution_id}/report", params={"event_limit": 1000}
        )
        assert response.status_code == 200
        body = response.json()
        report = TypeAdapter(ExecutionReport).validate_python(
            internalize_request("/api/v2/executions/report", body["report"])
        )
        trace = TypeAdapter(TraceView).validate_python(
            internalize_request("/api/v2/executions/report", {"trace": body["trace"]})[
                "trace"
            ]
        )
        assert trace.runtime.kind == "acp"
        assert trace.runtime.session_id.value
        assert trace.transports and all(
            entry.configured.value == "stdio" and entry.instrumented.value == "stdio"
            for entry in trace.transports
        )
        assert trace.messages and any(
            getattr(item.content[0], "text", "") == "api-acp-e2e"
            for item in trace.messages
            if item.content
        )
        assert trace.summary.timing.duration_ms >= 0
        assert trace.tool_calls and trace.tool_calls[-1].arguments.value == {
            "text": "api-acp-e2e"
        }
        wire_calls = [
            entry
            for entry in trace.tool_calls
            if any(item.origin.value == "wire_observed" for item in entry.provenance)
        ]
        reported_calls = [
            entry
            for entry in trace.tool_calls
            if any(item.origin.value == "harness_reported" for item in entry.provenance)
        ]
        assert wire_calls and reported_calls
        assert wire_calls[-1].arguments.value == {"text": "api-acp-e2e"}
        assert wire_calls[-1].result.value is not None
        ref = next(
            (
                entry.evidence_ref
                for entry in trace.raw_messages
                if entry.evidence_ref and entry.evidence_ref.sha256
            ),
            None,
        )
        assert ref is not None
        full_evidence_response = client.post(
            "/api/v2/evidence/read", json={"reference": ref.model_dump(mode="json")}
        )
        assert full_evidence_response.status_code == 200
        full_evidence = TypeAdapter(RawEvidence).validate_python(
            full_evidence_response.json()["evidence"]
        )
        assert full_evidence.redacted is True and not full_evidence.truncated
        evidence_response = client.post(
            "/api/v2/evidence/read",
            json={"reference": ref.model_dump(mode="json"), "max_bytes": 1},
        )
        assert evidence_response.status_code == 200
        evidence = TypeAdapter(RawEvidence).validate_python(
            evidence_response.json()["evidence"]
        )
        assert (
            evidence.redacted is True
            and evidence.truncated is True
            and evidence.returned_size_bytes == 1
        )
        for max_bytes in (0, 1_048_577):
            invalid = client.post(
                "/api/v2/evidence/read",
                json={"reference": ref.model_dump(mode="json"), "max_bytes": max_bytes},
            )
            assert invalid.status_code == 422
            assert invalid.json()["error"]["code"] == "invalid_request"
        missing = client.post(
            "/api/v2/evidence/read",
            json={"reference": {"evidence_id": "missing-evidence"}},
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "raw_evidence_not_found"
        wrong_digest = ref.model_copy(update={"sha256": "0" * 64})
        integrity = client.post(
            "/api/v2/evidence/read",
            json={"reference": wrong_digest.model_dump(mode="json")},
        )
        assert integrity.status_code == 500
        assert integrity.json()["error"]["code"] == "raw_evidence_integrity_error"
        canary = "raw-evidence-underlying-canary"
        assert canary.encode() not in integrity.content
    reopened = SQLiteExecutionStore(database)
    try:
        assert acp_marker.exists()
        observed_methods = [
            json.loads(line)["method"] for line in acp_marker.read_text().splitlines()
        ]
        assert {"initialize", "session/new", "session/prompt"} <= set(observed_methods)
        assert reopened.get_execution_spec(execution_id) == spec
        assert reopened.get_report(execution_id, event_limit=1000) == report
        assert reopened.get_trace_view(execution_id) == trace
    finally:
        reopened.close()
