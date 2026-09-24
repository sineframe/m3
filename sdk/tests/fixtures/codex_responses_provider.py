"""Deterministic local Responses API provider for real Codex app-server tests.

The provider has no model or credential dependency. Tests enqueue exact model
outputs, and this server records the requests Codex actually sends before
returning them as Responses API server-sent events.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ResponsesRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    body: JsonObject


@dataclass(frozen=True)
class ResponsesResponse:
    """HTTP status and exact body sent for one local provider response."""

    status: int
    body: str


@dataclass(frozen=True)
class ModelOutput:
    """One deterministic completed model response."""

    function_name: str | None = None
    arguments: Mapping[str, Any] | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        has_function = self.function_name is not None
        has_text = self.text is not None
        if has_function == has_text:
            raise ValueError("choose exactly one of function_name or text")
        if has_function and self.arguments is None:
            raise ValueError("function outputs require arguments")


class ResponsesRun:
    """Queued model outputs and exact request evidence for one Codex run."""

    def __init__(self) -> None:
        self._outputs: queue.Queue[ModelOutput] = queue.Queue()
        self._requests: list[ResponsesRequest] = []
        self._responses: list[ResponsesResponse] = []
        self._lock = threading.Lock()

    def enqueue(self, output: ModelOutput) -> None:
        self._outputs.put(output)

    @property
    def requests(self) -> tuple[ResponsesRequest, ...]:
        with self._lock:
            return tuple(self._requests)

    @property
    def responses(self) -> tuple[ResponsesResponse, ...]:
        with self._lock:
            return tuple(self._responses)

    def _record(self, request: ResponsesRequest) -> None:
        with self._lock:
            self._requests.append(request)

    def _record_response(self, response: ResponsesResponse) -> None:
        with self._lock:
            self._responses.append(response)

    def _next_output(self) -> ModelOutput:
        return self._outputs.get_nowait()


@contextmanager
def open_codex_responses_provider(
    run: ResponsesRun,
) -> Iterator[str]:
    """Serve a local OpenAI Responses-compatible endpoint and yield its base URL."""

    class HttpServer(ThreadingHTTPServer):
        daemon_threads = True

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            if self.path.endswith("/models"):
                self._send_json(
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": "m3-fixture-model",
                                "object": "model",
                                "created": 0,
                                "owned_by": "m3-tests",
                            }
                        ],
                    }
                )
                return
            self.send_error(404, "unexpected GET")

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                self.send_error(400, "request body must be an object")
                return
            request = ResponsesRequest(
                method=self.command,
                path=self.path,
                headers={key.lower(): value for key, value in self.headers.items()},
                body=body,
            )
            run._record(request)
            if not self.path.endswith("/responses"):
                self.send_error(404, "unexpected POST")
                return
            try:
                output = run._next_output()
            except queue.Empty:
                run._record_response(
                    ResponsesResponse(500, "no deterministic output was queued")
                )
                self.send_error(500, "no deterministic output was queued")
                return
            response_id = f"m3-response-{len(run.requests)}"
            try:
                events = _events(response_id, output, request)
            except AssertionError as exc:
                run._record_response(ResponsesResponse(500, str(exc)))
                self.send_error(500, str(exc))
                return
            payload = "".join(
                f"event: {event['type']}\ndata: "
                f"{json.dumps(event, separators=(',', ':'))}\n\n"
                for event in events
            )
            run._record_response(ResponsesResponse(200, payload))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.end_headers()
            for event in events:
                data = json.dumps(event, separators=(",", ":"))
                self.wfile.write(f"event: {event['type']}\ndata: {data}\n\n".encode())
                self.wfile.flush()

        def _send_json(self, payload: JsonObject) -> None:
            content = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = HttpServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="m3-codex-provider")
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def function_tools(request: ResponsesRequest) -> tuple[str, ...]:
    """Return names from top-level and namespaced Responses function tools."""

    functions = _functions(request)
    return tuple(
        name if namespace is None else f"{namespace}::{name}"
        for namespace, name in functions
    )


def _functions(request: ResponsesRequest) -> tuple[tuple[str | None, str], ...]:
    tools = request.body.get("tools")
    if not isinstance(tools, list):
        return ()
    functions: list[tuple[str | None, str]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("name"), str):
            functions.append((None, tool["name"]))
        if tool.get("type") != "namespace" or not isinstance(tool.get("name"), str):
            continue
        namespace = tool["name"]
        children = tool.get("tools")
        if not isinstance(children, list):
            continue
        functions.extend(
            (namespace, child["name"])
            for child in children
            if isinstance(child, dict)
            and child.get("type") == "function"
            and isinstance(child.get("name"), str)
        )
    return tuple(functions)


def _resolve_function(
    request: ResponsesRequest, requested: str
) -> tuple[str | None, str]:
    functions = _functions(request)
    matches = tuple(
        (namespace, name)
        for namespace, name in functions
        if name == requested
        or (namespace is not None and f"{namespace}::{name}" == requested)
        or name.endswith(f"__{requested}")
        or (namespace is not None and namespace.endswith(f"__{requested}"))
    )
    if len(matches) != 1:
        raise AssertionError(
            f"expected one advertised function matching {requested!r}, "
            f"found {matches!r}; all functions: {function_tools(request)!r}"
        )
    return matches[0]


def wait_for_requests(
    run: ResponsesRun, count: int, timeout_s: float = 5.0
) -> tuple[ResponsesRequest, ...]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        requests = run.requests
        if len(requests) >= count:
            return requests
        time.sleep(0.01)
    raise AssertionError(
        f"expected {count} Responses requests, got {len(run.requests)}"
    )


def _events(
    response_id: str, output: ModelOutput, request: ResponsesRequest
) -> tuple[JsonObject, ...]:
    events: list[JsonObject] = [
        {"type": "response.created", "response": {"id": response_id}}
    ]
    item: JsonObject
    if output.function_name is not None:
        namespace, function_name = _resolve_function(request, output.function_name)
        item = {
            "type": "function_call",
            "call_id": f"m3-call-{response_id}",
            "name": function_name,
            "arguments": json.dumps(output.arguments, separators=(",", ":")),
        }
        if namespace is not None:
            item["namespace"] = namespace
    else:
        item = {
            "type": "message",
            "role": "assistant",
            "id": f"m3-message-{response_id}",
            "content": [{"type": "output_text", "text": output.text}],
        }
    events.append({"type": "response.output_item.done", "item": item})
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "usage": {
                    "input_tokens": 1,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens": 1,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": 2,
                },
            },
        }
    )
    return tuple(events)


__all__ = [
    "ModelOutput",
    "ResponsesRequest",
    "ResponsesResponse",
    "ResponsesRun",
    "function_tools",
    "open_codex_responses_provider",
    "wait_for_requests",
]
