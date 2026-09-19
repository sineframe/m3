"""Ephemeral dependency-free HTTP/SSE MCP echo service for ACP probes.

The service deliberately lives in application code (rather than tests) so the
full probe exercises the same selected transport and proxy path as a run.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast


class EchoMcpHttpServer:
    def __init__(self, transport: str):
        if transport not in {"http", "sse"}:
            raise ValueError("transport must be http or sse")
        self.transport = transport
        self.server: asyncio.AbstractServer | None = None
        self._sse_writer: asyncio.StreamWriter | None = None
        self._sse_response: asyncio.Future[dict[str, Any]] | None = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._connection, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        return (
            f"http://127.0.0.1:{port}/mcp"
            if self.transport == "http"
            else f"http://127.0.0.1:{port}/sse"
        )

    async def stop(self) -> None:
        if self._sse_response and not self._sse_response.done():
            self._sse_response.cancel()
        if self._sse_writer:
            self._sse_writer.close()
            try:
                await self._sse_writer.wait_closed()
            except (ConnectionError, asyncio.CancelledError):
                pass
            self._sse_writer = None
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
            method, path, _ = first.split(" ", 2)
            parsed_headers: dict[str, str] = {}
            for line in header_lines:
                if ":" in line:
                    key, value = line.split(":", 1)
                    parsed_headers[key.lower()] = value.strip()
            length = int(parsed_headers.get("content-length", "0"))
            body = await reader.readexactly(length) if length else b""
            request = json.loads(body.decode("utf-8")) if body else {}
            if self.transport == "sse":
                keep_open = await self._sse(method, path, request, writer)
            else:
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

    async def _sse(
        self,
        method: str,
        path: str,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> bool:
        if method == "GET":
            self._sse_writer = writer
            self._sse_response = asyncio.get_running_loop().create_future()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n"
            )
            writer.write(b"event: endpoint\ndata: /message?session=probe\n\n")
            await writer.drain()
            try:
                response = await self._sse_response
                writer.write(
                    b"data: "
                    + json.dumps(response, separators=(",", ":")).encode("utf-8")
                    + b"\n\n"
                )
                await writer.drain()
            except (asyncio.CancelledError, ConnectionError):
                return True
            finally:
                self._sse_writer = None
                self._sse_response = None
            return False
        if method == "POST":
            if self._sse_response and not self._sse_response.done():
                self._sse_response.set_result(self._result(request))
            writer.write(
                b"HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
        return False
