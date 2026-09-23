"""Local OpenAI-compatible provider used by the runnable Pi examples."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

JsonObject = dict[str, object]


class ProviderRun:
    def __init__(self) -> None:
        self.requests: list[JsonObject] = []


@contextmanager
def open_provider(run: ProviderRun) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            request = json.loads(self.rfile.read(length))
            run.requests.append(request)
            messages = request.get("messages", [])
            user_content = next(
                (
                    item.get("content", "")
                    for item in reversed(messages)
                    if item.get("role") == "user"
                ),
                "",
            )
            user_text = (
                " ".join(
                    str(part.get("text", ""))
                    for part in user_content
                    if isinstance(part, dict)
                )
                if isinstance(user_content, list)
                else str(user_content)
            )
            marker = "m3-gate:"
            requested_tool = (
                user_text.split(marker, 1)[1].split()[0]
                if marker in user_text
                else None
            )
            if requested_tool is None and "delivery address" in user_text.lower():
                requested_tool = "book_shipment"
            tool_call = None
            current_turn = messages[
                1
                + max(
                    (
                        index
                        for index, item in enumerate(messages)
                        if item.get("role") == "user"
                    ),
                    default=-1,
                ) :
            ]
            if requested_tool and not any(
                item.get("role") == "tool" for item in current_turn
            ):
                for item in request.get("tools", []):
                    function = item.get("function", {})
                    description = str(function.get("description", ""))
                    if (
                        requested_tool[:20] in function.get("name", "")
                        or requested_tool in description
                    ):
                        if requested_tool == "book_verified_shipment":
                            prompt = user_text.lower()
                            if "both addresses" in prompt:
                                kind = "both"
                            elif "without an address" in prompt:
                                kind = "none"
                            elif "business" in prompt:
                                kind = "business"
                            else:
                                kind = "home"
                            arguments = f'{{"address_kind":"{kind}"}}'
                        elif requested_tool in {"book_shipment", "shipping_quote"}:
                            arguments = '{"weight_kg":2,"zone":"local"}'
                        else:
                            arguments = "{}"
                        tool_call = {
                            "index": 0,
                            "id": f"m3-call-{len(run.requests)}",
                            "type": "function",
                            "function": {
                                "name": function["name"],
                                "arguments": arguments,
                            },
                        }
                        break
            if tool_call is not None:
                self._send_events(
                    [
                        {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "tool_calls": [tool_call],
                                    },
                                    "finish_reason": "tool_calls",
                                }
                            ]
                        }
                    ]
                )
                return
            self._send_events(
                [
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ]
            )

        def _send_events(self, events: list[JsonObject]) -> None:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.end_headers()
            for event in events:
                payload = {
                    "id": f"m3-response-{len(run.requests)}",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "fixture-model",
                    **event,
                }
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


__all__ = ["ProviderRun", "open_provider"]
