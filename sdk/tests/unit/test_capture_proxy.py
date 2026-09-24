from __future__ import annotations

import asyncio
import io
import json
import os
import socket
import sys
import threading
from collections.abc import AsyncIterator, MutableMapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import types
from mcp.server.lowlevel import Server

from m3._types.specs import AgentSpec
from m3.async_api import AsyncMCPTestKit
from m3.fixtures.echo_http import EchoMcpHttpServer
from m3.harness import HarnessAdapterRegistry
from m3.harness.contracts import (
    DeterministicHarnessAdapter,
    HarnessLaunch,
    HarnessSession,
    HarnessTurnRequest,
)
from m3.server_group import (
    HarnessServerConfig,
    ServerGroupManager,
    _LoopbackEndpoint,
    _SSECaptureParser,
)
from m3.trace.capture import CaptureWriter
from m3.transport.capture_proxy import (
    _MAX_OBSERVATION_FRAME_BYTES,
    McpCaptureManager,
    McpObservation,
    McpObservationIncomplete,
)
from m3.transport.http_proxy import McpHttpProxy
from m3.transport.stdio_proxy import _ObservationChannel, _relay
from m3.types import (
    ClaudeCode,
    HTTPServer,
    InProcessServer,
    SecretReference,
    ServerBinding,
    StdioServer,
    TextContent,
    TransportKind,
    TrustLevel,
    UserMessage,
)


def _stdio_config(
    *,
    key: str,
    connection_id: str,
    command: str = "fixture-child",
    args: tuple[str, ...] = (),
    environment: dict[str, Any] | None = None,
) -> HarnessServerConfig:
    return HarnessServerConfig(
        key=key,
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id=connection_id,
        command=command,
        args=args,
        environment=environment or {},
    )


async def _run_stdio_proxy(
    config: HarnessServerConfig,
    *,
    environment: dict[str, str],
    input_bytes: bytes = b"",
) -> tuple[int, bytes, bytes]:
    runtime_environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
        **environment,
    }
    process = await asyncio.create_subprocess_exec(
        config.command or "",
        *config.args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=runtime_environment,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(input_bytes), timeout=5)
    assert process.returncode is not None
    return process.returncode, stdout, stderr


def _server() -> Server:
    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[types.Tool(name="draw", input_schema={"type": "object"})]
        )

    async def call_tool(
        _context: object, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(text=params.name)])

    return Server("capture-fixture", on_list_tools=list_tools, on_call_tool=call_tool)


def test_capture_correlates_typed_ids_and_tool_latency(tmp_path: Path) -> None:
    manager = McpCaptureManager(tmp_path)
    writer = manager.writer_for("connection-a")
    writer.write(
        transport="stdio",
        direction="client_to_server",
        payload={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "draw", "arguments": {"x": 1}},
        },
    )
    writer.write(
        transport="stdio",
        direction="server_to_client",
        payload={"jsonrpc": "2.0", "id": 7, "result": {"content": [{"text": "ok"}]}},
    )
    writer.write(
        transport="stdio",
        direction="client_to_server",
        payload={
            "jsonrpc": "2.0",
            "id": "7",
            "method": "tools/call",
            "params": {"name": "draw", "arguments": {}},
        },
    )
    writer.write(
        transport="stdio",
        direction="server_to_client",
        payload={
            "jsonrpc": "2.0",
            "id": "7",
            "error": {"code": -1, "message": "failed"},
        },
    )
    snapshot = manager.snapshot("connection-a")
    assert [event.jsonrpc_id for event in snapshot.events] == [7, 7, "7", "7"]
    assert snapshot.events[0].request_sequence == 1
    assert snapshot.events[2].request_sequence == 2
    assert snapshot.events[1].response_to_sequence == 1
    assert snapshot.events[3].response_to_sequence == 2
    assert snapshot.events[1].tool == "draw"
    assert snapshot.events[1].latency_ms is not None
    assert snapshot.events[3].error == {"code": -1, "message": "failed"}


