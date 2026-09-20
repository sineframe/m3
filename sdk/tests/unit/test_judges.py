import json
import os
import shutil
import ssl
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from m3.judges import LLMJudge
from m3.storage.sqlite import SQLiteExecutionStore
from m3.sync_api import MCPTestKit
from m3.types import EvaluationContext, EvaluationStatus


def test_missing_judge_key_is_safe_and_does_not_request(monkeypatch):
    monkeypatch.delenv("M3_JUDGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "agent-secret")
    monkeypatch.setattr(
        "m3.judges._openai_sync",
        lambda *_: pytest.fail("missing judge key must not make a provider request"),
    )
    judge = LLMJudge(model="judge")
    result = judge(
        EvaluationContext(subject={"input": "task", "expected": "answer", "actual": ""})
    )
    assert result.status is EvaluationStatus.ERROR
    assert result.details["error_code"] == "judge_credentials_missing"
    assert "M3_JUDGE_API_KEY" in result.rationale


def test_default_judge_key_is_separate_from_agent_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "agent-secret")
    monkeypatch.setenv("M3_JUDGE_API_KEY", "judge-secret")
    keys: list[str] = []

    def request(_judge, _payload, key):
        keys.append(key)
        return {"score": 1, "rationale": "ok", "abstain": False}, {}, "judge"

    monkeypatch.setattr("m3.judges._openai_sync", request)
    subject = EvaluationContext(
        subject={"input": "task", "expected": "answer", "actual": "answer"}
    )
    result = LLMJudge(model="judge")(subject)
    assert result.status is EvaluationStatus.PASSED
    assert keys == ["judge-secret"]
    assert (
        LLMJudge(model="judge").config_digest
        == LLMJudge(model="judge", api_key_env="M3_JUDGE_API_KEY").config_digest
    )


def test_malformed_subject_does_not_request(monkeypatch):
    called = False

    def fail(*args):
        nonlocal called
        called = True
        raise AssertionError("request should not be made")

    monkeypatch.setattr("m3.judges._openai_sync", fail)
    result = LLMJudge(model="judge", api_key_env="KEY")(
        EvaluationContext(subject={"input": ""})
    )
    assert result.status is EvaluationStatus.ERROR
    assert result.details["error_code"] == "judge_input_invalid"
    assert called is False


def test_judge_validates_response_and_provenance(monkeypatch):
    monkeypatch.setenv("KEY", "secret")
    monkeypatch.setattr(
        "m3.judges._openai_sync",
        lambda *_: (
            {"score": 0.9, "rationale": "matches", "abstain": False},
            {"total_tokens": 4},
            "returned",
        ),
    )
    result = LLMJudge(model="judge", api_key_env="KEY")(
        EvaluationContext(
            subject={"input": "task", "expected": "answer", "actual": "answer"}
        )
    )
    assert result.status is EvaluationStatus.PASSED
    assert result.provenance is not None
    assert result.provenance.kind == "llm_judge"
    assert result.details["returned_model"] == "returned"


@pytest.mark.asyncio
async def test_async_judge_uses_async_adapter(monkeypatch):
    monkeypatch.setenv("KEY", "secret")

    async def request(*_):
        return {"score": 0.1, "rationale": "wrong", "abstain": False}, {}, "returned"

    monkeypatch.setattr("m3.judges._openai_async", request)
    result = await LLMJudge(model="judge", api_key_env="KEY").evaluate_async(
        EvaluationContext(
            subject={"input": "task", "expected": "answer", "actual": "no"}
        )
    )
    assert result.status is EvaluationStatus.FAILED


@pytest.mark.asyncio
async def test_async_judge_uses_default_judge_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "agent-secret")
    monkeypatch.setenv("M3_JUDGE_API_KEY", "judge-secret")
    keys: list[str] = []

    async def request(_judge, _payload, key):
        keys.append(key)
        return {"score": 1, "rationale": "ok", "abstain": False}, {}, "judge"

    monkeypatch.setattr("m3.judges._openai_async", request)
    result = await LLMJudge(model="judge").evaluate_async(
        EvaluationContext(
            subject={"input": "task", "expected": "answer", "actual": "answer"}
        )
    )
    assert result.status is EvaluationStatus.PASSED
    assert keys == ["judge-secret"]


