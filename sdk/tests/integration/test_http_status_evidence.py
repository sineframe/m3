"""HTTP status evidence for direct Streamable HTTP connections (SINEF-118)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from m3.async_api import AsyncMCPTestKit
from m3.errors import ProtocolError
from m3.observability import ObservationState, ProtocolEntry
from m3.types import HTTPServer, TrustLevel

pytestmark = pytest.mark.process_lifecycle

_PROTOCOL = "2025-11-25"
_CHALLENGE = (
    'Bearer realm="m3", error="invalid_token", '
    'error_description="token expired", '
    'resource_metadata="http://127.0.0.1/.well-known/oauth-protected-resource", '
    'nonce="challenge-only-secret"'
)

Responder = Callable[[dict[str, Any]], tuple[str, dict[str, str], bytes]]


class _BearerAuth(httpx2.Auth):
    def auth_flow(self, request: httpx2.Request) -> Any:
        request.headers["Authorization"] = "Bearer rejected-fixture-token"
        yield request


async def _serve(
    respond: Responder,
) -> tuple[asyncio.AbstractServer, int]:
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


async def _initialize_entry(
    respond: Responder, *, expect_failure: bool
) -> tuple[ProtocolEntry, str]:
    server, port = await _serve(respond)
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project")
    client = kit.direct(
        HTTPServer(
            name="guarded",
            url=f"http://127.0.0.1:{port}/mcp",
            trust=TrustLevel.TRUSTED_PRIVATE,
        ),
        auth=_BearerAuth(),
    )
    try:
        if expect_failure:
            with pytest.raises(ProtocolError):
                async with client:
                    pass
        else:
            async with client:
                pass
        trace = client.final_trace
        assert trace is not None
        entries = [
            entry
            for entry in trace.view().protocol
            if entry.method.value == "initialize"
        ]
        assert len(entries) == 1
        return entries[0], json.dumps(trace.model_dump(mode="json"))
    finally:
        await kit.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"", b'{"error":"invalid_token"}'])
async def test_refused_initialize_records_http_401_and_a_safe_challenge(
    body: bytes,
) -> None:
    def respond(_request: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
        return (
            "401 Unauthorized",
            {"Content-Type": "application/json", "WWW-Authenticate": _CHALLENGE},
            body,
        )

    entry, dumped = await _initialize_entry(respond, expect_failure=True)

    assert entry.http.state is ObservationState.OBSERVED
    assert entry.http.value is not None
    assert entry.http.value.method == "POST"
    assert entry.http.value.status_code == 401
    challenges = [
        header.value
        for header in entry.http.value.headers
        if header.name == "www-authenticate"
    ]
    assert len(challenges) == 1
    assert challenges[0].startswith("Bearer ")
    for kept in (
        'realm="m3"',
        'error="invalid_token"',
        'error_description="token expired"',
        "resource_metadata=",
    ):
        assert kept in challenges[0]
    assert "challenge-only-secret" not in dumped
    assert "rejected-fixture-token" not in dumped


@pytest.mark.asyncio
async def test_accepted_initialize_records_http_200() -> None:
    def respond(request: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
        if request.get("method") == "initialize":
            result = {
                "protocolVersion": _PROTOCOL,
                "capabilities": {},
                "serverInfo": {"name": "guarded", "version": "1"},
            }
            body = json.dumps(
                {"jsonrpc": "2.0", "id": request["id"], "result": result}
            ).encode()
            return "200 OK", {"Content-Type": "application/json"}, body
        return "202 Accepted", {}, b""

    entry, _dumped = await _initialize_entry(respond, expect_failure=False)

    assert entry.http.state is ObservationState.OBSERVED
    assert entry.http.value is not None
    assert entry.http.value.status_code == 200
