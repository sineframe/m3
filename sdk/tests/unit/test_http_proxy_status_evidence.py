"""HTTP status evidence for proxied Streamable HTTP servers (SINEF-118)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from m3.agent_session import AsyncAgentSession
from m3.server_group import HarnessServerConfig
from m3.transport.capture_proxy import McpCaptureManager, McpWireEvent
from m3.types import EventKind, TransportKind

_CHALLENGE = (
    'Bearer realm="m3", error="invalid_token", '
    'resource_metadata="http://127.0.0.1/.well-known/oauth-protected-resource", '
    'nonce="challenge-only-secret"'
)
_INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

Responder = Callable[[dict[str, Any]], tuple[str, dict[str, str], bytes]]


async def _upstream(respond: Responder) -> tuple[asyncio.AbstractServer, int]:
    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            header_bytes = await reader.readuntil(b"\r\n\r\n")
            lines = header_bytes[:-4].split(b"\r\n")
            headers = {
                line.decode().split(":", 1)[0].lower(): line.decode()
                .split(":", 1)[1]
                .strip()
                for line in lines[1:]
            }
            length = int(headers.get("content-length", "0"))
            body = await reader.readexactly(length) if length else b""
            status, extra, body_out = respond(json.loads(body or b"{}"))
            head = f"HTTP/1.1 {status}\r\n" + "".join(
                f"{name}: {value}\r\n" for name, value in extra.items()
            )
            writer.write(
                (
                    head + f"Content-Length: {len(body_out)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
                + body_out
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, int(server.sockets[0].getsockname()[1])


async def _proxied_initialize(
    tmp_path: Path, respond: Responder
) -> tuple[httpx.Response, tuple[McpWireEvent, ...], str]:
    server, port = await _upstream(respond)
    manager = McpCaptureManager(tmp_path, trusted_private_keys={"guarded"})
    config = HarnessServerConfig(
        key="guarded",
        transport=TransportKind.STREAMABLE_HTTP,
        required=True,
        available=True,
        connection_id="guarded",
        endpoint=f"http://127.0.0.1:{port}/mcp",
        headers={"Authorization": "Bearer proxy-only-secret"},
    )
    try:
        instrumented = (await manager.instrument((config,)))[0]
        assert instrumented.endpoint is not None
        async with httpx.AsyncClient() as client:
            response = await client.post(instrumented.endpoint, json=_INITIALIZE)
        events = manager.snapshot("guarded").events
        capture = "".join(
            path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.jsonl")
        )
        return response, events, capture
    finally:
        await manager.close()
        server.close()
        await server.wait_closed()


def _answer(events: tuple[McpWireEvent, ...]) -> McpWireEvent:
    answers = [
        event
        for event in events
        if event.jsonrpc_id == 1 and event.kind in {"response", "error"}
    ]
    assert len(answers) == 1
    return answers[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"", None),
        (b'{"error":"invalid_token"}', "invalid_token"),
        (b"unauthorized", None),
    ],
)
async def test_proxy_records_the_http_status_of_a_refused_request(
    tmp_path: Path, body: bytes, error: object
) -> None:
    def respond(_request: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
        return (
            "401 Unauthorized",
            {"Content-Type": "application/json", "WWW-Authenticate": _CHALLENGE},
            body,
        )

    response, events, capture = await _proxied_initialize(tmp_path, respond)

    assert response.status_code == 401
    answer = _answer(events)
    assert answer.kind == "error"
    assert answer.method == "initialize"
    assert answer.response_to_sequence is not None
    assert answer.error == error
    assert [
        event for event in events if event.jsonrpc_id is None and event.kind == "error"
    ] == []
    assert answer.http is not None
    assert answer.http["method"] == "POST"
    assert answer.http["status_code"] == 401
    challenges = [
        header["value"]
        for header in answer.http["headers"]
        if header["name"] == "www-authenticate"
    ]
    assert challenges == [
        'Bearer realm="m3", error="invalid_token", '
        'resource_metadata="http://127.0.0.1/.well-known/oauth-protected-resource"'
    ]
    assert "challenge-only-secret" not in capture
    assert "proxy-only-secret" not in capture


def _initialize_result(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "serverInfo": {"name": "guarded", "version": "1"},
        },
    }


@pytest.mark.asyncio
async def test_proxy_records_the_http_status_of_a_json_response(
    tmp_path: Path,
) -> None:
    def respond(request: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
        body = json.dumps(_initialize_result(request)).encode()
        return "200 OK", {"Content-Type": "application/json"}, body

    _response, events, _capture = await _proxied_initialize(tmp_path, respond)

    answer = _answer(events)
    assert answer.kind == "response"
    assert answer.http is not None
    assert answer.http["status_code"] == 200


@pytest.mark.asyncio
async def test_proxy_records_the_http_status_of_an_sse_response(
    tmp_path: Path,
) -> None:
    def respond(request: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
        frame = f"event: message\ndata: {json.dumps(_initialize_result(request))}\n\n"
        return "200 OK", {"Content-Type": "text/event-stream"}, frame.encode()

    _response, events, _capture = await _proxied_initialize(tmp_path, respond)

    answer = _answer(events)
    assert answer.kind == "response"
    assert answer.http is not None
    assert answer.http["status_code"] == 200


def test_agent_session_keeps_wire_http_evidence_in_the_trace_payload() -> None:
    exchange = {"method": "POST", "status_code": 401, "headers": []}
    wire = (
        McpWireEvent(
            connection_id="guarded",
            transport="streamable_http",
            direction="client_to_server",
            kind="request",
            offset_ms=0.0,
            request_sequence=1,
            jsonrpc_id=1,
            method="initialize",
        ),
        McpWireEvent(
            connection_id="guarded",
            transport="streamable_http",
            direction="server_to_client",
            kind="error",
            offset_ms=1.0,
            request_sequence=1,
            jsonrpc_id=1,
            method="initialize",
            response_to_sequence=1,
            http=exchange,
        ),
    )
    snapshot = SimpleNamespace(connection_id="guarded", events=wire)
    manager = SimpleNamespace(
        capture=SimpleNamespace(snapshots=lambda: (snapshot,)),
        snapshot=lambda: SimpleNamespace(records=()),
    )
    emitted: list[tuple[EventKind, dict[str, Any]]] = []
    session = SimpleNamespace(
        _server_manager=manager,
        _capture_seen={},
        _captured_tool_outcomes=[],
        _emit_event=lambda kind, payload, **_kwargs: emitted.append((kind, payload)),
    )

    AsyncAgentSession._emit_captured_wire_events(session, None)  # type: ignore[arg-type]

    assert [kind for kind, _payload in emitted] == [
        EventKind.MCP_REQUEST,
        EventKind.MCP_ERROR,
    ]
    assert "http" not in emitted[0][1]
    assert emitted[1][1]["http"] == exchange


@pytest.mark.asyncio
async def test_refused_request_status_is_not_published_as_a_live_message(
    tmp_path: Path,
) -> None:
    def respond(_request: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
        return "401 Unauthorized", {"WWW-Authenticate": _CHALLENGE}, b""

    server, port = await _upstream(respond)
    manager = McpCaptureManager(tmp_path, trusted_private_keys={"guarded"})
    config = HarnessServerConfig(
        key="guarded",
        transport=TransportKind.STREAMABLE_HTTP,
        required=True,
        available=True,
        connection_id="guarded",
        endpoint=f"http://127.0.0.1:{port}/mcp",
    )
    subscription = manager.subscribe()
    try:
        instrumented = (await manager.instrument((config,)))[0]
        assert instrumented.endpoint is not None
        async with httpx.AsyncClient() as client:
            await client.post(instrumented.endpoint, json=_INITIALIZE)
        published = await asyncio.wait_for(anext(subscription), timeout=1)
        assert published.direction == "client_to_server"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(subscription), timeout=0.3)
    finally:
        await subscription.aclose()
        await manager.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["401 Unauthorized", "403 Forbidden", "500 Internal Server Error"]
)
async def test_http_refused_tool_call_counts_as_a_failed_call(
    tmp_path: Path, status: str
) -> None:
    def respond(_request: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
        return status, {}, b""

    server, port = await _upstream(respond)
    manager = McpCaptureManager(tmp_path, trusted_private_keys={"guarded"})
    config = HarnessServerConfig(
        key="guarded",
        transport=TransportKind.STREAMABLE_HTTP,
        required=True,
        available=True,
        connection_id="guarded",
        endpoint=f"http://127.0.0.1:{port}/mcp",
    )
    call = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {}},
    }
    try:
        instrumented = (await manager.instrument((config,)))[0]
        assert instrumented.endpoint is not None
        async with httpx.AsyncClient() as client:
            await client.post(instrumented.endpoint, json=call)
        session = SimpleNamespace(
            _server_manager=SimpleNamespace(
                capture=manager, snapshot=lambda: SimpleNamespace(records=())
            ),
            _capture_seen={},
            _captured_tool_outcomes=[],
            _emit_event=lambda *_args, **_kwargs: None,
        )

        AsyncAgentSession._emit_captured_wire_events(session, None)  # type: ignore[arg-type]

        assert session._captured_tool_outcomes == [False]
        assert AsyncAgentSession._captured_activity_outcomes(session) == [False]  # type: ignore[arg-type]
    finally:
        await manager.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("id_field", [b'"id":null,', b""])
async def test_proxy_pairs_a_null_id_error_with_the_refused_request(
    tmp_path: Path, id_field: bytes
) -> None:
    body = (
        b'{"jsonrpc":"2.0",'
        + id_field
        + b'"error":{"code":-32600,"message":"Session not found"}}'
    )

    def respond(_request: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
        return "404 Not Found", {"Content-Type": "application/json"}, body

    _response, events, capture = await _proxied_initialize(tmp_path, respond)

    answer = _answer(events)
    assert answer.kind == "error"
    assert answer.method == "initialize"
    assert answer.error == {"code": -32600, "message": "Session not found"}
    assert answer.http is not None
    assert answer.http["status_code"] == 404
    assert [
        event for event in events if event.jsonrpc_id is None and event.kind == "error"
    ] == []
    assert '"http_status"' not in capture
