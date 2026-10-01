from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.async_api import AsyncMCPTestKit
from m3.types import HTTPServer, SecretReference, StdioServer, TrustLevel

HERE = Path(__file__).resolve().parent
_PROTOCOL = "2025-11-25"
_DUMMY_TOKEN = "dummy-endpoint-token"


def _response(request: dict[str, Any], result: dict[str, Any]) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request["id"], "result": result},
        separators=(",", ":"),
    ).encode()


async def _start_server(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
) -> tuple[asyncio.AbstractServer, int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, int(server.sockets[0].getsockname()[1])


@pytest.mark.asyncio
async def test_http_secret_reference_reaches_local_endpoint_without_entering_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str]] = []

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
            body = await reader.readexactly(length) if length else b"{}"
            request = json.loads(body)
            requests.append(
                (headers.get("x-demo-key", ""), headers.get("authorization", ""))
            )
            if (
                headers.get("x-demo-key") != "dummy-endpoint-token"
                or headers.get("authorization") != "Bearer dummy-bearer-token"
            ):
                status, payload = "401 Unauthorized", b"unauthorized"
            elif request.get("method") == "initialize":
                status = "200 OK"
                payload = _response(
                    request,
                    {
                        "protocolVersion": _PROTOCOL,
                        "capabilities": {},
                        "serverInfo": {"name": "credential-endpoint", "version": "1"},
                    },
                )
            elif request.get("method") == "tools/list":
                status = "200 OK"
                payload = _response(
                    request,
                    {
                        "tools": [
                            {
                                "name": "credential_check",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                )
            elif request.get("method") == "tools/call":
                status = "200 OK"
                payload = _response(
                    request,
                    {
                        "content": [
                            {"type": "text", "text": "http credential accepted"}
                        ],
                        "structuredContent": {"credential_present": True},
                        "isError": False,
                    },
                )
            else:
                status, payload = "202 Accepted", b""
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await _start_server(handler)
    monkeypatch.setenv("DEMO_ENDPOINT_KEY", _DUMMY_TOKEN)
    monkeypatch.setenv("DEMO_BEARER_TOKEN", "dummy-bearer-token")
    kit = AsyncMCPTestKit(env={})
    try:
        binding = HTTPServer(
            name="credential-endpoint",
            url=f"http://127.0.0.1:{port}/mcp",
            headers={
                "X-Demo-Key": SecretReference(
                    source="environment", name="DEMO_ENDPOINT_KEY"
                )
            },
            trust=TrustLevel.TRUSTED_PRIVATE,
        )
        async with kit.direct(
            binding,
            bearer_token=SecretReference(
                source="environment", name="DEMO_BEARER_TOKEN"
            ),
        ) as client:
            result = await client.call_tool("credential_check", {})
            assert result.content[0]["text"] == "http credential accepted"
            assert result.structured_content == {"credential_present": True}
            assert client.trace is not None
            assert "dummy-endpoint-token" not in repr(
                client.trace.model_dump(mode="json")
            )
            assert "dummy-bearer-token" not in repr(
                client.trace.model_dump(mode="json")
            )
        assert requests
        assert all(key == "dummy-endpoint-token" for key, _ in requests)
        assert all(auth == "Bearer dummy-bearer-token" for _, auth in requests)
    finally:
        await kit.aclose()
        server.close()
        await server.wait_closed()


def test_stdio_server_resolves_named_parent_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        kit = AsyncMCPTestKit(env={})
        try:
            binding = StdioServer(
                name="credential-stdio",
                command=sys.executable,
                args=("-u", str(HERE / "stdio_server.py")),
                cwd=str(HERE),
                environment={
                    "DEMO_SERVICE_TOKEN": SecretReference(
                        source="environment", name="M3_DEMO_SERVICE_KEY"
                    )
                },
            )
            # The test process sets this dummy value only for this subprocess test.
            monkeypatch.setenv("M3_DEMO_SERVICE_KEY", "dummy-service-token")
            async with kit.direct(binding) as client:
                result = await client.call_tool("credential_check", {})
                assert result.content[0]["text"] == "stdio credential accepted"
                assert result.structured_content == {"credential_present": True}
        finally:
            await kit.aclose()

    asyncio.run(run())


def test_acp_manifest_resolves_key_in_isolated_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("M3_DEMO_AGENT_KEY", "dummy-agent-key")
    monkeypatch.setenv("M3_DEMO_PARENT_HOME", str(Path.home()))
    manifest = json.loads((HERE / "acp_manifest.json").read_text(encoding="utf-8"))
    manifest["command"] = sys.executable
    manifest["args"] = [str(HERE / "acp_credential_agent.py")]
    selection = {
        "harness": "acp",
        "models": ["fixture"],
        "manifest": manifest,
    }
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=("-u", str(HERE / "shipping_server.py")),
        cwd=str(HERE),
    )
    with MCPTestKit(env={}) as kit:
        agent = kit.agents([selection])[0]
        result = agent.run(
            "Use shipping:shipping_quote with weight_kg 2 and zone local.",
            server=server,
            tools=["shipping:shipping_quote"],
            timeout=20,
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    assert result.trace_view is not None
    assert "dummy-agent-key" not in repr(result.trace_view)
    assert any(
        content.text == "Dummy credential resolved; ACP HOME isolated."
        for message in result.trace_view.messages
        for content in message.content
        if hasattr(content, "text")
    )
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
    captured = result.trace_view.tool_calls[0].result.value
    assert captured.content[0].text == "9.00 USD"
    assert captured.structured_content.value == {"amount": 9.0, "currency": "USD"}