@pytest.mark.asyncio
async def test_async_budget_exhausts_before_second_request(monkeypatch):
    monkeypatch.setenv("KEY", "secret")
    calls = 0

    async def request(*_):
        nonlocal calls
        calls += 1
        return {"score": 1, "rationale": "ok", "abstain": False}, {}, "returned"

    monkeypatch.setattr("m3.judges._openai_async", request)
    from m3.async_api import AsyncMCPTestKit

    kit = AsyncMCPTestKit(max_judge_requests=1)
    try:
        judge = LLMJudge(model="judge", api_key_env="KEY", max_retries=0)
        await kit.judge_response(
            name="budget.v1",
            input="task",
            expected="answer",
            actual="answer",
            judge=judge,
        )
        second = await kit.judge_response(
            name="budget.v1",
            input="task",
            expected="answer",
            actual="answer",
            judge=judge,
        )
        assert calls == 1
        assert second.details["error_code"] == "judge_budget_exhausted"
    finally:
        await kit.aclose()


def test_retry_consumes_two_budget_slots(monkeypatch):
    monkeypatch.setenv("KEY", "secret")
    calls = 0

    def request(*_):
        nonlocal calls
        calls += 1
        from m3.judges import _JudgeFailure

        raise _JudgeFailure("rate_limit", "temporary")

    monkeypatch.setattr("m3.judges._openai_sync", request)
    from m3.sync_api import MCPTestKit

    kit = MCPTestKit(max_judge_requests=2)
    try:
        result = kit.judge_response(
            name="retry.v1",
            input="task",
            expected="answer",
            actual="answer",
            judge=LLMJudge(model="judge", api_key_env="KEY", max_retries=1),
        )
        assert calls == 2
        assert result.details["error_code"] == "rate_limit"
    finally:
        kit.close()


def test_in_memory_execution_store_uses_local_judge_budget(monkeypatch):
    from m3.storage import InMemoryExecutionStore

    monkeypatch.setenv("KEY", "secret")
    calls = 0

    def request(*_):
        nonlocal calls
        calls += 1
        return {"score": 1, "rationale": "ok", "abstain": False}, {}, "judge"

    monkeypatch.setattr("m3.judges._openai_sync", request)
    kit = MCPTestKit(store=InMemoryExecutionStore(), max_judge_requests=1)
    judge = LLMJudge(model="judge", api_key_env="KEY", max_retries=0)
    try:
        first = kit.judge_response(
            name="budget.v1",
            input="task",
            expected="answer",
            actual="answer",
            judge=judge,
        )
        second = kit.judge_response(
            name="budget.v1",
            input="task",
            expected="answer",
            actual="answer",
            judge=judge,
        )
        assert first.status is EvaluationStatus.PASSED
        assert second.status is EvaluationStatus.ERROR
        assert second.details["error_code"] == "judge_budget_exhausted"
        assert calls == 1
    finally:
        kit.close()


def test_direct_kit_cap_cannot_raise_default_run_cap():
    from m3._default_store import (
        install_default_judge_limit_factory,
        restore_default_judge_limit_factory,
    )

    token = install_default_judge_limit_factory(lambda: 1)
    from m3.sync_api import MCPTestKit

    kit = MCPTestKit(max_judge_requests=100)
    try:
        assert kit._evaluations._judge_budget.limit == 1
    finally:
        kit.close()
        restore_default_judge_limit_factory(token)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_anonymous_redirects_are_blocked(monkeypatch, status):
    monkeypatch.setattr(
        "httpx2.Client.post",
        lambda *_args, **_kwargs: type("R", (), {"status_code": status})(),
    )
    result = LLMJudge(
        model="judge",
        auth="none",
        base_url="http://127.0.0.1:1",
        response_mode="json_schema",
    )(
        EvaluationContext(
            subject={"input": "task", "expected": "answer", "actual": "answer"}
        )
    )
    assert result.details["error_code"] == "judge_redirect_blocked"


def test_remote_anonymous_auth_is_rejected():
    with pytest.raises(ValueError, match="loopback"):
        LLMJudge(
            model="judge",
            auth="none",
            base_url="https://example.test/v1",
            response_mode="json_schema",
        )


@pytest.mark.parametrize("field", ["input", "expected"])
def test_oversized_required_subject_is_rejected(field):
    subject = {"input": "task", "expected": "answer", "actual": "answer"}
    subject[field] = "x" * (32 * 1024 + 1)
    result = LLMJudge(
        model="judge",
        auth="none",
        base_url="http://127.0.0.1:1",
        response_mode="json_schema",
    )(EvaluationContext(subject=subject))
    assert result.details["error_code"] == "judge_input_invalid"


