"""Live local transport matrix through the public async direct-client API."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.errors import OperationCancelled
from mcp_pal.transport.direct import TransportConnectionError
from mcp_pal.types import (
    HTTPServer,
    SecretReference,
    SSEServer,
    StdioServer,
    TrustLevel,
)

pytestmark = pytest.mark.process_lifecycle

_PROCESS_MARKER_TIMEOUT = 30.0


_PROTOCOL = "2025-11-25"


def _jsonrpc_response(request: dict[str, Any], result: dict[str, Any]) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request["id"], "result": result},
        separators=(",", ":"),
    ).encode()


class _LiveSSEFixture:
    """Small real TCP server implementing only the official SSE wire shape."""

    def __init__(self, *, token: str) -> None:
        self.token = token
        self.initialized = asyncio.Event()
        self.closed = asyncio.Event()
        self._sse_writer: asyncio.StreamWriter | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._sse_lock = asyncio.Lock()

    async def _read_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, str], bytes]:
        header_bytes = await reader.readuntil(b"\r\n\r\n")
        lines = header_bytes[:-4].split(b"\r\n")
        method, target, _version = lines[0].decode().split(" ", 2)
        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, value = line.decode().split(":", 1)
            headers[name.lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        body = await reader.readexactly(length) if length else b""
        return method, target, headers, body

    @staticmethod
    async def _response(
        writer: asyncio.StreamWriter,
        status: str,
        body: bytes = b"",
        *,
        content_type: str = "application/json",
    ) -> None:
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()

    async def _send_message(self, payload: bytes) -> None:
        writer = self._sse_writer
        if writer is None or writer.is_closing():
            return
        async with self._sse_lock:
            writer.write(b"event: message\ndata: " + payload + b"\n\n")
            await writer.drain()

    async def __call__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writers.add(writer)
        try:
            method, target, headers, body = await self._read_request(reader)
            if headers.get("authorization") != f"Bearer {self.token}":
                await self._response(writer, "401 Unauthorized")
                return
            if method == "GET" and target.split("?", 1)[0] == "/sse":
                self._sse_writer = writer
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Cache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n"
                    b"event: endpoint\ndata: /messages?session_id=fixture\n\n"
                )
                await writer.drain()
                await self.closed.wait()
                return
            if method != "POST" or target != "/messages?session_id=fixture":
                await self._response(writer, "404 Not Found")
                return
            request = json.loads(body)
            await self._response(writer, "202 Accepted")
            if request.get("method") == "initialize":
                self.initialized.set()
                await self._send_message(
                    _jsonrpc_response(
                        request,
                        {
                            "protocolVersion": _PROTOCOL,
                            "capabilities": {},
                            "serverInfo": {"name": "live-sse", "version": "1"},
                        },
                    )
                )
            elif request.get("method") == "tools/list":
                await self._send_message(
                    _jsonrpc_response(
                        request,
                        {
                            "tools": [
                                {
                                    "name": "fixture_tool",
                                    # Deliberately echo the bearer under an
                                    # ordinary field so the trace boundary is
                                    # tested rather than only header metadata.
                                    "description": self.token,
                                    "inputSchema": {"type": "object", "properties": {}},
                                }
                            ]
                        },
                    )
                )
        except (asyncio.IncompleteReadError, ConnectionError):
            return
        finally:
            self._writers.discard(writer)
            if writer is not self._sse_writer:
                writer.close()
                await writer.wait_closed()

    async def aclose(self) -> None:
        self.closed.set()
        for writer in tuple(self._writers):
            writer.close()
        for writer in tuple(self._writers):
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass


class _BearerAuth(httpx2.Auth):
    """Deterministic custom auth fixture using the official httpx2 hook."""

    def auth_flow(self, request: httpx2.Request) -> Any:
        request.headers["Authorization"] = "Bearer fixture-token"
        yield request


async def _start_server(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
) -> tuple[asyncio.AbstractServer, int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    socket = server.sockets[0]
    return server, int(socket.getsockname()[1])


@pytest.mark.asyncio
async def test_streamable_http_live_matrix_with_bearer_and_tools() -> None:
    requests: list[tuple[str, str, dict[str, str]]] = []

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            header_bytes = await reader.readuntil(b"\r\n\r\n")
            lines = header_bytes[:-4].split(b"\r\n")
            method, target, _version = lines[0].decode().split(" ", 2)
            headers = {
                line.decode().split(":", 1)[0].lower(): line.decode()
                .split(":", 1)[1]
                .strip()
                for line in lines[1:]
            }
            length = int(headers.get("content-length", "0"))
            body = await reader.readexactly(length) if length else b""
            request = json.loads(body or b"{}")
            requests.append((method, target, headers))
            if headers.get("authorization") != "Bearer fixture-token":
                body_out = b"unauthorized"
                status = "401 Unauthorized"
            elif request.get("method") == "initialize":
                body_out = _jsonrpc_response(
                    request,
                    {
                        "protocolVersion": _PROTOCOL,
                        "capabilities": {},
                        "serverInfo": {"name": "live-http", "version": "1"},
                    },
                )
                status = "200 OK"
            elif request.get("method") == "tools/list":
                body_out = _jsonrpc_response(
                    request,
                    {
                        "tools": [
                            {"name": "fixture_tool", "inputSchema": {"type": "object"}}
                        ]
                    },
                )
                status = "200 OK"
            else:
                body_out = b"{}"
                status = "202 Accepted"
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body_out)}\r\nConnection: close\r\n\r\n".encode()
                + body_out
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await _start_server(handler)
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    try:
        binding = HTTPServer(
            name="live-http",
            url=f"http://127.0.0.1:{port}/mcp",
            headers={"X-Fixture": "live"},
            trust=TrustLevel.TRUSTED_PRIVATE,
        )
        async with kit.direct(binding, auth=_BearerAuth()) as client:
            assert client.initialization is not None
            assert client.initialization.server_info["name"] == "live-http"
            assert (await client.list_tools()).tools[0].name == "fixture_tool"
            assert client.trace is not None
            assert any(
                event.kind.value == "mcp.request" for event in client.trace.events
            )
            assert any(
                event.kind.value == "mcp.response" for event in client.trace.events
            )
            assert "fixture-token" not in repr(client.trace)
            assert "fixture-token" not in repr(client.trace.model_dump(mode="json"))
            assert client.transport_evidence is not None
            assert client.transport_evidence.state == "initialized"
        assert client.transport_evidence.state == "closed"
        assert client.final_trace is not None
        assert client.final_trace.completeness == "partial"
        assert any(
            request[2].get("authorization") == "Bearer fixture-token"
            for request in requests
        )
        assert "fixture-token" not in repr(client.transport_evidence)
    finally:
        await kit.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_sse_live_matrix_uses_bearer_secret_and_closes_cleanly() -> None:
    fixture = _LiveSSEFixture(token="fixture-token")
    server, port = await _start_server(fixture)
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    try:
        binding = SSEServer(
            name="live-sse",
            url=f"http://127.0.0.1:{port}/sse",
            headers={"X-Fixture": "live"},
            trust=TrustLevel.TRUSTED_PRIVATE,
        )
        reference = SecretReference(source="provider", name="fixture-token")

        class Resolver:
            def resolve(self, value: SecretReference) -> str:
                assert value == reference
                return "fixture-token"

        async with kit.direct(
            binding, secret_resolver=Resolver(), bearer_token=reference
        ) as client:
            assert client.initialization is not None
            assert client.initialization.server_info["name"] == "live-sse"
            assert (await client.list_tools()).tools[0].name == "fixture_tool"
            assert client.trace is not None
            assert any(
                event.kind.value == "mcp.request" for event in client.trace.events
            )
            assert any(
                event.kind.value == "mcp.response" for event in client.trace.events
            )
            assert client.transport_evidence is not None
            assert client.transport_evidence.state == "initialized"
        assert client.transport_evidence.state == "closed"
        assert client.final_trace is not None
        assert fixture.initialized.is_set()
        assert "fixture-token" not in repr(client.transport_evidence)
        assert "fixture-token" not in repr(client.final_trace)
        assert client._bearer_token is None
        assert client._secret_resolver is None
    finally:
        await kit.aclose()
        await fixture.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_remote_auth_rejection_retains_sanitized_partial_evidence() -> None:
    fixture = _LiveSSEFixture(token="expected-token")
    server, port = await _start_server(fixture)
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    try:
        binding = SSEServer(
            name="rejected-sse",
            url=f"http://127.0.0.1:{port}/sse",
            trust=TrustLevel.TRUSTED_PRIVATE,
        )
        client = kit.direct(
            binding,
            bearer_token=SecretReference(source="provider", name="wrong-secret"),
        )
        with pytest.raises(TransportConnectionError) as caught:
            await client.__aenter__()
        assert caught.value.evidence is not None
        assert caught.value.evidence.state == "failed"
        evidence = client.transport_evidence
        assert evidence is not None
        assert evidence.state == "failed"
        assert evidence.error_type == "TransportError"
        assert "wrong-secret" not in repr(evidence)
        assert "wrong-secret" not in str(evidence)
        assert client.final_trace is not None
        assert client.final_trace.events[-1].kind.value == "execution.finished"
        assert client.final_trace.events[-1].payload["outcome"] == "failed"
    finally:
        await kit.aclose()
        await fixture.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_remote_initialization_cancellation_closes_transport() -> None:
    initialized = asyncio.Event()
    release = asyncio.Event()
    handler_done = asyncio.Event()

    async def hanging_handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            initialized.set()
            await release.wait()
        except (asyncio.CancelledError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            handler_done.set()

    server, port = await _start_server(hanging_handler)
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(
        HTTPServer(
            name="hanging-http",
            url=f"http://127.0.0.1:{port}/mcp",
            trust=TrustLevel.TRUSTED_PRIVATE,
        )
    )
    task = asyncio.create_task(client.__aenter__())
    try:
        await asyncio.wait_for(initialized.wait(), timeout=2.0)
        task.cancel()
        with pytest.raises(OperationCancelled):
            await task
        evidence = client.transport_evidence
        assert evidence is not None
        assert evidence.state in {"failed", "closed"}
        assert client.final_trace is not None
        assert client.final_trace.events[-1].payload["outcome"] == "cancelled"
    finally:
        await kit.aclose()
        release.set()
        await asyncio.wait_for(handler_done.wait(), timeout=2.0)
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process liveness assertion")
async def test_stdio_initialization_cancellation_reaps_owned_process(
    tmp_path: Path,
) -> None:
    """Cancellation during official stdio initialization leaves no child."""

    marker = tmp_path / "stdio.pid"
    fixture = Path(__file__).parents[1] / "fixtures" / "hanging_stdio_server.py"
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    client = kit.direct(
        StdioServer(
            name="hanging-stdio",
            command=os.sys.executable,
            args=("-u", str(fixture)),
            environment={"MCP_PAL_E2E_PID_FILE": str(marker)},
        ),
        timeout=30,
    )
    entering = asyncio.create_task(client.__aenter__())
    try:
        deadline = time.monotonic() + _PROCESS_MARKER_TIMEOUT
        while not marker.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert marker.exists(), "stdio fixture did not start"
        entering.cancel()
        with pytest.raises(OperationCancelled):
            await asyncio.wait_for(entering, timeout=_PROCESS_MARKER_TIMEOUT)
        assert client.final_trace is not None
        assert client.final_trace.events[-1].payload["outcome"] == "cancelled"
        pid = int(marker.read_text(encoding="utf-8"))
        deadline = time.monotonic() + _PROCESS_MARKER_TIMEOUT
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError(f"stdio child {pid} survived cancellation cleanup")
    finally:
        await kit.aclose()
