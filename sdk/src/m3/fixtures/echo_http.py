"""Ephemeral dependency-free Streamable HTTP MCP echo service for ACP probes.

The service deliberately lives in application code (rather than tests) so the
full probe exercises the same selected transport and proxy path as a run.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast


class EchoMcpHttpServer:
    def __init__(self, transport: str):
        if transport != "http":
            raise ValueError("transport must be http")
        self.transport = transport
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._connection, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/mcp"

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    @staticmethod
    def _result(request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        value: Any
        if method == "initialize":
            value = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "probe-echo", "version": "1"},
            }
        elif method == "tools/list":
            value = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Return the provided nonce unchanged.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }
                ]
            }
        elif method == "tools/call":
            args = cast(
                dict[str, Any], (request.get("params") or {}).get("arguments") or {}
            )
            value = {
                "content": [{"type": "text", "text": args.get("text", "")}],
                "isError": False,
            }
        else:
            value = {}
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": value}

    async def _connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        keep_open = False
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            first, *header_lines = headers.decode("latin1").split("\r\n")
            _method, _path, _ = first.split(" ", 2)
            parsed_headers: dict[str, str] = {}
            for line in header_lines:
                if ":" in line:
                    key, value = line.split(":", 1)
                    parsed_headers[key.lower()] = value.strip()
            length = int(parsed_headers.get("content-length", "0"))
            body = await reader.readexactly(length) if length else b""
            request = json.loads(body.decode("utf-8")) if body else {}
            await self._http(request, writer)
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
            json.JSONDecodeError,
        ):
            pass
        finally:
            if not keep_open:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, asyncio.CancelledError):
                    pass

    async def _http(
        self, request: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        body = json.dumps(self._result(request), separators=(",", ":")).encode("utf-8")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
        await writer.drain()
