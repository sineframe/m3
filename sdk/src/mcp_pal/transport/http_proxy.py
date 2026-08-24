"""Per-run reverse proxy for streamable HTTP and legacy SSE MCP transports."""

from __future__ import annotations

import asyncio
import codecs
import ipaddress
import os
import re
import socket
from typing import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from mcp_pal.trace.capture import CaptureWriter, parse_json_payload

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


class UnsafeUpstreamError(ValueError):
    pass


def _expand_env(value: str) -> str:
    result = value
    for key, env_value in os.environ.items():
        result = result.replace(f"${{{key}}}", env_value)
    return result


def validate_public_upstream(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUpstreamError("MCP upstream must be an HTTP(S) URL")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUpstreamError(f"MCP upstream host could not be resolved: {parsed.hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise UnsafeUpstreamError(f"MCP upstream resolves to a blocked non-public address: {ip}")


class McpHttpProxy:
    def __init__(
        self,
        *,
        upstream_url: str,
        configured_headers: dict[str, str] | None,
        transport: str,
        capture_path: str,
        baseline_ns: int,
        allow_private: bool = False,
    ):
        self.upstream_url = _expand_env(upstream_url)
        self.configured_headers = {key: _expand_env(value) for key, value in (configured_headers or {}).items()}
        self.transport = transport
        self.writer = CaptureWriter(capture_path, baseline_ns)
        self.allow_private = allow_private
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task | None = None
        self.socket: socket.socket | None = None
        self.client: httpx.AsyncClient | None = None
        upstream = urlsplit(self.upstream_url)
        self.origin = urlunsplit((upstream.scheme, upstream.netloc, "", "", ""))
        self.initial_path = upstream.path or "/"
        self.initial_query = upstream.query

    async def start(self) -> str:
        if not self.allow_private:
            await asyncio.to_thread(validate_public_upstream, self.upstream_url)
        self.client = httpx.AsyncClient(follow_redirects=False, timeout=None)
        app = Starlette(routes=[Route("/{path:path}", self._forward, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]), Route("/", self._forward, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])])
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        port = self.socket.getsockname()[1]
        config = uvicorn.Config(app, log_level="error", lifespan="off", access_log=False)
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
        for _ in range(100):
            if self.server.started:
                break
            if self.task.done():
                await self.task
            await asyncio.sleep(0.01)
        query = f"?{self.initial_query}" if self.initial_query else ""
        return f"http://127.0.0.1:{port}{self.initial_path}{query}"

    async def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=3)
            except asyncio.TimeoutError:
                self.task.cancel()
        if self.client:
            await self.client.aclose()
        if self.socket:
            self.socket.close()

    def _target_url(self, request: Request) -> str:
        path = request.url.path
        query = request.url.query
        return f"{self.origin}{path}" + (f"?{query}" if query else "")

    def _request_headers(self, request: Request) -> dict[str, str]:
        headers = {key: value for key, value in request.headers.items() if key.lower() not in HOP_BY_HOP}
        headers.update({key: value for key, value in self.configured_headers.items() if key.lower() not in HOP_BY_HOP})
        return headers

    def _response_headers(self, response: httpx.Response) -> dict[str, str]:
        return {key: value for key, value in response.headers.items() if key.lower() not in HOP_BY_HOP}

    async def _forward(self, request: Request) -> Response:
        assert self.client is not None
        body = await request.body()
        target = self._target_url(request)
        if not self.allow_private:
            try:
                await asyncio.to_thread(validate_public_upstream, target)
            except UnsafeUpstreamError:
                return Response("MCP upstream destination is blocked", status_code=502)
        # Capture every MCP exchange, including GET-based SSE handshakes.
        # Header values are intentionally not persisted; CaptureWriter redacts
        # the URL query and credential-shaped metadata fields.
        self.writer.write(
            transport=self.transport,
            direction="client_to_server",
            payload=parse_json_payload(body) if body else None,
            metadata={"method": request.method, "url": target, "content_type": request.headers.get("content-type")},
        )
        outbound = self.client.build_request(request.method, target, headers=self._request_headers(request), content=body)
        try:
            response = await self.client.send(outbound, stream=True)
        except httpx.HTTPError as exc:
            self.writer.write(transport=self.transport, direction="proxy_error", payload={"error": type(exc).__name__}, kind="error", metadata={"url": target})
            return Response("MCP upstream request failed", status_code=502)

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return StreamingResponse(
                self._stream_sse(response),
                status_code=response.status_code,
                headers=self._response_headers(response),
                media_type="text/event-stream",
            )
        data = await response.aread()
        await response.aclose()
        if data:
            self.writer.write(
                transport=self.transport,
                direction="server_to_client",
                payload=parse_json_payload(data),
                metadata={"status_code": response.status_code, "content_type": content_type},
            )
        return Response(data, status_code=response.status_code, headers=self._response_headers(response), media_type=None)

    async def _stream_sse(self, response: httpx.Response) -> AsyncIterator[bytes]:
        buffer = ""
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            async for chunk in response.aiter_bytes():
                buffer += decoder.decode(chunk)
                while True:
                    separator = re.search(r"\r\n\r\n|\n\n|\r\r", buffer)
                    if not separator:
                        break
                    frame = buffer[:separator.start()]
                    buffer = buffer[separator.end():]
                    rewritten = self._capture_sse_frame(frame)
                    yield (rewritten + "\n\n").encode("utf-8")
            buffer += decoder.decode(b"", final=True)
            if buffer:
                yield self._capture_sse_frame(buffer).encode("utf-8")
        finally:
            await response.aclose()

    def _capture_sse_frame(self, frame: str) -> str:
        output: list[str] = []
        for line in frame.splitlines():
            if not line.startswith("data:"):
                output.append(line)
                continue
            data = line[5:].lstrip()
            self.writer.write(transport=self.transport, direction="server_to_client", payload=parse_json_payload(data), kind="sse_data")
            if self.socket and (data.startswith(self.origin) or data.startswith("/")):
                port = self.socket.getsockname()[1]
                suffix = data[len(self.origin):] if data.startswith(self.origin) else data
                data = "http://127.0.0.1:" + str(port) + suffix
            output.append("data: " + data)
        return "\n".join(output)
