"""Per-run reverse proxy for Streamable HTTP MCP transport."""

from __future__ import annotations

import asyncio
import codecs
import ipaddress
import json
import os
import re
import socket
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import anyio
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from m3.trace.capture import CaptureWriter, parse_json_payload
from m3.trace.redaction import is_sensitive_key
from m3.types import ToolPolicy

from ._http_pinning import (
    ValidatingHTTPXTransport,
    canonical_hostname,
    safe_endpoint_error_message,
)
from .tool_policy import ProxyToolPolicy

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_UPSTREAM_CONNECT_TIMEOUT = 30.0
_PROXY_START_TIMEOUT = 5.0
_MAX_SSE_FRAME_BYTES = 8 * 1024 * 1024


class UnsafeUpstreamError(ValueError):
    pass


def _expand_env(value: str) -> str:
    result = value
    for key, env_value in os.environ.items():
        result = result.replace(f"${{{key}}}", env_value)
    return result


def _expand_configured(value: str, secrets: set[str]) -> str:
    """Expand configured references, failing closed when one is unavailable."""

    for match in _ENV_REFERENCE.finditer(value):
        resolved = os.environ.get(match.group(1))
        if not resolved:
            raise ValueError("MCP environment reference unavailable")
        secrets.add(resolved)
    return _expand_env(value)


def _resolve_public_host(hostname: str, port: int) -> tuple[str, ...]:
    try:
        addresses = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        raise UnsafeUpstreamError("MCP upstream host could not be resolved") from None
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise UnsafeUpstreamError(
                "public MCP endpoint resolved to a non-public address"
            )
    return tuple(dict.fromkeys(str(address[4][0]) for address in addresses))


def _parse_upstream(url: str) -> tuple[SplitResult, int]:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsafeUpstreamError("MCP upstream must be an HTTP(S) URL")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError):
        raise UnsafeUpstreamError("MCP upstream must be an HTTP(S) URL") from None
    return parsed, port


def validate_public_upstream(url: str) -> tuple[str, ...]:
    parsed, port = _parse_upstream(url)
    assert parsed.hostname is not None
    return _resolve_public_host(parsed.hostname, port)


