"""Exercise report construction from a real persisted M3 execution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit
from m3.feedback import build_feedback, export_feedback
from m3.storage import SQLiteExecutionStore
from m3.types import CallTool, DirectSpec, ServerBinding, StdioServer
from m3_cli.control_plane import inspect_current_run, upload_current_run

pytestmark = pytest.mark.e2e


def test_complete_current_run_uploads_summary_execution_and_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import m3_cli.control_plane as control_plane

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
        digest, contains_secret = inspect_current_run(
            feedback, store, directory, sensitive_values=("unused-test-credential",)
        )
        assert contains_secret is False

        sent: list[tuple[str, bytes]] = []
        monkeypatch.setattr(
            control_plane,
            "_post",
            lambda url, _token, body: sent.append((url, body)),
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
        assert execution["report"]["trace"]["schema_version"] == "1.1"
        assert execution["report"]["report"]["events_truncated"] is False
        assert json.loads(sent[2][1]) == {"transport_version": 1}
    finally:
        store.close()
