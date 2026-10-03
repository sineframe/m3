from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from m3.feedback import build_feedback, export_feedback
from m3.storage import SQLiteExecutionStore
from m3.types import ExecutionId, ExecutionState, RunId
from m3_app.api.report_payloads import build_execution_envelope
from m3_app.services.execution_service import project_test_results
from m3_cli.control_plane import PublishResult, _post, upload_current_run
from m3_cli.errors import UploadError


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
            lambda url, token, body, _subject: requests.append((url, token, body)),
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


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            {
                "published": True,
                "run_url": "https://x/reports/runs/r",
                "run_label": "Run abcdef02",
            },
            PublishResult("Run abcdef02", "https://x/reports/runs/r"),
        ),
        (
            {"published": True, "run_url": "https://x/reports/runs/r"},
            PublishResult(None, "https://x/reports/runs/r"),
        ),
        (
            {"published": True, "run_url": "http://x/reports/runs/r"},
            PublishResult(None, None),
        ),
        ({"published": True, "run_label": ""}, PublishResult(None, None)),
        ({"published": True, "run_label": "x" * 257}, PublishResult(None, None)),
        ({"published": True, "run_label": 7}, PublishResult(None, None)),
        ({"published": True}, PublishResult(None, None)),
        (None, PublishResult(None, None)),
    ],
)
def test_upload_returns_label_and_https_run_url_from_publish_response(
    tmp_path, monkeypatch, response, expected
):
    import m3_cli.control_plane as control_plane

    root = tmp_path.resolve()
    store = SQLiteExecutionStore(root / "results.sqlite")
    try:
        feedback = build_feedback(store, "run-test")
        directory = root / "reports" / "run-test"
        export_feedback(feedback, store, directory)
        monkeypatch.setattr(
            control_plane,
            "_post",
            lambda url, *_args: response if url.endswith("/publish") else None,
        )
        assert (
            upload_current_run(
                feedback,
                store,
                directory,
                base_url="https://control-plane.example",
                token="m3pat_test",
            )
            == expected
        )
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
            lambda _store, _snapshot, _entry, _attempts: {
                "transport_version": 1,
                "marker": "execution",
                "snapshot": {"execution_id": "summary", "run_id": "run-test"},
            },
        )
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
        )
        monkeypatch.setattr(
            control_plane,
            "load_run_entries",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("cached retry must not load the run")
            ),
        )
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


@contextmanager
def _serve(handler):
    """Run ``handler`` on a local HTTP server and yield its base URL."""
    handler.log_message = lambda *_args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def _replying(status, body=b""):
    """Build a handler that counts requests and always answers ``status``."""

    class Handler(BaseHTTPRequestHandler):
        calls = 0

        def do_POST(self):
            Handler.calls += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def test_post_uses_json_and_retries_same_bytes(monkeypatch):
    import m3_cli.control_plane as control_plane

    monkeypatch.setattr(control_plane.time, "sleep", lambda _seconds: None)
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

    with _serve(Handler) as base:
        _post(base + "/v1/runs/r/report", "secret", b'{"x":1}', "run r summary")
    assert len(received) == 2
    assert {item[1] for item in received} == {"application/json"}
    assert {item[2] for item in received} == {"Bearer secret"}
    assert received[0][3] == received[1][3] == b'{"x":1}'


def test_post_rejects_redirect_once_without_forwarding_token():
    class Handler(_replying(302)):
        def send_response(self, code, message=None):
            super().send_response(code, message)
            self.send_header("Location", "https://example.invalid/")

    with _serve(Handler) as base:
        with pytest.raises(UploadError, match="redirected") as raised:
            _post(base + "/", "secret", b"{}", "run r summary")
    assert raised.value.retryable is False
    assert Handler.calls == 1


def test_post_client_rejection_names_subject_and_server_code_without_retrying():
    handler = _replying(
        413, b'{"error":{"code":"payload_too_large","message":"too big"}}'
    )
    with _serve(handler) as base:
        with pytest.raises(UploadError) as raised:
            _post(base + "/", "secret", b"{}", "execution exec-1 report")
    assert str(raised.value) == (
        "the M3 server rejected execution exec-1 report (HTTP 413 payload_too_large)"
    )
    assert (raised.value.retryable, raised.value.status, raised.value.code) == (
        False,
        413,
        "payload_too_large",
    )
    assert handler.calls == 1


@pytest.mark.parametrize(
    "body",
    [
        b'{"error":{"code":"Bad Code\\u001b[31m"}}',
        b'{"error":{"code":"' + b"a" * 65 + b'"}}',
        b'{"error":"payload_too_large"}',
        b"[" * 5000,
        b"not json",
    ],
)
def test_post_drops_server_codes_that_are_not_plain_identifiers(body):
    with _serve(_replying(400, body)) as base:
        with pytest.raises(UploadError) as raised:
            _post(base + "/", "secret", b"{}", "run r summary")
    assert str(raised.value) == "the M3 server rejected run r summary (HTTP 400)"
    assert raised.value.code is None


def test_post_reports_retryable_server_failure_after_all_attempts(monkeypatch):
    import m3_cli.control_plane as control_plane

    monkeypatch.setattr(control_plane.time, "sleep", lambda _seconds: None)
    handler = _replying(503, b'{"error":{"code":"unavailable"}}')
    with _serve(handler) as base:
        with pytest.raises(UploadError) as raised:
            _post(base + "/", "secret", b"{}", "run r publication")
    assert str(raised.value) == (
        "the M3 server did not accept run r publication after 3 attempts "
        "(HTTP 503 unavailable)"
    )
    assert raised.value.retryable is True
    assert handler.calls == 3


def test_post_reports_unreachable_server_as_retryable(monkeypatch):
    import m3_cli.control_plane as control_plane

    monkeypatch.setattr(control_plane.time, "sleep", lambda _seconds: None)
    with _serve(_replying(201)) as base:
        pass
    with pytest.raises(UploadError) as raised:
        _post(base + "/", "secret", b"{}", "run r summary")
    assert str(raised.value) == (
        "could not reach the M3 server to send run r summary after 3 attempts"
    )
    assert (raised.value.retryable, raised.value.status) == (True, None)


def test_post_retries_malformed_response_as_unreachable(monkeypatch):
    import m3_cli.control_plane as control_plane

    class Handler(BaseHTTPRequestHandler):
        calls = 0

        def do_POST(self):
            Handler.calls += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            self.wfile.write(b"GARBAGE\r\n\r\n")
            self.close_connection = True

    monkeypatch.setattr(control_plane.time, "sleep", lambda _seconds: None)
    with _serve(Handler) as base:
        with pytest.raises(UploadError) as raised:
            _post(base + "/", "secret", b"{}", "run r summary")
    assert str(raised.value) == (
        "could not reach the M3 server to send run r summary after 3 attempts"
    )
    assert (raised.value.retryable, raised.value.status) == (True, None)
    assert Handler.calls == 3