def test_capture_preserves_mrtr_params_for_requests_and_responses(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    writer = manager.writer_for("connection-mrtr")
    params = {
        "name": "book_shipment",
        "arguments": {"weight_kg": 2},
        "requestState": "opaque-state",
        "inputResponses": {
            "shipping_address": {
                "action": "accept",
                "content": {"city": "Pune"},
            }
        },
    }
    writer.write(
        transport="stdio",
        direction="client_to_server",
        payload={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params},
    )
    writer.write(
        transport="stdio",
        direction="server_to_client",
        payload={"jsonrpc": "2.0", "id": 7, "result": {"content": []}},
    )

    request, response = manager.snapshot("connection-mrtr").events
    assert request.params == response.params == params


@pytest.mark.asyncio
async def test_stdio_configuration_is_rewritten_to_transparent_capture_proxy(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    config = HarnessServerConfig(
        key="echo",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="connection-stdio",
        command="echo-server",
        args=("--fixture",),
    )
    instrumented = (await manager.instrument((config,)))[0]
    assert instrumented.command == sys.executable
    assert "m3.transport.stdio_proxy" in instrumented.args
    assert "--" in instrumented.args
    marker = instrumented.args.index("--")
    assert instrumented.args[marker + 1 :] == ("echo-server", "--fixture")
    await manager.close()


@pytest.mark.asyncio
async def test_observer_connect_failure_does_not_block_real_stdio_child(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    child_code = (
        "import json,sys; request=json.loads(sys.stdin.readline()); "
        "print(json.dumps({'jsonrpc':'2.0','id':request['id'],"
        "'result':{'ok':True}},separators=(',',':')),flush=True)"
    )
    instrumented = (
        await manager.instrument(
            (
                _stdio_config(
                    key="stdio",
                    connection_id="stdio-connect-failure",
                    command=sys.executable,
                    args=("-c", child_code),
                ),
            )
        )
    )[0]
    server = manager._observer_server
    assert server is not None
    server.close()
    await server.wait_closed()

    request = b'{"jsonrpc":"2.0","id":4,"method":"ping"}\n'
    try:
        returncode, stdout, stderr = await _run_stdio_proxy(
            instrumented,
            environment={},
            input_bytes=request,
        )
        assert returncode == 0, stderr.decode(errors="replace")
        assert json.loads(stdout) == {
            "jsonrpc": "2.0",
            "id": 4,
            "result": {"ok": True},
        }
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_codex_stdio_defaults_reach_child_and_known_http_alias_is_ignored(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path, trusted_private_keys={"http-profile"})
    marker_child = (
        "import os,sys; sys.stdout.write("
        "os.environ.get('CODEX_MCP_PROTOCOL_VERSION','missing')+'\\n'); "
        "sys.stdout.flush()"
    )
    configs = (
        _stdio_config(
            key="modern-profile",
            connection_id="stdio-modern-marker",
            command=sys.executable,
            args=("-c", marker_child),
        ),
        _stdio_config(
            key="legacy-profile",
            connection_id="stdio-legacy-marker",
            command=sys.executable,
            args=("-c", marker_child),
            environment={"CODEX_MCP_PROTOCOL_VERSION": "2025-06-18"},
        ),
        HarnessServerConfig(
            key="http-profile",
            transport=TransportKind.STREAMABLE_HTTP,
            required=True,
            available=True,
            connection_id="http-profile",
            endpoint="http://127.0.0.1:1/mcp",
        ),
    )
    defaults = {
        "modern-profile": {"CODEX_MCP_PROTOCOL_VERSION": "2026-07-28"},
        "legacy-profile": {"CODEX_MCP_PROTOCOL_VERSION": "2026-07-28"},
        # Codex may include an unresolved profile binding which resolves to
        # HTTP. Its stdio-only environment overlay is valid and ignored here.
        "http-profile": {"CODEX_MCP_PROTOCOL_VERSION": "2026-07-28"},
    }
    try:
        instrumented = await manager.instrument(
            configs,
            stdio_environment_defaults=defaults,
        )
        for config, expected in zip(
            instrumented[:2], ("2026-07-28", "2025-06-18"), strict=True
        ):
            if config.key == "legacy-profile":
                assert config.environment["CODEX_MCP_PROTOCOL_VERSION"] == expected
            returncode, stdout, stderr = await _run_stdio_proxy(
                config,
                environment={"CODEX_MCP_PROTOCOL_VERSION": "ambient-wrong-value"},
            )
            assert returncode == 0, stderr.decode(errors="replace")
            assert stdout.decode().strip() == expected
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_http_instrumentation_keeps_credentials_only_in_proxy(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path, trusted_private_keys={"remote"})
    config = HarnessServerConfig(
        key="remote",
        transport=TransportKind.STREAMABLE_HTTP,
        required=True,
        available=True,
        connection_id="remote",
        endpoint="http://127.0.0.1:1/mcp",
        headers={"Authorization": "Bearer canary"},
    )
    instrumented = (await manager.instrument((config,)))[0]
    assert instrumented.headers == {}
    target = manager._targets[config.connection_id]
    assert target.proxy is not None
    assert target.proxy.configured_headers["Authorization"] == "Bearer canary"
    await manager.close()


@pytest.mark.asyncio
async def test_http_proxy_publishes_original_request_and_response(
    tmp_path: Path,
) -> None:
    upstream = EchoMcpHttpServer("http")
    endpoint = await upstream.start()
    manager = McpCaptureManager(tmp_path, trusted_private_keys={"http-live"})
    config = HarnessServerConfig(
        key="remote",
        transport=TransportKind.STREAMABLE_HTTP,
        required=True,
        available=True,
        connection_id="http-live",
        endpoint=endpoint,
    )
    instrumented = (await manager.instrument((config,)))[0]
    subscription = manager.subscribe()
    request = {
        "jsonrpc": "2.0",
        "id": 41,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"text": "round payload"}},
    }
    try:
        assert instrumented.endpoint is not None
        async with httpx.AsyncClient() as client:
            response = await client.post(instrumented.endpoint, json=request)
        assert response.status_code == 200
        original_request = await asyncio.wait_for(anext(subscription), timeout=1)
        original_response = await asyncio.wait_for(anext(subscription), timeout=1)
        assert original_request.payload == request
        assert original_request.direction == "client_to_server"
        assert original_response.payload == response.json()
        assert original_response.direction == "server_to_client"
        assert original_request.jsonrpc_identity == (int, 41)
    finally:
        await subscription.aclose()
        await manager.close()
        await upstream.stop()


@pytest.mark.asyncio
async def test_stdio_capture_resolves_secret_reference_in_one_shot_0600_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("M3_CAPTURE_SECRET", "capture-secret-value")
    manager = McpCaptureManager(tmp_path)
    config = HarnessServerConfig(
        key="echo",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="connection-secret",
        command="echo-server",
        environment={
            "TOKEN": SecretReference(source="environment", name="M3_CAPTURE_SECRET")
        },
    )
    instrumented = (await manager.instrument((config,)))[0]
    env_file = Path(instrumented.args[instrumented.args.index("--env-file") + 1])
    assert env_file.stat().st_mode & 0o777 == 0o600
    handoff = json.loads(env_file.read_text(encoding="utf-8"))
    assert handoff["environment"]["TOKEN"] == "capture-secret-value"
    assert handoff["environment"]["PATH"]
    assert handoff["canaries"] == ["capture-secret-value"]
    writer = manager.writer_for("connection-secret")
    writer.write(
        transport="stdio",
        direction="server_to_client",
        payload={"result": "capture-secret-value"},
    )
    assert "capture-secret-value" not in str(
        manager.snapshot("connection-secret").events
    )
    await manager.close()
    assert not env_file.exists()


@pytest.mark.asyncio
async def test_server_group_exposes_capture_context_and_preserves_connection_identity() -> (
    None
):
    manager = ServerGroupManager(
        (ServerBinding(server=StdioServer(name="echo", command="echo-server")),)
    )
    await manager.start()
    try:
        config = manager.configurations()[0]
        assert manager.capture is not None
        assert config.connection_id == manager.snapshot().records[0].connection_id
        assert config.command == sys.executable
        assert manager.capture.evidence()["capture_server_count"] == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_in_process_loopback_is_observed_without_rewriting_endpoint() -> None:
    manager = ServerGroupManager(
        (ServerBinding(server=InProcessServer(name="fixture", factory=_server)),)
    )
    await manager.start()
    endpoint = manager.configurations()[0].endpoint
    assert endpoint is not None
    assert manager.capture is not None
    subscription = manager.capture.subscribe()
    try:
        async with AsyncMCPTestKit() as kit:
            async with kit.direct(
                HTTPServer(name="loopback", url=endpoint, trust=TrustLevel.SDK_LOOPBACK)
            ) as client:
                await client.list_tools()
                await client.call_tool("draw", {})
        assert manager.capture is not None
        snapshot = manager.capture.snapshots()[0]
        assert snapshot.transport == "in_process"
        assert any(event.method == "tools/list" for event in snapshot.events)
        assert any(event.method == "tools/call" for event in snapshot.events)
        live = [
            await asyncio.wait_for(anext(subscription), timeout=1)
            for _ in snapshot.events
        ]
        assert any(
            event.direction == "client_to_server"
            and event.payload.get("method") == "tools/call"
            for event in live
            if isinstance(event.payload, dict)
        )
        assert any(
            event.direction == "server_to_client" and "result" in event.payload
            for event in live
            if isinstance(event.payload, dict)
        ), live
    finally:
        await subscription.aclose()
        await manager.close()


def test_loopback_sse_observation_is_incremental_and_frame_bounded() -> None:
    observed: list[Any] = []
    incomplete: list[str] = []
    parser = _SSECaptureParser(observed.append, incomplete.append)

    # This is the first ASGI response.body chunk with more_body=True. The
    # event becomes observable before the stream's final body chunk arrives.
    parser.feed(b'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n')
    assert observed == [{"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}]
    assert incomplete == []

    parser.feed(b'data: {"jsonrpc":"2.0","id":2,"result":{}}')
    parser.finish()
    assert observed[-1] == {"jsonrpc": "2.0", "id": 2, "result": {}}

    oversized_observed: list[Any] = []
    oversized_incomplete: list[str] = []
    bounded = _SSECaptureParser(oversized_observed.append, oversized_incomplete.append)
    bounded.feed(b"data: " + b"x" * (8 * 1024 * 1024 + 1))
    assert oversized_incomplete == ["message_too_large"]
    assert oversized_observed == []


@pytest.mark.asyncio
async def test_loopback_stream_publishes_event_before_response_finishes(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    endpoint = _LoopbackEndpoint(InProcessServer(name="fixture", factory=_server))
    manager.attach_loopback("streaming-loopback", endpoint)
    subscription = manager.subscribe()
    first_chunk_sent = asyncio.Event()
    allow_finish = asyncio.Event()
    final_chunk_sent = asyncio.Event()
    forwarded: list[dict[str, Any]] = []
    first_message = {
        "type": "http.response.body",
        "body": b'data: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n\n',
        "more_body": True,
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        forwarded.append(message)
        if message is first_message:
            first_chunk_sent.set()
        if message.get("type") == "http.response.body" and not message.get(
            "more_body", False
        ):
            final_chunk_sent.set()

    async def handle_request(_scope: Any, _receive: Any, send_message: Any) -> None:
        await send_message(
            {
                "type": "http.response.start",
                "status": 202,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send_message(first_message)
        await asyncio.wait_for(allow_finish.wait(), timeout=1)
        await send_message(
            {
                "type": "http.response.body",
                "body": b'data: {"jsonrpc":"2.0","id":8,"result":{}}\n\n',
                "more_body": False,
            }
        )

    task = asyncio.create_task(
        endpoint._observe_exchange(
            {"method": "POST", "path": "/mcp"},
            receive,
            send,
            handle_request,
        )
    )
    try:
        await asyncio.wait_for(first_chunk_sent.wait(), timeout=1)
        event = await asyncio.wait_for(anext(subscription), timeout=1)
        assert event.payload == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}
        assert event.direction == "server_to_client"
        assert not final_chunk_sent.is_set()
        assert forwarded[1] is first_message
        allow_finish.set()
        await asyncio.wait_for(task, timeout=1)
        final_event = await asyncio.wait_for(anext(subscription), timeout=1)
        assert final_event.payload == {"jsonrpc": "2.0", "id": 8, "result": {}}
    finally:
        allow_finish.set()
        if not task.done():
            await task
        await subscription.aclose()
        await manager.close()


@pytest.mark.asyncio
async def test_loopback_open_stream_exit_marks_observation_incomplete(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    endpoint = _LoopbackEndpoint(InProcessServer(name="fixture", factory=_server))
    manager.attach_loopback("incomplete-loopback", endpoint)
    subscription = manager.subscribe()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict[str, Any]) -> None:
        return None

    async def handle_request(_scope: Any, _receive: Any, send_message: Any) -> None:
        await send_message(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send_message(
            {
                "type": "http.response.body",
                "body": b'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n',
                "more_body": True,
            }
        )

    try:
        await endpoint._observe_exchange(
            {"method": "POST", "path": "/mcp"},
            receive,
            send,
            handle_request,
        )
        with pytest.raises(McpObservationIncomplete) as raised:
            await asyncio.wait_for(anext(subscription), timeout=1)
        assert raised.value.reason == "stream_ended_without_final_body"
    finally:
        await subscription.aclose()
        await manager.close()


@pytest.mark.asyncio
async def test_loopback_capture_writer_timeout_does_not_stall_final_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("m3.server_group._CAPTURE_DRAIN_TIMEOUT_SECONDS", 0.25)

    class StuckWriter:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.finished = threading.Event()

        def write(self, **_payload: Any) -> None:
            self.started.set()
            try:
                self.release.wait()
                raise OSError("late observer write failure")
            finally:
                self.finished.set()

    writer = StuckWriter()
    endpoint = _LoopbackEndpoint(InProcessServer(name="fixture", factory=_server))
    incomplete: list[str] = []
    endpoint.attach_capture(writer, on_incomplete=incomplete.append)
    forwarded: list[dict[str, Any]] = []
    response_finished = asyncio.Event()
    loop_errors: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    previous_exception_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        forwarded.append(message)
        if message.get("type") == "http.response.body" and not message.get(
            "more_body", False
        ):
            response_finished.set()

    response_start = {
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"application/json")],
    }
    response_body = {
        "type": "http.response.body",
        "body": b'{"jsonrpc":"2.0","id":1,"result":{}}',
        "more_body": False,
    }

    async def handle_request(_scope: Any, _receive: Any, send_message: Any) -> None:
        await send_message(response_start)
        await send_message(response_body)

    exchange = asyncio.create_task(
        endpoint._observe_exchange(
            {"method": "POST", "path": "/mcp"},
            receive,
            send,
            handle_request,
        )
    )
    try:
        await asyncio.wait_for(response_finished.wait(), timeout=1)
        deadline = loop.time() + 1
        while not writer.started.is_set() and loop.time() < deadline:
            await asyncio.sleep(0.001)
        assert writer.started.is_set()

        await asyncio.wait_for(exchange, timeout=0.5)

        assert forwarded == [response_start, response_body]
        assert incomplete == ["capture_write_timeout"]
        assert not [
            task
            for task in asyncio.all_tasks()
            if task.get_name() == "m3-loopback-capture-write" and not task.done()
        ]
    finally:
        writer.release.set()
        if not exchange.done():
            await asyncio.wait_for(exchange, timeout=1)
        deadline = loop.time() + 1
        while not writer.finished.is_set() and loop.time() < deadline:
            await asyncio.sleep(0.001)
        loop.set_exception_handler(previous_exception_handler)
    assert writer.finished.is_set()
    assert loop_errors == []


@pytest.mark.asyncio
async def test_http_sse_oversized_frame_fails_observation_and_forwards_bytes(
    tmp_path: Path,
) -> None:
    class ChunkStream(httpx.AsyncByteStream):
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks

        async def __aiter__(self) -> AsyncIterator[bytes]:
            for chunk in self.chunks:
                yield chunk

        async def aclose(self) -> None:
            return None

    original_chunks = [
        b"data: " + b"x" * (8 * 1024 * 1024 + 1),
        b"\n\n",
        b'data: {"jsonrpc":"2.0","id":2,"result":{}}\n\n',
    ]
    incomplete: list[str] = []
    proxy = McpHttpProxy(
        upstream_url="https://example.com/mcp",
        configured_headers=None,
        transport="streamable_http",
        capture_path=str(tmp_path / "http-sse.jsonl"),
        baseline_ns=0,
        on_incomplete=incomplete.append,
    )
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=ChunkStream(original_chunks),
    )

    forwarded = b"".join([chunk async for chunk in proxy._stream_sse(response)])

    assert forwarded == b"".join(original_chunks)
    assert incomplete == ["message_too_large"]
    assert response.is_closed


@pytest.mark.asyncio
async def test_http_sse_joins_data_lines_once_and_preserves_event_bytes(
    tmp_path: Path,
) -> None:
    class ChunkStream(httpx.AsyncByteStream):
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks

        async def __aiter__(self) -> AsyncIterator[bytes]:
            for chunk in self.chunks:
                yield chunk

        async def aclose(self) -> None:
            return None

    body = (
        b'event: message\r\ndata: {"jsonrpc":"2.0",\r\n'
        b'data: "id":7,"method":"tools/call","params":{"name":"draw"}}\r\n\r\n'
        b"event: done\ndata:  [DONE]\n\n"
        b"data\n\n"
    )
    chunks = [body[:23], body[23:71], body[71:]]
    capture_path = tmp_path / "http-sse-multiline.jsonl"
    proxy = McpHttpProxy(
        upstream_url="https://example.com/mcp",
        configured_headers=None,
        transport="streamable_http",
        capture_path=str(capture_path),
        baseline_ns=0,
    )
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=ChunkStream(chunks),
    )

    forwarded = b"".join([chunk async for chunk in proxy._stream_sse(response)])

    assert forwarded == body
    assert response.is_closed
    records = [
        json.loads(line)
        for line in capture_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["payload"] for record in records] == [
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "draw"},
        },
        " [DONE]",
        "",
    ]
    assert [record["kind"] for record in records] == ["sse_data"] * 3


@pytest.mark.asyncio
async def test_http_sse_capture_keeps_url_rewrite_behavior(
    tmp_path: Path,
) -> None:
    class ChunkStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"event: endpoint\r\ndata: https://example.com/next\r\n\r\n"

        async def aclose(self) -> None:
            return None

    capture_path = tmp_path / "http-sse-url-rewrite.jsonl"
    proxy = McpHttpProxy(
        upstream_url="https://example.com/mcp",
        configured_headers=None,
        transport="streamable_http",
        capture_path=str(capture_path),
        baseline_ns=0,
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    proxy.socket = listener
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=ChunkStream(),
    )

    try:
        forwarded = b"".join([chunk async for chunk in proxy._stream_sse(response)])
    finally:
        listener.close()

    local_url = f"http://127.0.0.1:{port}"
    assert forwarded == (f"event: endpoint\r\ndata: {local_url}/next\r\n\r\n".encode())
    records = [
        json.loads(line)
        for line in capture_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["payload"] for record in records] == ["https://example.com/next"]


@pytest.mark.asyncio
async def test_live_observation_keeps_original_envelopes_and_typed_ids(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    subscription = manager.subscribe()
    writer = manager.writer_for("live-http", "streamable_http")
    writer.add_secrets({"raw-address-canary"})
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "book", "arguments": {"address": "raw-address-canary"}},
    }
    response = {
        "jsonrpc": "2.0",
        "id": "7",
        "result": {
            "structuredContent": {
                "requestState": "opaque-round-state",
                "inputRequests": {
                    "address": {
                        "type": "elicitation",
                        "mode": "form",
                        "message": "Where should we deliver?",
                    }
                },
            }
        },
    }
    writer.write(
        transport="streamable_http",
        direction="client_to_server",
        payload=request,
    )
    writer.write(
        transport="streamable_http",
        direction="server_to_client",
        payload=response,
    )

    observed_request = await asyncio.wait_for(anext(subscription), timeout=1)
    observed_response = await asyncio.wait_for(anext(subscription), timeout=1)
    assert observed_request.payload == request
    assert observed_response.payload == response
    assert observed_request.jsonrpc_identity == (int, 7)
    assert observed_response.jsonrpc_identity == (str, "7")
    assert (observed_request.sequence, observed_response.sequence) == (1, 2)
    assert observed_request.transport == "streamable_http"
    assert observed_response.direction == "server_to_client"

    persisted = (tmp_path / "live-http.jsonl").read_text(encoding="utf-8")
    assert "raw-address-canary" not in persisted
    assert "opaque-round-state" in persisted
    await subscription.aclose()
    await manager.close()