def _resolve_loopback_host(hostname: str, port: int) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        raise UnsafeUpstreamError("MCP upstream host could not be resolved") from None
    addresses = tuple(dict.fromkeys(str(item[4][0]) for item in records))
    if not addresses:
        raise UnsafeUpstreamError("MCP upstream host could not be resolved")
    try:
        safe = all(ipaddress.ip_address(address).is_loopback for address in addresses)
    except ValueError:
        safe = False
    if not safe:
        raise UnsafeUpstreamError(
            "loopback-only MCP endpoint resolved to a non-loopback address"
        )
    return addresses


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
        loopback_only: bool = False,
        secrets: set[str] | None = None,
        tool_policy: ToolPolicy | None = None,
        server_alias: str = "server",
        known_servers: tuple[str, ...] = (),
        known_tools: tuple[str, ...] = (),
        known_tools_by_server: dict[str, tuple[str, ...]] | None = None,
        writer: CaptureWriter | None = None,
        on_incomplete: Callable[[str], None] | None = None,
    ):
        self.transport = transport
        writer_secrets = set(secrets or ())
        self.upstream_url = _expand_configured(upstream_url, writer_secrets)
        self.configured_headers = {
            key: _expand_configured(value, writer_secrets)
            for key, value in (configured_headers or {}).items()
        }
        writer_secrets.update(
            value
            for key, value in self.configured_headers.items()
            if is_sensitive_key(key) and value
        )
        writer_config = (
            writer_secrets if secrets is not None or writer_secrets else None
        )
        self.writer = writer or CaptureWriter(
            capture_path, baseline_ns, secrets=writer_config
        )
        self._on_incomplete = on_incomplete
        if writer is not None and writer_secrets:
            writer.add_secrets(writer_secrets)
        if secrets is not None:
            secrets.update(writer_secrets)
        self.allow_private = allow_private
        self.loopback_only = loopback_only
        self._tool_policy = (
            ProxyToolPolicy(
                tool_policy,
                server=server_alias,
                known_servers=known_servers,
                known_tools=known_tools,
                known_tools_by_server=known_tools_by_server,
            )
            if tool_policy is not None
            else None
        )
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task[Any] | None = None
        self.socket: socket.socket | None = None
        self.proxy_origin: str | None = None
        self.client: httpx.AsyncClient | None = None
        upstream = urlsplit(self.upstream_url)
        self.origin = urlunsplit((upstream.scheme, upstream.netloc, "", "", ""))
        self.initial_path = upstream.path or "/"
        self.initial_query = upstream.query

    async def start(self) -> str:
        upstream, upstream_port = _parse_upstream(self.upstream_url)
        assert upstream.hostname is not None
        if self.allow_private and not self.loopback_only:
            # This explicit opt-out is used for loopback fixtures and deliberately
            # does not apply the public-upstream SSRF boundary.
            self.client = httpx.AsyncClient(follow_redirects=False, timeout=None)
        else:
            resolver = (
                _resolve_loopback_host if self.loopback_only else _resolve_public_host
            )
            try:
                with anyio.fail_after(_UPSTREAM_CONNECT_TIMEOUT):
                    initial_addresses = await anyio.to_thread.run_sync(
                        resolver,
                        upstream.hostname,
                        upstream_port,
                        abandon_on_cancel=True,
                    )
            except TimeoutError:
                raise UnsafeUpstreamError(
                    "MCP upstream host resolution timed out"
                ) from None
            initial_available = True
            initial_lock = threading.Lock()
            upstream_hostname = canonical_hostname(upstream.hostname)

            def resolve_upstream(hostname: str, requested_port: int) -> tuple[str, ...]:
                nonlocal initial_available
                with initial_lock:
                    if (
                        initial_available
                        and canonical_hostname(hostname) == upstream_hostname
                        and requested_port == upstream_port
                    ):
                        initial_available = False
                        return initial_addresses
                return resolver(hostname, requested_port)

            self.client = httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(None, connect=_UPSTREAM_CONNECT_TIMEOUT),
                trust_env=False,
                transport=ValidatingHTTPXTransport(
                    scheme=upstream.scheme,
                    hostname=upstream.hostname,
                    port=upstream_port,
                    resolve_addresses=resolve_upstream,
                ),
            )
        app = Starlette(
            routes=[
                Route(
                    "/{path:path}",
                    self._forward,
                    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                ),
                Route(
                    "/",
                    self._forward,
                    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                ),
            ]
        )
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        proxy_port = self.socket.getsockname()[1]
        self.proxy_origin = f"http://127.0.0.1:{proxy_port}"
        config = uvicorn.Config(
            app, log_level="error", lifespan="off", access_log=False
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
        with anyio.move_on_after(_PROXY_START_TIMEOUT) as startup_scope:
            while not self.server.started:
                if self.task.done():
                    await self.task
                    raise RuntimeError("MCP HTTP proxy exited before startup completed")
                await asyncio.sleep(0.01)
        if startup_scope.cancel_called:
            await self.stop()
            raise TimeoutError(
                "MCP HTTP proxy failed to start within "
                f"{_PROXY_START_TIMEOUT:g} seconds"
            ) from None
        query = f"?{self.initial_query}" if self.initial_query else ""
        return f"{self.proxy_origin}{self.initial_path}{query}"

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
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP
        }
        headers.update(
            {
                key: value
                for key, value in self.configured_headers.items()
                if key.lower() not in HOP_BY_HOP
            }
        )
        return headers

    def _response_headers(
        self, response: httpx.Response, *, base_url: str | None = None
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in response.headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            if key.lower() == "location":
                # Never hand the upstream origin to the harness: following
                # it would bypass this proxy (and its capture/policy gate).
                try:
                    target = urlsplit(urljoin(base_url or self.upstream_url, value))
                    upstream = urlsplit(self.upstream_url)
                    target_port = target.port or (
                        443 if target.scheme == "https" else 80
                    )
                    upstream_port = upstream.port or (
                        443 if upstream.scheme == "https" else 80
                    )
                except ValueError:
                    continue
                if (
                    target.scheme.lower(),
                    (target.hostname or "").lower(),
                    target_port,
                ) != (
                    upstream.scheme.lower(),
                    (upstream.hostname or "").lower(),
                    upstream_port,
                ):
                    continue
                if self.proxy_origin is None:
                    continue
                proxy = urlsplit(self.proxy_origin)
                headers[key] = urlunsplit(
                    (
                        proxy.scheme,
                        proxy.netloc,
                        target.path or "/",
                        target.query,
                        target.fragment,
                    )
                )
                continue
            headers[key] = value
        return headers

    async def _forward(self, request: Request) -> Response:
        assert self.client is not None
        body = await request.body()
        target = self._target_url(request)
        # Capture every MCP exchange.
        # Header values are intentionally not persisted; CaptureWriter redacts
        # the URL query and credential-shaped metadata fields.
        payload = parse_json_payload(body) if body else None
        if self._tool_policy is not None:
            if isinstance(payload, list):
                for item in payload:
                    self._tool_policy.observe_request(item)
            else:
                self._tool_policy.observe_request(payload)
        request_payload = payload if isinstance(payload, dict) else {}
        denied: tuple[bool, str] | None = None
        denied_batch: list[dict[str, Any]] | None = None
        if isinstance(payload, dict) and payload.get("method") == "tools/call":
            params = payload.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            if self._tool_policy is not None:
                allowed, reason = self._tool_policy.decide(name)
                if not allowed:
                    denied = (allowed, reason)
        elif isinstance(payload, list) and self._tool_policy is not None:
            denied_batch = []
            for item in payload:
                if not isinstance(item, dict) or item.get("method") != "tools/call":
                    continue
                params = item.get("params")
                name = params.get("name") if isinstance(params, dict) else None
                allowed, reason = self._tool_policy.decide(name)
                if not allowed:
                    denied_batch.append({"id": item.get("id"), "reason": reason})
            if not denied_batch:
                denied_batch = None
        capture_payload = payload
        if denied_batch is not None and isinstance(payload, list):
            capture_payload = [
                {
                    "jsonrpc": item.get("jsonrpc", "2.0"),
                    "id": item.get("id"),
                    "method": "tools/call",
                    "params": {
                        "name": item.get("params", {}).get("name")
                        if isinstance(item.get("params"), dict)
                        else None
                    },
                }
                if isinstance(item, dict) and item.get("method") == "tools/call"
                else {"policy_batch_item": "redacted"}
                for item in payload
            ]
        if denied is not None:
            # Policy evidence must never retain caller arguments.  The
            # JSON-RPC id is kept so the denial can be correlated safely.
            capture_payload = {
                "jsonrpc": request_payload.get("jsonrpc", "2.0"),
                "id": request_payload.get("id"),
                "method": "tools/call",
                "params": {
                    "name": request_payload.get("params", {}).get("name")
                    if isinstance(request_payload.get("params"), dict)
                    else None
                },
            }
        self.writer.write(
            transport=self.transport,
            direction="client_to_server",
            payload=capture_payload,
            kind="policy_denied"
            if denied is not None or denied_batch is not None
            else "jsonrpc",
            metadata={
                "method": request.method,
                "url": target,
                "content_type": request.headers.get("content-type"),
                **(
                    {"policy_denied": True}
                    if denied is not None or denied_batch is not None
                    else {}
                ),
                **({"policy_reason": denied[1]} if denied is not None else {}),
            },
        )
        if denied is not None:
            if "id" not in request_payload:
                return Response(b"", status_code=202)
            response_payload = {
                "jsonrpc": request_payload.get("jsonrpc", "2.0"),
                "id": request_payload.get("id"),
                "error": {"code": -32001, "message": "MCP tool call denied by policy"},
            }
            self.writer.write(
                transport=self.transport,
                direction="server_to_client",
                payload=response_payload,
                kind="policy_denied",
            )
            return Response(
                json.dumps(response_payload).encode("utf-8"),
                status_code=200,
                media_type="application/json",
            )
        if denied_batch is not None:
            batch_response: Any = [
                {
                    "jsonrpc": "2.0",
                    "id": item.get("id"),
                    "error": {"code": -32001, "message": "MCP batch denied by policy"},
                }
                for item in (payload if isinstance(payload, list) else ())
                if isinstance(item, dict) and "id" in item
            ]
            if not batch_response:
                return Response(b"", status_code=202)
            self.writer.write(
                transport=self.transport,
                direction="server_to_client",
                payload=batch_response,
                kind="policy_denied",
            )
            return Response(
                json.dumps(batch_response).encode("utf-8"),
                status_code=200,
                media_type="application/json",
            )
        outbound = self.client.build_request(
            request.method, target, headers=self._request_headers(request), content=body
        )
        try:
            response = await self.client.send(outbound, stream=True)
        except httpx.HTTPError as exc:
            trust_message = safe_endpoint_error_message(exc)
            self.writer.write(
                transport=self.transport,
                direction="proxy_error",
                payload={"error": type(exc).__name__},
                kind="error",
                metadata={"url": target},
            )
            return Response(
                trust_message or "MCP upstream request failed", status_code=502
            )

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return StreamingResponse(
                self._stream_sse(response),
                status_code=response.status_code,
                headers=self._response_headers(response, base_url=target),
                media_type="text/event-stream",
            )
        data = await response.aread()
        await response.aclose()
        if data:
            response_payload = parse_json_payload(data)
            if self._tool_policy is not None:
                self._tool_policy.observe(response_payload)
            self.writer.write(
                transport=self.transport,
                direction="server_to_client",
                payload=response_payload,
                metadata={
                    "status_code": response.status_code,
                    "content_type": content_type,
                },
            )
        return Response(
            data,
            status_code=response.status_code,
            headers=self._response_headers(response, base_url=target),
            media_type=None,
        )

    async def _stream_sse(self, response: httpx.Response) -> AsyncIterator[bytes]:
        buffer = ""
        buffer_bytes = 0
        decoder = codecs.getincrementaldecoder("utf-8")()
        passthrough = False

        def fail_observation() -> None:
            callback = self._on_incomplete
            if callback is not None:
                try:
                    callback("message_too_large")
                except Exception:
                    # Observation failure must not interrupt response streaming.
                    pass

        async def consume_text(text: str) -> AsyncIterator[bytes]:
            nonlocal buffer, buffer_bytes, passthrough
            offset = 0
            while offset < len(text):
                if passthrough:
                    yield text[offset:].encode("utf-8")
                    return
                piece = text[offset : offset + 64 * 1024]
                offset += len(piece)
                buffer += piece
                buffer_bytes += len(piece.encode("utf-8"))
                while True:
                    separator = re.search(r"\r\n\r\n|\n\n|\r\r", buffer)
                    if separator is None:
                        break
                    frame = buffer[: separator.start()]
                    consumed = buffer[: separator.end()]
                    remainder = buffer[separator.end() :]
                    frame_size = len(frame.encode("utf-8"))
                    if frame_size > _MAX_SSE_FRAME_BYTES:
                        fail_observation()
                        yield (consumed + remainder).encode("utf-8")
                        buffer = ""
                        buffer_bytes = 0
                        passthrough = True
                        return
                    rewritten = self._capture_sse_frame(frame)
                    yield (rewritten + "\n\n").encode("utf-8")
                    buffer = remainder
                    buffer_bytes -= len(consumed.encode("utf-8"))
                if buffer_bytes > _MAX_SSE_FRAME_BYTES:
                    fail_observation()
                    yield buffer.encode("utf-8")
                    buffer = ""
                    buffer_bytes = 0
                    passthrough = True
                    return

        try:
            async for chunk in response.aiter_bytes():
                async for output in consume_text(decoder.decode(chunk)):
                    yield output
            async for output in consume_text(decoder.decode(b"", final=True)):
                yield output
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
            if self._tool_policy is not None:
                self._tool_policy.observe(parse_json_payload(data))
            self.writer.write(
                transport=self.transport,
                direction="server_to_client",
                payload=parse_json_payload(data),
                kind="sse_data",
            )
            if self.socket and (data.startswith(self.origin) or data.startswith("/")):
                port = self.socket.getsockname()[1]
                suffix = (
                    data[len(self.origin) :] if data.startswith(self.origin) else data
                )
                data = "http://127.0.0.1:" + str(port) + suffix
            output.append("data: " + data)
        return "\n".join(output)
