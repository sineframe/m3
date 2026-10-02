"""Exercise report construction from a real persisted M3 execution."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import m3_cli.control_plane as control_plane
from m3 import MCPTestKit
from m3.feedback import build_feedback, export_feedback
from m3.observability import CorrelationState
from m3.storage import SQLiteExecutionStore
from m3.types import (
    CallTool,
    DirectSpec,
    EventId,
    EventKind,
    EventOrigin,
    EventSource,
    ServerBinding,
    StdioServer,
)
from m3_cli.control_plane import inspect_current_run, upload_current_run

pytestmark = pytest.mark.e2e


def test_complete_current_run_uploads_summary_execution_and_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    repository = Path(__file__).parents[2]
    fixture = repository / "sdk/tests/fixtures/matrix_stdio_server.py"
    store = SQLiteExecutionStore(root / "results.sqlite")
    try:
        server = StdioServer(
            name="fixture",
            command=sys.executable,
            args=(str(fixture),),
            cwd=str(repository),
        )
        spec = DirectSpec(
            servers=(ServerBinding(server=server, alias="fixture"),),
            operation=CallTool(
                server="fixture", name="echo", arguments={"text": "upload-test"}
            ),
        )
        with MCPTestKit(
            store=store, env={}, cwd=str(repository), run_id="run-upload"
        ) as kit:
            result = kit.run(spec)
        execution_id = result.snapshot.execution_id.root
        store.save_test_result(
            "run-upload",
            "attempt-upload",
            {
                "attempt_id": "attempt-upload",
                "node_id": "tests/test_upload.py::test_result",
                "suite_name": "upload",
                "outcome": "passed",
                "execution_ids": [execution_id],
            },
        )
        feedback = build_feedback(store, "run-upload")
        directory = root / "reports" / "run-upload"
        export_feedback(feedback, store, directory)
        digest = inspect_current_run(
            feedback,
            store,
            directory,
            token="m3pat_test",
            sensitive_values=("unused-test-credential",),
        )

        sent: list[tuple[str, bytes]] = []
        monkeypatch.setattr(
            control_plane,
            "_post",
            lambda url, _token, body, _subject: sent.append((url, body)),
        )
        upload_current_run(
            feedback,
            store,
            directory,
            base_url="https://control-plane.example",
            token="m3pat_test",
            expected_digest=digest,
        )

        assert [url.rsplit("/", 1)[-1] for url, _ in sent] == [
            "report",
            "report",
            "publish",
        ]
        summary = json.loads(sent[0][1])
        execution = json.loads(sent[1][1])
        assert summary["execution_ids"] == [execution_id]
        assert summary["feedback"]["feedback"]["run_id"] == "run-upload"
        assert execution["snapshot"]["execution_id"] == execution_id
        assert execution["snapshot"]["run_id"] == "run-upload"
        assert execution["snapshot"]["lifecycle"] == "finished"
        assert execution["execution"]["execution_id"] == execution_id
        assert execution["report"]["execution_id"] == execution_id
        assert execution["report"]["report"]["snapshot"]["run_id"] == "run-upload"
        assert execution["report"]["report"]["event_count"] == len(
            execution["report"]["report"]["events"]
        )
        assert execution["report"]["trace"]["execution_id"] == execution_id
        assert execution["report"]["trace"]["schema_id"] == "trace_view"
        assert execution["report"]["test_results"] == [
            {
                "attempt_id": "attempt-upload",
                "node_id": "tests/test_upload.py::test_result",
                "description": "",
                "outcome": "passed",
                "verdict": "passed",
                "effective_verdict": "passed",
                "duration_seconds": None,
            }
        ]
        assert execution["report"]["trace"]["schema_version"] == "2.0"
        assert execution["report"]["report"]["events_truncated"] is False
        assert json.loads(sent[2][1]) == {"transport_version": 1}
    finally:
        store.close()


_UPLOAD_LIMIT_BYTES = 16 * 1024 * 1024


@pytest.mark.parametrize("field", ("arguments", "result"))
def test_correlated_five_mib_value_upload_stays_below_the_limit(
    tmp_path: Path, field: str
) -> None:
    """A value is held by the wire event, the harness event and the trace once."""
    root = tmp_path.resolve()
    repository = Path(__file__).parents[2]
    fixture = repository / "sdk/tests/fixtures/matrix_stdio_server.py"
    large = "a" * (5 * 1024 * 1024)
    store = SQLiteExecutionStore(root / "results.sqlite")
    try:
        server = StdioServer(
            name="fixture",
            command=sys.executable,
            args=(str(fixture),),
            cwd=str(repository),
        )
        spec = DirectSpec(
            servers=(ServerBinding(server=server, alias="fixture"),),
            operation=CallTool(server="fixture", name="echo", arguments={"text": "x"}),
        )
        with MCPTestKit(
            store=store, env={}, cwd=str(repository), run_id="run-large"
        ) as kit:
            result = kit.run(spec)
        execution_id = result.snapshot.execution_id.root
        snapshot = store.get_snapshot(execution_id)
        report = store.get_report(execution_id, event_limit=None, artifact_limit=None)
        wire = store.get_trace(execution_id)
        assert report is not None and wire is not None

        # The agent run's evidence: the wire call and the harness's report of it,
        # both carrying the same large argument.
        request = next(
            e for e in wire.events if e.kind is EventKind.TOOL_CALL_REQUESTED
        )
        response = next(
            e for e in wire.events if e.kind is EventKind.TOOL_RESULT_RECEIVED
        )
        large_request = request
        large_response = response
        if field == "arguments":
            large_request = request.model_copy(
                update={
                    "payload": {
                        **request.payload,
                        "params": {"name": "echo", "arguments": {"text": large}},
                    },
                    "payload_ref": None,
                }
            )
        else:
            large_response = response.model_copy(
                update={
                    "payload": {
                        **response.payload,
                        "result": {"content": [{"type": "text", "text": large}]},
                    },
                    "payload_ref": None,
                }
            )
        provenance = EventSource(origin=EventOrigin.HARNESS_REPORTED, source="harness")
        sequence = wire.highest_sequence
        harness = [
            event.model_copy(
                update={
                    "sequence": sequence + offset,
                    "event_id": EventId(f"harness-{offset}"),
                    "correlation": None,
                    "provenance": provenance,
                    "payload": {**event.payload, "call_id": "harness-call"},
                    "payload_ref": None,
                }
            )
            for offset, event in enumerate((large_request, large_response))
        ]
        terminal = wire.events[-1].model_copy(update={"sequence": sequence + 2})
        events = tuple(
            large_request
            if event is request
            else large_response
            if event is response
            else event
            for event in wire.events[:-1]
        )
        trace = wire.model_copy(
            update={
                "events": (*events, *harness, terminal),
                "highest_sequence": sequence + 2,
            }
        )
        view = trace.view()
        (call,) = view.tool_calls
        assert call.correlation is CorrelationState.CORRELATED
        assert call.reported.value is not None
        assert field in call.reported.value.same_as_call

        entry = SimpleNamespace(
            report=report.model_copy(
                update={
                    "events": trace.events,
                    "event_count": len(trace.events),
                }
            ),
            trace=view,
            spec=None,
        )
        body = control_plane._json_bytes(
            control_plane._execution_payload(store, snapshot, entry, ())
        )

        print(f"correlated 5 MiB {field} execution body: {len(body)} bytes")
        assert len(body) < _UPLOAD_LIMIT_BYTES
        envelope = json.loads(body)["report"]
        assert json.dumps(envelope["trace"]).count(large) == 1
        assert json.dumps(envelope["report"]["events"]).count(large) == 2
    finally:
        store.close()