@pytest.mark.asyncio
async def test_observation_barrier_drains_scheduled_publication_and_processing(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    subscription = manager.subscribe()
    writer = manager.writer_for("barrier")
    processing_started = asyncio.Event()
    allow_acknowledgement = asyncio.Event()
    consumed: list[McpObservation] = []

    async def consume_one() -> None:
        event = await anext(subscription)
        processing_started.set()
        await allow_acknowledgement.wait()
        consumed.append(event)
        await subscription.acknowledge(event)

    consumer = asyncio.create_task(consume_one())
    relay = threading.Thread(
        target=lambda: writer.write(
            transport="stdio",
            direction="server_to_client",
            payload={"jsonrpc": "2.0", "id": 9, "result": {"ok": True}},
        )
    )
    relay.start()
    relay.join(timeout=2)
    assert not relay.is_alive()
    # The writer's callback has been accepted from another thread, but the
    # event loop has not yet run it because this test has not yielded.
    assert len(manager._pending_publication_tickets) == 1

    barrier = asyncio.create_task(subscription.barrier())
    try:
        await asyncio.wait_for(processing_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not barrier.done()
        allow_acknowledgement.set()
        assert await asyncio.wait_for(barrier, timeout=1) == 1
        await asyncio.wait_for(consumer, timeout=1)
        assert [event.payload["id"] for event in consumed] == [9]
    finally:
        allow_acknowledgement.set()
        if not consumer.done():
            await consumer
        if not barrier.done():
            barrier.cancel()
            await asyncio.gather(barrier, return_exceptions=True)
        await subscription.aclose()
        await manager.close()


@pytest.mark.asyncio
async def test_observation_barrier_preserves_same_loop_publication_order(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    subscription = manager.subscribe()
    writer = manager.writer_for("same-loop-barrier")
    for request_id in (4, 5):
        writer.write(
            transport="streamable_http",
            direction="server_to_client",
            payload={"jsonrpc": "2.0", "id": request_id, "result": {}},
        )

    barrier = asyncio.create_task(subscription.barrier())
    try:
        await asyncio.sleep(0)
        assert not barrier.done()
        observed_ids: list[int] = []
        for _ in range(2):
            event = await asyncio.wait_for(anext(subscription), timeout=1)
            observed_ids.append(event.payload["id"])
            await subscription.acknowledge(event)
        assert await asyncio.wait_for(barrier, timeout=1) == 2
        assert observed_ids == [4, 5]
    finally:
        if not barrier.done():
            barrier.cancel()
            await asyncio.gather(barrier, return_exceptions=True)
        await subscription.aclose()
        await manager.close()


@pytest.mark.asyncio
async def test_observation_barrier_reports_thread_callback_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = McpCaptureManager(tmp_path)
    subscription = manager.subscribe()

    def fail_publish(
        _connection_id: str, _transport: str, _direction: str, _payload: Any
    ) -> None:
        raise RuntimeError("observer publish failed")

    monkeypatch.setattr(manager, "_publish", fail_publish)
    relay = threading.Thread(
        target=lambda: manager.writer_for("failed-publication").write(
            transport="stdio",
            direction="server_to_client",
            payload={"jsonrpc": "2.0", "id": 1, "result": {}},
        )
    )
    relay.start()
    relay.join(timeout=2)
    assert not relay.is_alive()
    try:
        with pytest.raises(McpObservationIncomplete) as exc_info:
            await asyncio.wait_for(subscription.barrier(), timeout=1)
        assert exc_info.value.reason == "publication_failed"
    finally:
        await subscription.aclose()
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", ["close", "fail"])
async def test_observation_barrier_wakes_with_incomplete_on_interrupt(
    tmp_path: Path, interrupt: str
) -> None:
    manager = McpCaptureManager(tmp_path)
    subscription = manager.subscribe()
    manager.writer_for("interrupted-barrier").write(
        transport="streamable_http",
        direction="server_to_client",
        payload={"jsonrpc": "2.0", "id": 1, "result": {}},
    )
    await asyncio.wait_for(anext(subscription), timeout=1)
    barrier = asyncio.create_task(subscription.barrier())
    try:
        await asyncio.sleep(0)
        assert not barrier.done()
        if interrupt == "close":
            await subscription.aclose()
            expected_reason = "subscription_closed"
        else:
            manager.fail_observation("interrupted-barrier", "test_failure")
            expected_reason = "test_failure"
        with pytest.raises(McpObservationIncomplete) as exc_info:
            await asyncio.wait_for(barrier, timeout=1)
        assert exc_info.value.reason == expected_reason
    finally:
        if not barrier.done():
            barrier.cancel()
            await asyncio.gather(barrier, return_exceptions=True)
        if not subscription._closed:
            await subscription.aclose()
        await manager.close()


@pytest.mark.asyncio
async def test_policy_denials_are_not_published_to_live_observers(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    subscription = manager.subscribe()
    writer = manager.writer_for("policy")
    writer.write(
        transport="streamable_http",
        direction="client_to_server",
        payload={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
        kind="policy_denied",
    )
    writer.write(
        transport="streamable_http",
        direction="server_to_client",
        payload={"jsonrpc": "2.0", "id": 1, "error": {"code": -32001}},
        kind="policy_denied",
    )
    writer.write(
        transport="streamable_http",
        direction="server_to_client",
        payload={"jsonrpc": "2.0", "id": 2, "result": {"ok": True}},
    )
    event = await asyncio.wait_for(anext(subscription), timeout=1)
    assert event.payload["id"] == 2
    await subscription.aclose()
    await manager.close()


@pytest.mark.asyncio
async def test_observation_queue_overflow_fails_closed_and_redacts_persistence(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    subscription = manager.subscribe(maxsize=1)
    writer = manager.writer_for("bounded")
    writer.add_secrets({"private-value"})
    for ident in (1, 2):
        writer.write(
            transport="in_process",
            direction="server_to_client",
            payload={"jsonrpc": "2.0", "id": ident, "result": "private-value"},
        )
    await asyncio.sleep(0)
    with pytest.raises(McpObservationIncomplete) as raised:
        await asyncio.wait_for(anext(subscription), timeout=1)
    assert raised.value.connection_id == "bounded"
    assert raised.value.reason == "buffer_overflow"
    persisted = (tmp_path / "bounded.jsonl").read_text(encoding="utf-8")
    assert "private-value" not in persisted
    await manager.close()


@pytest.mark.asyncio
async def test_observation_frame_size_is_bounded_before_queueing(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    subscription = manager.subscribe()
    manager._publish(
        "oversized",
        "streamable_http",
        "server_to_client",
        {"result": "x" * (_MAX_OBSERVATION_FRAME_BYTES + 1)},
    )
    with pytest.raises(McpObservationIncomplete) as raised:
        await asyncio.wait_for(anext(subscription), timeout=1)
    assert raised.value.reason == "message_too_large"
    await manager.close()


@pytest.mark.asyncio
async def test_observer_listener_bind_failure_keeps_stdio_child_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = McpCaptureManager(tmp_path)
    subscription = manager.subscribe({"stdio-bind-failure"})

    async def fail_listener() -> None:
        raise OSError("loopback bind unavailable")

    monkeypatch.setattr(manager, "_start_observation_server", fail_listener)
    config = _stdio_config(
        key="stdio",
        connection_id="stdio-bind-failure",
        command=sys.executable,
        args=("-c", "print('child-started')"),
    )
    try:
        instrumented = (await manager.instrument((config,)))[0]
        env_file = Path(instrumented.args[instrumented.args.index("--env-file") + 1])
        assert "observer" not in json.loads(env_file.read_text())
        returncode, stdout, stderr = await _run_stdio_proxy(
            instrumented, environment={}
        )
        assert returncode == 0, stderr.decode(errors="replace")
        assert stdout == b"child-started\n"
        with pytest.raises(McpObservationIncomplete) as raised:
            await asyncio.wait_for(anext(subscription), timeout=0.1)
        assert raised.value.reason == "observer_start_failed"
    finally:
        await manager.close()


def test_stdio_request_is_observed_before_fast_child_response(
    tmp_path: Path,
) -> None:
    request = b'{"jsonrpc":"2.0","id":8,"method":"ping"}\n'
    timeline: list[str] = []

    class ImmediateObserver:
        healthy = True

        def observe(self, direction: str, _payload: Any) -> None:
            timeline.append(f"observed:{direction}")

        def fail(self) -> None:
            self.healthy = False

    observer = ImmediateObserver()

    class ImmediateChild:
        def __init__(self) -> None:
            self.data = bytearray()

        def write(self, value: bytes) -> None:
            assert timeline == ["observed:client_to_server"]
            timeline.append("child-received-request")
            self.data.extend(value)
            observer.observe(
                "server_to_client",
                {"jsonrpc": "2.0", "id": 8, "result": {}},
            )

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    child = ImmediateChild()
    _relay(
        io.BytesIO(request),
        child,
        CaptureWriter(str(tmp_path / "ordered.jsonl"), 0),
        "client_to_server",
        observer=observer,  # type: ignore[arg-type]
    )
    assert bytes(child.data) == request
    assert timeline == [
        "observed:client_to_server",
        "child-received-request",
        "observed:server_to_client",
    ]


@pytest.mark.asyncio
async def test_stdio_live_observation_authenticates_and_reports_eof(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    config = HarnessServerConfig(
        key="echo",
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="stdio-observed",
        command="echo-server",
    )
    instrumented = (await manager.instrument((config,)))[0]
    env_file = Path(instrumented.args[instrumented.args.index("--env-file") + 1])
    handoff = json.loads(env_file.read_text(encoding="utf-8"))
    observer_info = handoff["observer"]
    assert observer_info["host"] == "127.0.0.1"
    assert 1 <= int(observer_info["port"]) <= 65535
    subscription = manager.subscribe()

    # A local process without the per-target secret cannot publish events or
    # terminate the authenticated proxy connection.
    unauthenticated = socket.create_connection(
        (observer_info["host"], int(observer_info["port"]))
    )
    unauthenticated.sendall(
        json.dumps(
            {
                "connection_id": "stdio-observed",
                "token": "wrong-token",
            }
        ).encode()
        + b"\n"
    )
    unauthenticated.close()

    channel = _ObservationChannel(**observer_info)
    request_line = (
        b'{"jsonrpc":"2.0", "id": 7, "method":"tools/call",'
        b'"params":{"name":"book","arguments":{"x":1}}}\n'
    )

    class ForwardSink:
        def __init__(self) -> None:
            self.data = bytearray()

        def write(self, value: bytes) -> None:
            self.data.extend(value)

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    forwarded = ForwardSink()
    relay = threading.Thread(
        target=_relay,
        args=(
            io.BytesIO(request_line),
            forwarded,
            CaptureWriter(str(tmp_path / "stdio-child.jsonl"), 0),
            "client_to_server",
            None,
            channel,
        ),
    )
    relay.start()
    relay.join(timeout=2)
    assert not relay.is_alive()
    assert bytes(forwarded.data) == request_line

    async def wait_for_authenticated_channel() -> None:
        while not manager._observer_writers:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_authenticated_channel(), timeout=1)
    observed = await asyncio.wait_for(anext(subscription), timeout=1)
    assert channel.healthy
    assert observed.payload["id"] == 7
    assert observed.jsonrpc_identity == (int, 7)
    assert observed.direction == "client_to_server"

    local_writer = manager.writer_for("stdio-observed")
    await asyncio.gather(
        asyncio.to_thread(
            local_writer.write,
            transport="stdio",
            direction="server_to_client",
            payload={"jsonrpc": "2.0", "id": 90, "result": "local"},
        ),
        asyncio.to_thread(
            channel.observe,
            "server_to_client",
            {"jsonrpc": "2.0", "id": "91", "result": "remote"},
        ),
    )
    interleaved = [
        await asyncio.wait_for(anext(subscription), timeout=1),
        await asyncio.wait_for(anext(subscription), timeout=1),
    ]
    assert [event.sequence for event in interleaved] == sorted(
        event.sequence for event in interleaved
    )
    assert {event.jsonrpc_identity for event in interleaved} == {
        (int, 90),
        (str, "91"),
    }

    channel.close()
    with pytest.raises(McpObservationIncomplete) as raised:
        await asyncio.wait_for(anext(subscription), timeout=1)
    assert raised.value.reason == "observer_eof"
    await manager.close()


@pytest.mark.asyncio
async def test_stdio_capture_write_failure_preserves_forwarding_and_fails_observer(
    tmp_path: Path,
) -> None:
    manager = McpCaptureManager(tmp_path)
    config = _stdio_config(
        key="broken-capture",
        connection_id="broken-capture",
        command="unused-child",
    )
    instrumented = (await manager.instrument((config,)))[0]
    env_file = Path(instrumented.args[instrumented.args.index("--env-file") + 1])
    observer_info = json.loads(env_file.read_text())["observer"]
    subscription = manager.subscribe({"broken-capture"})
    channel = _ObservationChannel(**observer_info)

    async def wait_for_authenticated_channel() -> None:
        while not manager._observer_writers:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_authenticated_channel(), timeout=1)
    request = b'{"jsonrpc":"2.0","id":11,"method":"ping"}\n'

    class BrokenCapture:
        def write(self, **_kwargs: Any) -> None:
            raise OSError("capture target unavailable")

    class ForwardSink:
        def __init__(self) -> None:
            self.data = bytearray()

        def write(self, value: bytes) -> None:
            self.data.extend(value)

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    forwarded = ForwardSink()
    try:
        _relay(
            io.BytesIO(request),
            forwarded,
            BrokenCapture(),  # type: ignore[arg-type]
            "client_to_server",
            observer=channel,
        )
        assert bytes(forwarded.data) == request
        with pytest.raises(McpObservationIncomplete) as raised:
            await asyncio.wait_for(anext(subscription), timeout=1)
        assert raised.value.reason == "observer_eof"
    finally:
        channel.close()
        await manager.close()


@pytest.mark.asyncio
async def test_agent_execution_projects_wire_capture_into_stable_trace() -> None:
    writer_holder: dict[str, CaptureWriter] = {}

    async def handler(
        _request: HarnessTurnRequest, _state: MutableMapping[str, Any]
    ) -> str:
        writer = writer_holder["writer"]
        writer.write(
            transport="stdio",
            direction="client_to_server",
            payload={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "draw", "arguments": {"token": "secret-canary"}},
            },
        )
        writer.write(
            transport="stdio",
            direction="server_to_client",
            payload={
                "jsonrpc": "2.0",
                "id": 7,
                "result": {"content": [{"text": "ok"}]},
            },
        )
        return "done"

    class WireAdapter(DeterministicHarnessAdapter):
        async def open(self, launch: HarnessLaunch) -> HarnessSession:
            configurations = launch.configurations
            capture = launch.capture
            assert capture is not None
            writer_holder["writer"] = capture.writer_for(
                configurations[0].connection_id
            )
            return await super().open(launch)

    adapter = WireAdapter(handler=handler)
    registry = HarnessAdapterRegistry({"claude_code": lambda _harness: adapter})
    spec = AgentSpec(
        harness=ClaudeCode(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        message=UserMessage(content=(TextContent(text="draw"),)),
    )
    async with AsyncMCPTestKit(
        adapter_registry=registry, env={}, cwd="/tmp/m3-no-project"
    ) as kit:
        result = await kit.run(spec)
    assert result.trace is not None
    wire = [
        event
        for event in result.trace.events
        if event.provenance.origin.value == "wire_observed"
    ]
    assert any(event.kind.value == "tool.call_requested" for event in wire)
    assert any(event.kind.value == "tool.result_received" for event in wire)
    request = next(event for event in wire if event.kind.value == "tool.call_requested")
    assert request.connection_id is not None
    assert request.correlation is not None and request.correlation.jsonrpc_id == 7
    assert result.activity_health.value == "all_succeeded"
    assert "secret-canary" not in str(result.trace.model_dump(mode="json"))
