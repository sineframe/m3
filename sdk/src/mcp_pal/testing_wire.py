"""Small, deliberately boring stdio fault fixture for tests.

This module is launched by :meth:`FaultInjector.stdio_server`.  It speaks the
official MCP stdio newline-delimited JSON framing directly so malformed and
truncated bytes are real transport faults, while the production client and
transport remain entirely authoritative.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from threading import Lock, Thread, Timer
from typing import Any
from urllib.parse import urlparse

_MAX_CONFIG_BYTES = 64 * 1024
_MAX_OVERSIZED_BYTES = 64 * 1024 * 1024
_INVALID_RESULT_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


def _load_config() -> dict[str, Any]:
    import os

    raw = os.environ.get("MCP_PAL_WIRE_FAULTS", "{}")
    if len(raw.encode("utf-8")) > _MAX_CONFIG_BYTES:
        raise SystemExit(2)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise SystemExit(2) from None
    if not isinstance(value, dict):
        raise SystemExit(2)
    return value


def _method_fault(config: Mapping[str, Any], method: str, field: str) -> bool:
    values = config.get(field, [])
    if isinstance(values, Mapping):
        return method in values
    return isinstance(values, list) and method in values


def _error_frame(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


class _WireServer:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.write_lock = asyncio.Lock()
        self.reorder_lock = asyncio.Lock()
        self.pending_frame: tuple[str, bytes] | None = None
        self.flush_task: asyncio.Task[None] | None = None
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.request_tasks: dict[str, asyncio.Task[None]] = {}

    async def write_bytes(self, data: bytes) -> None:
        async with self.write_lock:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    async def write_frame(self, method: str, frame: dict[str, Any]) -> None:
        encoded = (
            json.dumps(frame, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
        if _method_fault(self.config, method, "partial_methods"):
            await self.write_bytes(encoded[: max(1, len(encoded) // 2)])
            raise SystemExit(0)
        if _method_fault(self.config, method, "malformed_methods"):
            await self.write_bytes(b"{not-valid-json\n")
            raise SystemExit(0)
        if _method_fault(self.config, method, "disconnect_methods"):
            raise SystemExit(0)
        if _method_fault(self.config, method, "process_crash_methods"):
            raise SystemExit(17)
        if _method_fault(self.config, method, "protocol_errors"):
            configured = self.config["protocol_errors"].get(method, {})
            code = (
                int(configured.get("code", -32000))
                if isinstance(configured, dict)
                else -32000
            )
            frame = _error_frame(frame.get("id"), code, "injected protocol error")
            encoded = (json.dumps(frame, separators=(",", ":")) + "\n").encode("utf-8")
        if _method_fault(self.config, method, "oversized_methods"):
            configured_size = self.config["oversized_methods"].get(method, 1)
            size = int(configured_size) if isinstance(configured_size, int) else 1
            if size < 1 or size > _MAX_OVERSIZED_BYTES:
                raise SystemExit(2)
            result = frame.get("result")
            if isinstance(result, dict):
                content = result.get("content")
                if isinstance(content, list):
                    content.append({"type": "text", "text": "x" * size})
                    encoded = (json.dumps(frame, separators=(",", ":")) + "\n").encode(
                        "utf-8"
                    )
        if _method_fault(self.config, method, "reordered_methods"):
            async with self.reorder_lock:
                if self.pending_frame is None:
                    self.pending_frame = (method, encoded)
                    self.flush_task = asyncio.create_task(self.flush_reordered())
                    return
                _, first = self.pending_frame
                self.pending_frame = None
                if self.flush_task is not None:
                    self.flush_task.cancel()
                await self.write_bytes(encoded)
                await self.write_bytes(first)
                return
        await self.write_bytes(encoded)
        if _method_fault(self.config, method, "duplicate_id_methods"):
            await self.write_bytes(encoded)

    async def flush_reordered(self) -> None:
        await asyncio.sleep(0.02)
        async with self.reorder_lock:
            if self.pending_frame is not None:
                _, frame = self.pending_frame
                self.pending_frame = None
                await self.write_bytes(frame)

    async def handle(self, request: dict[str, Any]) -> None:
        method = request.get("method")
        request_id = request.get("id")
        if not isinstance(method, str) or "id" not in request:
            return
        key = json.dumps(request_id, sort_keys=True)
        self.cancel_events.setdefault(key, asyncio.Event())
        if _method_fault(self.config, method, "cancel_before_methods"):
            raise SystemExit(0)
        delays = self.config.get("delays", {})
        if isinstance(delays, Mapping):
            delay = delays.get(method)
            if isinstance(delay, (int, float)) and delay > 0:
                await asyncio.sleep(float(delay))
        race = _method_fault(self.config, method, "race_methods")
        if race:
            event = self.cancel_events[key]
            try:
                await asyncio.wait_for(event.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass
            if event.is_set():
                return
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-pal-wire-fault", "version": "1"},
            }
        elif method == "tools/list":
            tool: dict[str, Any] = {
                "name": "echo",
                "description": "Echo",
                "inputSchema": {"type": "object"},
            }
            if _method_fault(self.config, "tools/call", "invalid_result_methods"):
                tool["outputSchema"] = dict(_INVALID_RESULT_SCHEMA)
            result = {"tools": [tool]}
        elif method == "tools/call":
            params = request.get("params")
            arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
            result = {
                "content": [{"type": "text", "text": str(arguments.get("text", "ok"))}]
            }
            if _method_fault(self.config, "tools/call", "invalid_result_methods"):
                result["structuredContent"] = {"value": "invalid structured result"}
        elif method == "ping":
            result = {}
        else:
            await self.write_frame(
                method, _error_frame(request_id, -32601, "method not found")
            )
            return
        await self.write_frame(
            method, {"jsonrpc": "2.0", "id": request_id, "result": result}
        )

    async def run(self) -> None:
        while True:
            raw = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not raw:
                return
            try:
                request = json.loads(raw)
            except (TypeError, ValueError):
                # A malformed client request is not a server fault; keep the
                # process alive for the next test request.
                continue
            if not isinstance(request, dict):
                continue
            if request.get("method") == "notifications/cancelled":
                params = request.get("params")
                request_id = (
                    params.get("requestId") if isinstance(params, dict) else None
                )
                key = json.dumps(request_id, sort_keys=True)
                self.cancel_events.setdefault(key, asyncio.Event()).set()
                task = self.request_tasks.get(key)
                if task is not None:
                    task.cancel()
                continue
            task = asyncio.create_task(self.handle(request))
            request_id = request.get("id")
            if request_id is not None:
                self.request_tasks[json.dumps(request_id, sort_keys=True)] = task


class _SSEFixture:
    """Minimal SSE endpoint using the same raw JSON fault semantics."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.messages: Queue[bytes | None] = Queue()
        self.lock = Lock()
        self.connections: set[Any] = set()
        self.pending: bytes | None = None
        self.timer: Timer | None = None
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                with fixture.lock:
                    fixture.connections.add(self.connection)

            def finish(self) -> None:
                try:
                    super().finish()
                finally:
                    with fixture.lock:
                        fixture.connections.discard(self.connection)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                if urlparse(self.path).path != "/sse":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(
                    b"event: endpoint\ndata: /messages?session_id=fixture\n\n"
                )
                self.wfile.flush()
                while True:
                    payload = fixture.messages.get()
                    if payload is None:
                        return
                    self.wfile.write(payload)
                    self.wfile.flush()

            def do_POST(self) -> None:
                if urlparse(self.path).path != "/messages":
                    self.send_error(404)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    request = json.loads(self.rfile.read(size))
                except (TypeError, ValueError):
                    request = None
                self.send_response(202)
                self.end_headers()
                if isinstance(request, dict):
                    fixture.enqueue(request)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(
            target=self.server.serve_forever, name="mcp-pal-sse-fixture", daemon=True
        )
        self.thread.start()

    def _fault(self, method: str, field: str) -> bool:
        values = self.config.get(field, [])
        return method in values if isinstance(values, (list, dict)) else False

    def _event(self, encoded: bytes) -> bytes:
        return b"event: message\ndata: " + encoded + b"\n\n"

    def enqueue(self, request: dict[str, Any]) -> None:
        method = request.get("method")
        request_id = request.get("id")
        if not isinstance(method, str) or "id" not in request:
            return
        if self._fault(method, "disconnect_methods") or self._fault(
            method, "process_crash_methods"
        ):
            self.messages.put(None)
            return
        if self._fault(method, "partial_methods"):
            self.messages.put(b'event: message\ndata: {"jsonrpc":"2.0","id":')
            self.messages.put(None)
            return
        if self._fault(method, "malformed_methods"):
            self.messages.put(b"event: message\ndata: {bad-json\n\n")
            self.messages.put(None)
            return
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-pal-sse-fault", "version": "1"},
            }
        elif method == "tools/list":
            tool: dict[str, Any] = {
                "name": "echo",
                "description": "Echo",
                "inputSchema": {"type": "object"},
            }
            if self._fault("tools/call", "invalid_result_methods"):
                tool["outputSchema"] = dict(_INVALID_RESULT_SCHEMA)
            result = {"tools": [tool]}
        elif method == "tools/call":
            params = request.get("params")
            arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
            result = {
                "content": [{"type": "text", "text": str(arguments.get("text", "ok"))}]
            }
            if self._fault("tools/call", "invalid_result_methods"):
                result["structuredContent"] = {"value": "invalid structured result"}
        else:
            result = {}
        if method == "initialize" or method in {"tools/list", "tools/call", "ping"}:
            frame: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        else:
            frame = _error_frame(request_id, -32601, "method not found")
        if self._fault(method, "protocol_errors"):
            configured = self.config.get("protocol_errors", {}).get(method, {})
            code = (
                int(configured.get("code", -32000))
                if isinstance(configured, dict)
                else -32000
            )
            frame = _error_frame(request_id, code, "injected protocol error")
        if self._fault(method, "oversized_methods"):
            size = self.config["oversized_methods"].get(method, 1)
            if isinstance(size, int) and 1 <= size <= _MAX_OVERSIZED_BYTES:
                frame.setdefault("result", {}).setdefault("content", []).append(
                    {"type": "text", "text": "x" * size}
                )
        encoded = (json.dumps(frame, separators=(",", ":")) + "\n").encode("utf-8")
        event = self._event(encoded)
        with self.lock:
            if self._fault(method, "reordered_methods") and self.pending is not None:
                self.messages.put(event)
                self.messages.put(self.pending)
                self.pending = None
            elif self._fault(method, "reordered_methods"):
                self.pending = event
                self.timer = Timer(0.02, self.flush_pending)
                self.timer.daemon = True
                self.timer.start()
            else:
                self.messages.put(event)
                if self._fault(method, "duplicate_id_methods"):
                    self.messages.put(event)

    def flush_pending(self) -> None:
        with self.lock:
            if self.pending is not None:
                self.messages.put(self.pending)
                self.pending = None

    def close(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
        self.messages.put(None)
        with self.lock:
            connections = tuple(self.connections)
        for connection in connections:
            try:
                connection.shutdown(2)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def start_sse_fixture(config: dict[str, Any]) -> tuple[str, Any]:
    fixture = _SSEFixture(config)
    return f"http://127.0.0.1:{fixture.server.server_port}/sse", fixture.close


def main() -> None:
    try:
        asyncio.run(_WireServer(_load_config()).run())
    except BrokenPipeError:
        return


if __name__ == "__main__":
    main()
