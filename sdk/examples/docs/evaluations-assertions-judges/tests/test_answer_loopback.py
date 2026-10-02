import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from m3.judges import LLMJudge
from m3.types import EvaluationStatus

pytestmark = pytest.mark.m3(suite_name="answers-loopback")


def test_judge_defaults() -> None:
    judge = LLMJudge(model="judge-model")

    assert judge.base_url == "https://api.openai.com/v1"
    assert judge.timeout_seconds == 30.0
    assert judge.threshold == 0.8


def test_judge_response_registers_the_judge_and_sends_the_subject(m3_kit) -> None:
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "body": body})
            content = json.dumps({"score": 1.0, "rationale": "match", "abstain": False})
            payload = json.dumps(
                {
                    "id": "completion-1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": content},
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args) -> None:
            pass

    fake = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=fake.serve_forever, daemon=True)
    thread.start()
    try:
        judge = LLMJudge(
            model="local-judge",
            base_url=f"http://127.0.0.1:{fake.server_port}/v1",
            auth="none",
            response_mode="json_schema",
        )
        result = m3_kit.judge_response(
            name="answer.correctness.v1",
            input="What is 2 + 3?",
            actual="The answer is 5.",
            expected="The answer is 5.",
            judge=judge,
            required=True,
        )
    finally:
        fake.shutdown()
        fake.server_close()

    assert result.status is EvaluationStatus.PASSED
    assert [item.name for item in m3_kit.evaluation_results()] == [
        "answer.correctness.v1"
    ]
    assert len(requests) == 1
    assert requests[0]["path"] == "/v1/chat/completions"
    assert requests[0]["body"]["model"] == "local-judge"
    subject = json.loads(requests[0]["body"]["messages"][1]["content"])
    assert subject == {
        "input": "What is 2 + 3?",
        "expected": "The answer is 5.",
        "actual": "The answer is 5.",
    }