@pytest.mark.parametrize("status", [301, 302, 307, 308])
@pytest.mark.parametrize("auth", ["none", "env"])
def test_wire_redirects_do_not_follow_or_leak_credentials(monkeypatch, status, auth):
    original: list[dict[str, str]] = []
    destination: list[str] = []

    class Original(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            original.append({"authorization": self.headers.get("Authorization", "")})
            self.send_response(status)
            self.send_header(
                "Location", f"http://127.0.0.1:{destination_server.server_port}/steal"
            )
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def log_message(self, *_args):
            pass

    class Destination(BaseHTTPRequestHandler):
        def do_POST(self):
            destination.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    destination_server = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    original_server = ThreadingHTTPServer(("127.0.0.1", 0), Original)
    threads = [
        Thread(target=server.serve_forever, daemon=True)
        for server in (destination_server, original_server)
    ]
    for thread in threads:
        thread.start()
    try:
        monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret")
        original_get = os.environ.get
        lookups: list[str] = []
        monkeypatch.setattr(
            os.environ,
            "get",
            lambda key, *args: (lookups.append(key), original_get(key, *args))[1],
        )
        if auth == "env":
            monkeypatch.setenv("KEY", "selected-secret")
        judge = LLMJudge(
            model="judge",
            auth=auth,
            api_key_env="KEY" if auth == "env" else None,
            base_url=f"http://127.0.0.1:{original_server.server_port}/v1",
            response_mode="json_schema",
        )
        result = judge(
            EvaluationContext(
                subject={"input": "task", "expected": "answer", "actual": "answer"}
            )
        )
        assert result.details["error_code"] == "judge_redirect_blocked"
        assert len(original) == 1
        assert destination == []
        if auth == "env":
            assert original[0]["authorization"] == "Bearer selected-secret"
        else:
            assert original[0]["authorization"] == ""
            assert "OPENAI_API_KEY" not in lookups
    finally:
        original_server.shutdown()
        destination_server.shutdown()
        original_server.server_close()
        destination_server.server_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_authenticated_loopback_bypasses_environment_proxy(
    monkeypatch, async_mode
):
    origin_requests: list[tuple[str, str]] = []
    proxy_requests: list[tuple[str, str]] = []
    response = json.dumps(
        {
            "id": "completion-1",
            "object": "chat.completion",
            "created": 0,
            "model": "judge",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {"score": 1, "rationale": "ok", "abstain": False}
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
        }
    ).encode()

    def reply(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(response)))
        handler.end_headers()
        handler.wfile.write(response)

    class Origin(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            origin_requests.append(
                (self.headers.get("Authorization", ""), body.decode())
            )
            reply(self)

        def log_message(self, *_args):
            pass

    class Proxy(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            proxy_requests.append(
                (self.headers.get("Authorization", ""), body.decode())
            )
            reply(self)

        def log_message(self, *_args):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threads = [
        Thread(target=server.serve_forever, daemon=True) for server in (origin, proxy)
    ]
    for thread in threads:
        thread.start()
    try:
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, proxy_url)
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.setenv("KEY", "judge-secret")
        judge = LLMJudge(
            model="judge",
            base_url=f"http://127.0.0.1:{origin.server_port}/v1",
            api_key_env="KEY",
            response_mode="json_text",
            max_retries=0,
        )
        context = EvaluationContext(
            subject={"input": "private task", "expected": "answer", "actual": "answer"}
        )
        result = await judge.evaluate_async(context) if async_mode else judge(context)
        assert result.status is EvaluationStatus.PASSED
        assert len(origin_requests) == 1
        assert origin_requests[0][0] == "Bearer judge-secret"
        assert "private task" in origin_requests[0][1]
        assert proxy_requests == []
    finally:
        origin.shutdown()
        proxy.shutdown()
        origin.server_close()
        proxy.server_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("ca_source", ["file", "dir"])
@pytest.mark.parametrize("auth", ["env", "none"])
async def test_https_loopback_uses_environment_ca_without_proxy(
    monkeypatch, tmp_path, async_mode, ca_source, auth
):
    fixture = Path(__file__).parents[1] / "fixtures" / "judge_tls"
    cert, key = fixture / "cert.pem", fixture / "key.pem"
    if ca_source == "file":
        monkeypatch.setenv("SSL_CERT_FILE", str(cert))
        monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    else:
        ca_dir = tmp_path / "ca"
        ca_dir.mkdir()
        shutil.copyfile(cert, ca_dir / "ce275665.0")
        monkeypatch.setenv("SSL_CERT_DIR", str(ca_dir))
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    origin_requests: list[tuple[str, str]] = []
    proxy_requests: list[str] = []
    response = json.dumps(
        {
            "id": "completion-1",
            "object": "chat.completion",
            "created": 0,
            "model": "judge",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {"score": 1, "rationale": "ok", "abstain": False}
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
        }
    ).encode()

    class Origin(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            origin_requests.append(
                (self.headers.get("Authorization", ""), body.decode())
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            pass

    class Proxy(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            proxy_requests.append(self.path)
            self.send_error(502)

        def log_message(self, *_args):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    origin.socket = tls.wrap_socket(origin.socket, server_side=True)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threads = [
        Thread(target=server.serve_forever, daemon=True) for server in (origin, proxy)
    ]
    for thread in threads:
        thread.start()
    try:
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, proxy_url)
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.setenv("KEY", "judge-secret")
        judge = LLMJudge(
            model="judge",
            base_url=f"https://127.0.0.1:{origin.server_port}/v1",
            auth=auth,
            api_key_env="KEY" if auth == "env" else None,
            response_mode="json_text",
            max_retries=0,
        )
        context = EvaluationContext(
            subject={"input": "private task", "expected": "answer", "actual": "answer"}
        )
        result = await judge.evaluate_async(context) if async_mode else judge(context)
        assert result.status is EvaluationStatus.PASSED
        assert len(origin_requests) == 1
        assert origin_requests[0][0] == ("Bearer judge-secret" if auth == "env" else "")
        assert "private task" in origin_requests[0][1]
        assert proxy_requests == []
    finally:
        origin.shutdown()
        proxy.shutdown()
        origin.server_close()
        proxy.server_close()


@pytest.mark.parametrize("status", [408, 409, 429, 500])
@pytest.mark.parametrize("auth", ["none", "env"])
def test_retry_policy_counts_transport_attempts(monkeypatch, status, auth):
    monkeypatch.setenv("KEY", "secret")
    calls = 0

    def request(*_args):
        nonlocal calls
        calls += 1
        from m3.judges import _JudgeFailure

        raise _JudgeFailure(
            "rate_limit"
            if status == 429
            else "server_error"
            if status >= 500
            else "provider_error",
            "failure",
        )

    target = "m3.judges._anonymous_sync" if auth == "none" else "m3.judges._openai_sync"
    monkeypatch.setattr(target, request)
    judge = LLMJudge(
        model="judge",
        auth=auth,
        api_key_env="KEY" if auth == "env" else None,
        base_url="http://127.0.0.1:1" if auth == "none" else None,
        response_mode="json_schema",
        max_retries=1,
    )
    result = judge(
        EvaluationContext(
            subject={"input": "task", "expected": "answer", "actual": "answer"}
        )
    )
    assert calls == (2 if status in {429, 500} else 1)
    assert result.status is EvaluationStatus.ERROR


@pytest.mark.parametrize("status", [408, 409, 429, 500])
@pytest.mark.parametrize("auth", ["none", "env"])
@pytest.mark.parametrize("max_retries,expected", [(0, 1), (1, 2)])
def test_live_http_status_retry_counts(
    monkeypatch, status, auth, max_retries, expected
):
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            calls.append(self.path)
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("KEY", "secret")
        judge = LLMJudge(
            model="judge",
            auth=auth,
            api_key_env="KEY" if auth == "env" else None,
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            response_mode="json_schema",
            max_retries=max_retries,
        )
        result = judge(
            EvaluationContext(
                subject={"input": "task", "expected": "answer", "actual": "answer"}
            )
        )
        assert len(calls) == (expected if status in {429, 500} else 1)
        assert result.status is EvaluationStatus.ERROR
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    "name",
    ["APIConnectionError", "APITimeoutError", "ConnectError", "TimeoutException"],
)
def test_provider_transport_exceptions_are_retryable(name):
    import httpx2

    from m3 import judges

    if name.startswith("API"):
        import openai

        error = getattr(openai, name)
        # Provider errors expose a response only after a request; a minimal
        # uninitialized instance is enough to exercise classification.
        instance = error.__new__(error)
    else:
        error = getattr(httpx2, name)
        instance = error("transport")
    assert judges._provider_error(instance).code == "timeout"


@pytest.mark.parametrize(
    "raw",
    [
        {"score": 1, "rationale": "x"},
        {"score": float("nan"), "rationale": "x", "abstain": False},
        {"score": 2, "rationale": "x", "abstain": False},
    ],
)
def test_invalid_judgment_output_does_not_retry(monkeypatch, raw):
    monkeypatch.setenv("KEY", "secret")
    calls = 0

    def request(*_):
        nonlocal calls
        calls += 1
        return raw, {}, "judge"

    monkeypatch.setattr("m3.judges._openai_sync", request)
    result = LLMJudge(model="judge", api_key_env="KEY", max_retries=1)(
        EvaluationContext(
            subject={"input": "task", "expected": "answer", "actual": "answer"}
        )
    )
    assert calls == 1
    assert result.details["error_code"] in {
        "judge_malformed_output",
        "malformed_output",
    }


@pytest.mark.parametrize("content,code", [("not-json", "judge_malformed_output")])
@pytest.mark.parametrize("finish_reason", [None, "length", "content_filter"])
def test_decode_invalid_and_incomplete_outputs_are_safe(content, code, finish_reason):
    from m3.judges import _decode, _JudgeFailure

    message = {"content": content, "refusal": None}
    value = {"choices": [{"message": message, "finish_reason": finish_reason}]}
    with pytest.raises(_JudgeFailure) as failure:
        _decode(value)
    assert failure.value.code == ("judge_incomplete_output" if finish_reason else code)


def test_decode_missing_fields_rejected_by_validation():
    from m3.judges import _decode, _JudgeFailure, _validate

    raw, _, _ = _decode(
        {"choices": [{"message": {"content": "{}"}, "finish_reason": None}]}
    )
    with pytest.raises(_JudgeFailure, match="missing"):
        _validate(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {
            "choices": [
                {
                    "message": {"refusal": "unsafe", "content": None},
                    "finish_reason": "stop",
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": '{"score": null, "rationale": "cannot judge", "abstain": true}'
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    ],
)
def test_refusal_and_abstention_do_not_retry(monkeypatch, raw):
    monkeypatch.setenv("KEY", "secret")
    calls = 0

    def request(*_):
        nonlocal calls
        calls += 1
        from m3.judges import _decode

        return _decode(raw)

    monkeypatch.setattr("m3.judges._openai_sync", request)
    result = LLMJudge(model="judge", api_key_env="KEY", max_retries=1)(
        EvaluationContext(
            subject={"input": "task", "expected": "answer", "actual": "answer"}
        )
    )
    assert calls == 1
    assert result.status is EvaluationStatus.ERROR


def test_provider_details_redact_secret_and_bound_usage(monkeypatch):
    monkeypatch.setenv("MY_VENDOR_JUDGE_SECRET", "super-secret")
    monkeypatch.setattr(
        "m3.judges._openai_sync",
        lambda *_: (
            {"score": 1, "rationale": "answer super-secret", "abstain": False},
            {"total_tokens": 4, "nested": {"secret": "super-secret"}},
            "model-super-secret",
        ),
    )
    result = LLMJudge(model="judge", api_key_env="MY_VENDOR_JUDGE_SECRET")(
        EvaluationContext(
            subject={"input": "task", "expected": "answer", "actual": "answer"}
        )
    )
    assert "super-secret" not in str(result.model_dump())
    assert result.details["usage"] == {"total_tokens": 4}


def test_anonymous_success_never_reads_environment_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient")
    original_get = os.environ.get
    lookups: list[str] = []
    monkeypatch.setattr(
        os.environ,
        "get",
        lambda key, *args: (lookups.append(key), original_get(key, *args))[1],
    )
    monkeypatch.setattr(
        "m3.judges._anonymous_sync",
        lambda *_: (
            {"score": 1, "rationale": "ok", "abstain": False},
            {"total_tokens": 1},
            "model",
        ),
    )
    result = LLMJudge(
        model="judge",
        auth="none",
        base_url="http://127.0.0.1:1",
        response_mode="json_schema",
    )(
        EvaluationContext(
            subject={"input": "task", "expected": "answer", "actual": "answer"}
        )
    )
    assert result.status is EvaluationStatus.PASSED
    assert "OPENAI_API_KEY" not in lookups


def test_registered_required_failure_is_saved_before_raise(monkeypatch):
    monkeypatch.setenv("KEY", "secret")
    monkeypatch.setattr(
        "m3.judges._openai_sync",
        lambda *_: ({"score": 0, "rationale": "wrong", "abstain": False}, {}, "judge"),
    )
    from m3.evaluations import RequiredEvaluationError
    from m3.sync_api import MCPTestKit

    kit = MCPTestKit()
    judge = LLMJudge(model="judge", api_key_env="KEY")
    kit.register_evaluator("answer.v1", judge)
    try:
        with pytest.raises(RequiredEvaluationError):
            kit.evaluate(
                {"input": "task", "expected": "answer", "actual": "wrong"},
                "answer.v1",
                required=True,
                case_id="case-1",
            )
        saved = kit.evaluation_results()[0]
        assert saved.status is EvaluationStatus.FAILED
        assert saved.context is not None and saved.context.subject["input"] == "task"
        assert saved.provenance is not None and saved.provenance.kind == "llm_judge"
    finally:
        kit.close()


def test_sqlite_judge_reopen_keeps_safe_fields_and_digest(tmp_path):
    from m3.storage import SQLiteExecutionStore
    from m3.sync_api import MCPTestKit
    from m3.types import ExecutionId, ExecutionState, RunId

    path = tmp_path / "judge.sqlite"
    store = SQLiteExecutionStore(path)
    execution_id = ExecutionId("judge-execution")
    store.create(ExecutionState(execution_id=execution_id, run_id=RunId("judge-run")))
    secret = "vendor-secret"
    raw_input = "raw-user-question-7f2a"
    raw_expected = "raw-reference-answer-91bc"
    raw_actual = "raw-agent-answer-44de"
    import os

    os.environ["MY_VENDOR_JUDGE_SECRET"] = secret
    original = __import__("m3.judges", fromlist=["_openai_sync"])._openai_sync
    try:
        import m3.judges as judges

        judges._openai_sync = lambda *_: (
            {
                "score": 0.9,
                "rationale": f"{raw_input} {raw_expected} {raw_actual} {secret}",
                "abstain": False,
            },
            {"total_tokens": 3, f"token_{raw_input}": secret, "echo": secret},
            f"model-{raw_input}-{secret}",
        )
        kit = MCPTestKit(store=store)
        result = kit.judge_response(
            name="answer.v1",
            input=raw_input,
            expected=raw_expected,
            actual=raw_actual,
            judge=LLMJudge(model="judge", api_key_env="MY_VENDOR_JUDGE_SECRET"),
            execution_id=execution_id,
            case_id="case-1",
        )
        assert secret not in str(result.model_dump())
        kit.close()
    finally:
        judges._openai_sync = original
        os.environ.pop("MY_VENDOR_JUDGE_SECRET", None)
    reopened = SQLiteExecutionStore(path)
    record = reopened.evaluations(execution_id)[0]
    assert record.score == 0.9 and record.provenance is not None
    assert record.subject_digest is not None and secret not in str(record.model_dump())
    raw = path.read_bytes()
    for value in (secret, raw_input, raw_expected, raw_actual):
        assert value.encode() not in raw
    assert b"Authorization" not in raw
    evaluation_json = reopened.evaluation_json(execution_id)[0]
    report = reopened.get_report(execution_id)
    assert report is not None and report.evaluations
    assert evaluation_json["score"] == 0.9
    for value in (secret, raw_input, raw_expected, raw_actual):
        assert value not in str(evaluation_json)
        assert value not in str(report.model_dump())
    from m3.feedback import build_feedback, export_feedback

    feedback = build_feedback(reopened, "judge-run")
    output = export_feedback(feedback, reopened, tmp_path / "feedback")
    exported = "".join(path.read_text() for path in output.rglob("*.json"))
    for value in (secret, raw_input, raw_expected, raw_actual):
        assert value not in exported
    assert "actual" not in exported


def test_error_is_counted_but_excluded_from_pass_rate():
    from m3.feedback import _evaluation_stats
    from m3.types import EvaluationId, EvaluationRecord, ExecutionId

    records = (
        EvaluationRecord(
            evaluation_id=EvaluationId("p"),
            execution_id=ExecutionId("e"),
            name="j",
            status=EvaluationStatus.PASSED,
            score=1,
        ),
        EvaluationRecord(
            evaluation_id=EvaluationId("x"),
            execution_id=ExecutionId("e"),
            name="j",
            status=EvaluationStatus.ERROR,
        ),
    )
    stats = _evaluation_stats(records)
    assert stats["status_counts"]["error"] == 1
    assert stats["pass_rate"] == 1.0
    assert stats["measured_count"] == 1


def test_required_error_is_persisted_before_raise():
    from m3.evaluations import RequiredEvaluationError
    from m3.sync_api import MCPTestKit

    kit = MCPTestKit()
    kit.register_evaluator(
        "missing-key.v1", LLMJudge(model="judge", api_key_env="MISSING_JUDGE_KEY")
    )
    try:
        with pytest.raises(RequiredEvaluationError):
            kit.evaluate(
                {"input": "task", "expected": "answer", "actual": "answer"},
                "missing-key.v1",
                required=True,
            )
        assert kit.evaluation_results()[0].status is EvaluationStatus.ERROR
    finally:
        kit.close()


def test_sync_kit_retains_judge_cap_for_portal_runtime():
    from m3.sync_api import MCPTestKit, _SyncPortal

    kit = MCPTestKit(max_judge_requests=1)
    portal = _SyncPortal(kit.config, 5.0, 64 * 1024, max_judge_requests=1)
    try:
        assert portal._runtime.kit._evaluations._judge_budget.limit == 1
    finally:
        portal.close()
        kit.close()


@pytest.mark.parametrize("actual", [None, 3])
def test_actual_type_is_rejected_without_request(monkeypatch, actual):
    monkeypatch.setenv("KEY", "secret")
    monkeypatch.setattr(
        "m3.judges._openai_sync", lambda *_: pytest.fail("request made")
    )
    result = LLMJudge(model="judge", api_key_env="KEY")(
        EvaluationContext(
            subject={"input": "task", "expected": "answer", "actual": actual}
        )
    )
    assert result.details["error_code"] == "judge_input_invalid"


def test_combined_subject_limit_is_rejected_without_request(monkeypatch):
    monkeypatch.setenv("KEY", "secret")
    monkeypatch.setattr(
        "m3.judges._openai_sync", lambda *_: pytest.fail("request made")
    )
    result = LLMJudge(model="judge", api_key_env="KEY")(
        EvaluationContext(
            subject={
                "input": "x" * 24000,
                "expected": "y" * 24000,
                "actual": "z" * 20000,
            }
        )
    )
    assert result.details["error_code"] == "judge_input_invalid"


def test_helper_reuses_same_registration_and_rejects_conflict(monkeypatch):
    monkeypatch.setenv("KEY", "secret")
    monkeypatch.setattr(
        "m3.judges._openai_sync",
        lambda *_: ({"score": 1, "rationale": "ok", "abstain": False}, {}, "judge"),
    )
    kit = MCPTestKit()
    try:
        first = kit.judge_response(
            name="answer.v1",
            input="task",
            expected="answer",
            actual="answer",
            judge=LLMJudge(model="judge", api_key_env="KEY"),
        )
        second = kit.judge_response(
            name="answer.v1",
            input="task",
            expected="answer",
            actual="answer",
            judge=LLMJudge(model="judge", api_key_env="KEY"),
        )
        assert first.status is second.status is EvaluationStatus.PASSED
        with pytest.raises(ValueError, match="conflicting"):
            kit.judge_response(
                name="answer.v1",
                input="task",
                expected="answer",
                actual="answer",
                judge=LLMJudge(model="other", api_key_env="KEY"),
            )
    finally:
        kit.close()


def test_sqlite_budget_is_shared_and_atomic(tmp_path):
    path = tmp_path / "budget.sqlite"
    first, second = SQLiteExecutionStore(path), SQLiteExecutionStore(path)
    with ThreadPoolExecutor(max_workers=8) as pool:

        def reserve(index: int) -> bool:
            store = first if index % 2 == 0 else second
            return store.reserve_judge_request("run", 5)

        results = list(pool.map(reserve, range(8)))
    assert sum(results) == 5
    assert first.reserve_judge_request("run", 5) is False
