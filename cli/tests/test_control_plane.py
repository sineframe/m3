from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from m3.feedback import build_feedback, export_feedback
from m3.storage import SQLiteExecutionStore
from m3.types import ExecutionId, ExecutionState, RunId
from m3_app.api.report_payloads import build_execution_envelope
from m3_app.services.execution_service import project_test_results
from m3_cli.control_plane import _post, upload_current_run


def test_public_execution_envelope_is_json_serializable():
    snapshot = ExecutionState(execution_id=ExecutionId("exec-1"))
    envelope = build_execution_envelope(snapshot, None, None)
    assert json.loads(json.dumps(envelope))["execution_id"] == "exec-1"


def test_empty_sqlite_run_uploads_feedback_then_publishes(tmp_path, monkeypatch):
    import m3_cli.control_plane as control_plane

    root = tmp_path.resolve()
    store = SQLiteExecutionStore(root / "results.sqlite")
    try:
        feedback = build_feedback(store, "run-test")
        directory = root / "reports" / "run-test"
        export_feedback(feedback, store, directory)
        requests = []
        monkeypatch.setattr(
            control_plane,
            "_post",
            lambda url, token, body: requests.append((url, token, body)),
        )
        upload_current_run(
            feedback,
            store,
            directory,
            base_url="https://control-plane.example",
            token="m3pat_test",
        )
        assert [url.rsplit("/", 1)[-1] for url, _, _ in requests] == [
            "report",
            "publish",
        ]
        summary = json.loads(requests[0][2])
        assert summary["transport_version"] == 1
        assert summary["execution_ids"] == []
        assert summary["feedback"]["feedback"]["run_id"] == "run-test"
        assert json.loads(requests[1][2]) == {"transport_version": 1}
        assert (directory / "control-plane" / "summary.json").read_bytes() == requests[
            0
        ][2]
        upload_current_run(
            feedback,
            store,
            directory,
            base_url="https://control-plane.example",
            token="m3pat_test",
        )
        assert requests[2][2] == requests[0][2]
    finally:
        store.close()


def test_execution_id_summary_uses_separate_cache_file(tmp_path, monkeypatch):
    import m3_cli.control_plane as control_plane

    root = tmp_path.resolve()
    store = SQLiteExecutionStore(root / "results.sqlite")
    try:
        feedback = build_feedback(store, "run-test")
        directory = root / "reports" / "run-test"
        export_feedback(feedback, store, directory)
        store.create(
            ExecutionState(
                execution_id=ExecutionId("summary"), run_id=RunId("run-test")
            )
        )
        sent: list[tuple[str, bytes]] = []
        monkeypatch.setattr(
            control_plane,
            "_execution_payload",
            lambda _store, _snapshot: {
                "transport_version": 1,
                "marker": "execution",
                "snapshot": {"execution_id": "summary", "run_id": "run-test"},
            },
        )
        monkeypatch.setattr(
            control_plane,
            "_post",
            lambda url, _token, body: sent.append((url, body)),
        )

        for _ in range(2):
            upload_current_run(
                feedback,
                store,
                directory,
                base_url="https://control-plane.example",
                token="m3pat_test",
            )

        cache = directory / "control-plane"
        assert (cache / "summary.json").read_bytes() == sent[0][1]
        assert (cache / "executions" / "summary.json").read_bytes() == sent[1][1]
        assert (cache / "executions").stat().st_mode & 0o777 == 0o700
        assert (cache / "executions" / "summary.json").stat().st_mode & 0o777 == 0o600
        assert sent[0][1] != sent[1][1]
        assert json.loads(sent[1][1])["marker"] == "execution"
        assert sent[0][1] == sent[3][1]
        assert sent[1][1] == sent[4][1]
        assert sent[1][0].endswith("/executions/summary/report")
    finally:
        store.close()


def test_test_results_match_public_order_and_drop_invalid_records():
    values = project_test_results(
        [
            {
                "attempt_id": "b",
                "node_id": "z",
                "execution_ids": ["e"],
                "description": "z",
                "outcome": "passed",
                "duration_seconds": float("nan"),
            },
            {
                "attempt_id": "a",
                "node_id": "a",
                "execution_ids": ["e"],
                "description": "a",
                "outcome": "failed",
                "duration_seconds": 1,
            },
            {"attempt_id": "bad", "node_id": "", "execution_ids": ["e"]},
        ],
        "e",
    )
    assert [item.node_id for item in values] == ["a", "z"]
    assert values[1].duration_seconds is None


def test_post_uses_json_and_retries_same_bytes():
    received = []

    class Handler(BaseHTTPRequestHandler):
        calls = 0

        def do_POST(self):
            Handler.calls += 1
            received.append(
                (
                    self.path,
                    self.headers["Content-Type"],
                    self.headers["Authorization"],
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            self.send_response(503 if Handler.calls == 1 else 201)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _post(
            f"http://127.0.0.1:{server.server_port}/v1/runs/r/report",
            "secret",
            b'{"x":1}',
        )
    finally:
        server.shutdown()
        thread.join()
    assert len(received) == 2
    assert {item[1] for item in received} == {"application/json"}
    assert {item[2] for item in received} == {"Bearer secret"}
    assert received[0][3] == received[1][3] == b'{"x":1}'


def test_post_rejects_redirect_without_forwarding_token():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(RuntimeError, match="upload failed"):
            _post(f"http://127.0.0.1:{server.server_port}/", "secret", b"{}")
    finally:
        server.shutdown()
        thread.join()
