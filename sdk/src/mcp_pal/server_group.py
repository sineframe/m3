"""Required/optional MCP server groups for a single agent conversation.

The manager owns server descriptors and any SDK-created loopback endpoint for
an in-process server.  It intentionally does not own an agent session or
duplicate the official MCP protocol engine.  A controller can hand the
immutable snapshot and configurations to any :class:`HarnessAdapter`.
"""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import secrets
import socket
import uuid
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import MCPError
from .transport.capture_proxy import McpCaptureManager
from .types import (
    HTTPServer,
    InProcessServer,
    ServerBinding,
    ServerValue,
    SSEServer,
    StdioServer,
    ToolPolicy,
    TransportKind,
    TrustLevel,
)


class ServerGroupError(MCPError):
    """Base class for safe server-group failures."""


class ServerStartupError(ServerGroupError):
    """A required server could not be made available."""


class ServerCleanupError(ServerGroupError):
    """One or more owned server resources failed to close."""


class AmbiguousToolError(ServerGroupError):
    """A tool name maps to more than one available server."""


class ServerUnavailableError(ServerGroupError):
    """A selected server is unavailable."""


@dataclass(frozen=True, slots=True)
class ServerLifecycleEvidence:
    """Safe lifecycle evidence; command, URLs, headers, and stderr are excluded."""

    started: bool = False
    closed: bool = False
    partial: bool = False
    error_kind: str | None = None
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HarnessServerConfig:
    """Non-secret server configuration supplied to an agent adapter."""

    key: str
    transport: TransportKind
    required: bool
    available: bool
    connection_id: str
    endpoint: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    environment: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, Any] = field(default_factory=dict)
    reason: str | None = None
    cwd: str | None = None
    tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", dict(self.environment))
        object.__setattr__(self, "headers", dict(self.headers))


@dataclass(frozen=True, slots=True)
class ServerRecord:
    """One immutable view of a configured server binding."""

    key: str
    server: ServerValue | None
    required: bool
    available: bool
    connection_id: str
    transport: TransportKind | None = None
    endpoint: str | None = None
    reason: str | None = None
    tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ServerGroupSnapshot:
    """Fresh immutable projection of all configured server bindings."""

    records: tuple[ServerRecord, ...] = ()
    evidence: ServerLifecycleEvidence = field(default_factory=ServerLifecycleEvidence)

    def resolve(self, key: str) -> ServerRecord:
        for record in self.records:
            if record.key == key:
                if not record.available:
                    raise ServerUnavailableError("selected MCP server is unavailable")
                return record
        raise ServerUnavailableError("selected MCP server is not configured")

    def candidates(self, tool: str) -> tuple[ServerRecord, ...]:
        return tuple(
            record
            for record in self.records
            if record.available and tool in record.tools
        )

    def route_tool(self, tool: str, *, server: str | None = None) -> ServerRecord:
        if server is not None:
            record = self.resolve(server)
            if tool not in record.tools:
                raise ServerUnavailableError(
                    "requested MCP tool is not advertised by the selected server"
                )
            return record
        matches = self.candidates(tool)
        if not matches:
            raise ServerUnavailableError(
                "requested MCP tool is not advertised by an available server"
            )
        if len(matches) > 1:
            raise AmbiguousToolError("MCP tool requires an explicit server qualifier")
        return matches[0]


def _transport(server: ServerValue) -> TransportKind:
    if isinstance(server, InProcessServer):
        return TransportKind.IN_PROCESS
    if isinstance(server, StdioServer):
        return TransportKind.STDIO
    if isinstance(server, HTTPServer):
        return TransportKind.STREAMABLE_HTTP
    return TransportKind.SSE


def _private_host(host: str) -> bool:
    if host.lower() in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
    )


def _validate_binding(binding: ServerBinding, key: str) -> None:
    server = binding.server
    if server is None:
        raise ServerStartupError(
            "server profile resolution is unavailable in this runtime"
        )
    if isinstance(server, (HTTPServer, SSEServer)):
        parsed = urlsplit(server.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ServerStartupError("MCP server endpoint is invalid")
        if server.trust is TrustLevel.UNTRUSTED and _private_host(parsed.hostname):
            raise ServerStartupError("untrusted MCP server endpoint is private")
    if isinstance(server, StdioServer):
        if "\x00" in server.command or any("\x00" in arg for arg in server.args):
            raise ServerStartupError("stdio server command contains invalid text")
        if server.cwd is not None:
            path = Path(server.cwd)
            if not path.exists() or not path.is_dir():
                raise ServerStartupError(
                    "stdio server working directory is unavailable"
                )
    del key


class _LoopbackEndpoint:
    """Small official-MCP Streamable HTTP endpoint for an in-process server."""

    def __init__(self, server: InProcessServer) -> None:
        self._server_definition = server
        self._token = secrets.token_urlsafe(18)
        self._transport: Any = None
        self._server: Any = None
        self._uvicorn: Any = None
        self._uvicorn_task: asyncio.Task[Any] | None = None
        self._serve_task: asyncio.Task[Any] | None = None
        self._socket: socket.socket | None = None
        self._url: str | None = None
        self._closed = False
        self._capture_writer: Any = None

    def attach_capture(self, writer: Any) -> None:
        """Observe decoded ASGI request/response bodies without changing them."""

        self._capture_writer = writer

    @property
    def url(self) -> str:
        if self._url is None:
            raise ServerStartupError("loopback endpoint is not started")
        return self._url

    async def start(self) -> str:
        try:
            import uvicorn
            from mcp.server.streamable_http import StreamableHTTPServerTransport
            from mcp.server.transport_security import TransportSecuritySettings
        except ImportError:
            raise ServerStartupError(
                "loopback transport dependencies are unavailable"
            ) from None

        try:
            server = self._server_definition.factory()
            if inspect.isawaitable(server):
                server = await server
            if not callable(getattr(server, "run", None)) or not callable(
                getattr(server, "create_initialization_options", None)
            ):
                raise ServerStartupError(
                    "in-process server factory is not an official MCP server"
                )
            transport = StreamableHTTPServerTransport(
                None,
                security_settings=TransportSecuritySettings(
                    allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
                    allowed_origins=[],
                ),
            )
            self._server = server
            self._transport = transport

            async def serve() -> None:
                async with transport.connect() as (read_stream, write_stream):
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                        raise_exceptions=True,
                    )

            self._serve_task = asyncio.create_task(serve())

            class _Application:
                async def __call__(
                    application_self: Any, scope: Any, receive: Any, send: Any
                ) -> None:
                    if scope.get("type") != "http":
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 404,
                                "headers": [],
                            }
                        )
                        await send({"type": "http.response.body", "body": b"not found"})
                        return
                    path = str(scope.get("path", ""))
                    client = scope.get("client")
                    token_path = "/" + self._token
                    if not (
                        path == token_path or path.startswith(token_path + "/")
                    ) or (client and client[0] not in {"127.0.0.1", "::1"}):
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 404,
                                "headers": [],
                            }
                        )
                        await send({"type": "http.response.body", "body": b"not found"})
                        return
                    capture = self._capture_writer
                    request_parts: list[bytes] = []

                    async def observed_receive() -> Any:
                        message = await receive()
                        if (
                            capture is not None
                            and message.get("type") == "http.request"
                        ):
                            body = message.get("body", b"")
                            if body:
                                request_parts.append(body)
                            if not message.get("more_body", False) and request_parts:
                                from .trace.capture import parse_json_payload

                                capture.write(
                                    transport="in_process",
                                    direction="client_to_server",
                                    payload=parse_json_payload(b"".join(request_parts)),
                                    metadata={
                                        "method": scope.get("method", ""),
                                        "path": scope.get("path", ""),
                                    },
                                )
                                request_parts.clear()
                        return message

                    async def observed_send(message: Any) -> None:
                        if (
                            capture is not None
                            and message.get("type") == "http.response.body"
                        ):
                            body = message.get("body", b"")
                            if body:
                                from .trace.capture import parse_json_payload

                                capture.write(
                                    transport="in_process",
                                    direction="server_to_client",
                                    payload=parse_json_payload(body),
                                    metadata={
                                        "status_code": message.get("status", 200)
                                    },
                                )
                        await send(message)

                    await transport.handle_request(
                        scope, observed_receive, observed_send
                    )

            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.bind(("127.0.0.1", 0))
            self._socket.listen(64)
            port = int(self._socket.getsockname()[1])
            self._uvicorn = uvicorn.Server(
                uvicorn.Config(
                    _Application(), log_level="error", lifespan="off", access_log=False
                )
            )
            self._uvicorn_task = asyncio.create_task(
                self._uvicorn.serve(sockets=[self._socket])
            )
            for _ in range(100):
                if self._uvicorn.started:
                    break
                if self._uvicorn_task.done():
                    self._uvicorn_task.result()
                await asyncio.sleep(0.01)
            if not self._uvicorn.started:
                raise ServerStartupError("loopback endpoint did not start")
            self._url = f"http://127.0.0.1:{port}/{self._token}"
            return self._url
        except ServerStartupError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise ServerStartupError("loopback endpoint startup failed") from None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._transport is not None:
            with suppress(Exception):
                await self._transport.terminate()
        if self._uvicorn is not None:
            self._uvicorn.should_exit = True
        if self._uvicorn_task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(self._uvicorn_task), timeout=3.0)
        if self._serve_task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(self._serve_task), timeout=3.0)
        if self._socket is not None:
            self._socket.close()


class ServerGroupManager:
    """Own a required/optional server set for one agent conversation."""

    def __init__(
        self,
        bindings: Iterable[ServerBinding],
        *,
        unavailable: Mapping[str, str] | None = None,
        expose_in_process: bool = True,
        tool_policy: ToolPolicy | None = None,
    ) -> None:
        self._bindings = tuple(bindings)
        if not self._bindings:
            raise ValueError("server group requires at least one binding")
        self._unavailable = dict(unavailable or {})
        self._expose_in_process = expose_in_process
        self._tool_policy = tool_policy
        self._records: dict[str, ServerRecord] = {}
        self._endpoints: dict[str, _LoopbackEndpoint] = {}
        self._started = False
        self._closed = False
        self._evidence = ServerLifecycleEvidence()
        self._capture: McpCaptureManager | None = None
        self._validate_keys()

    def _validate_keys(self) -> None:
        keys: set[str] = set()
        for binding in self._bindings:
            server = binding.server
            key = binding.alias or (server.name if server is not None else "profile")
            if key in keys:
                raise ValueError("server aliases must be unique within an execution")
            keys.add(key)

    def preflight(self) -> ServerGroupSnapshot:
        records: list[ServerRecord] = []
        for binding in self._bindings:
            server = binding.server
            key = binding.alias or (server.name if server is not None else "profile")
            connection_id = self._records.get(
                key, ServerRecord(key, None, binding.required, False, "")
            ).connection_id
            if not connection_id:
                connection_id = "connection-" + uuid.uuid4().hex
            reason = self._unavailable.get(key)
            available = reason is None and server is not None
            try:
                _validate_binding(binding, key)
            except ServerStartupError:
                available = False
                reason = "preflight_failed"
            records.append(
                ServerRecord(
                    key=key,
                    server=server,
                    required=binding.required,
                    available=available,
                    connection_id=connection_id,
                    transport=_transport(server) if server is not None else None,
                    reason=reason,
                )
            )
        return ServerGroupSnapshot(tuple(records), self._evidence)

    async def start(self) -> ServerGroupSnapshot:
        if self._closed:
            raise ServerStartupError("server group is closed")
        if self._started:
            return self.snapshot()
        prepared = self.preflight()
        records: list[ServerRecord] = []
        try:
            for record in prepared.records:
                if not record.available:
                    if record.required:
                        raise ServerStartupError("required MCP server failed preflight")
                    records.append(record)
                    continue
                endpoint = record.endpoint
                server = record.server
                if isinstance(server, InProcessServer) and self._expose_in_process:
                    loopback = _LoopbackEndpoint(server)
                    endpoint = await loopback.start()
                    self._endpoints[record.key] = loopback
                records.append(
                    ServerRecord(
                        key=record.key,
                        server=server,
                        required=record.required,
                        available=True,
                        connection_id=record.connection_id,
                        transport=record.transport,
                        endpoint=endpoint,
                        reason=None,
                        tools=record.tools,
                    )
                )
            self._records = {record.key: record for record in records}
            trusted_private = {
                record.connection_id
                for record in records
                if record.server is not None
                and getattr(record.server, "trust", TrustLevel.UNTRUSTED)
                in {TrustLevel.TRUSTED_PRIVATE, TrustLevel.SDK_LOOPBACK}
                and record.transport
                in {TransportKind.STREAMABLE_HTTP, TransportKind.SSE}
            }
            self._capture = McpCaptureManager(
                trusted_private_keys=trusted_private,
                tool_policy=self._tool_policy,
                server_aliases=tuple(record.key for record in records),
                tools_by_server={record.key: record.tools for record in records},
            )
            raw_configurations = self._raw_configurations()
            instrumented = await self._capture.instrument(raw_configurations)
            for config in instrumented:
                configured_record = self._records.get(config.key)
                if configured_record is None:
                    continue
                if (
                    config.endpoint is not None
                    and config.endpoint != configured_record.endpoint
                ):
                    self._records[configured_record.key] = ServerRecord(
                        key=configured_record.key,
                        server=configured_record.server,
                        required=configured_record.required,
                        available=configured_record.available,
                        connection_id=configured_record.connection_id,
                        transport=configured_record.transport,
                        endpoint=configured_record.endpoint,
                        reason=configured_record.reason,
                        tools=configured_record.tools,
                    )
            for record in records:
                if record.transport is TransportKind.IN_PROCESS:
                    loopback_endpoint = self._endpoints.get(record.key)
                    if loopback_endpoint is not None:
                        self._capture.attach_loopback(
                            record.connection_id, loopback_endpoint
                        )
            self._started = True
            self._evidence = ServerLifecycleEvidence(started=True)
            return self.snapshot()
        except ServerStartupError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise ServerStartupError("MCP server group startup failed") from None

    def snapshot(self) -> ServerGroupSnapshot:
        return ServerGroupSnapshot(tuple(self._records.values()), self._evidence)

    def _raw_configurations(self) -> tuple[HarnessServerConfig, ...]:
        configurations: list[HarnessServerConfig] = []
        for record in self._records.values():
            server = record.server
            if server is None or record.transport is None:
                configurations.append(
                    HarnessServerConfig(
                        key=record.key,
                        transport=record.transport or TransportKind.STDIO,
                        required=record.required,
                        available=False,
                        connection_id=record.connection_id,
                        reason=record.reason or "server_profile_unresolved",
                        tools=record.tools,
                    )
                )
                continue
            if isinstance(server, StdioServer):
                configurations.append(
                    HarnessServerConfig(
                        key=record.key,
                        transport=record.transport,
                        required=record.required,
                        available=record.available,
                        connection_id=record.connection_id,
                        command=server.command,
                        args=server.args,
                        cwd=server.cwd,
                        environment=server.environment,
                        reason=record.reason,
                        tools=record.tools,
                    )
                )
            elif isinstance(server, (HTTPServer, SSEServer)):
                configurations.append(
                    HarnessServerConfig(
                        key=record.key,
                        transport=record.transport,
                        required=record.required,
                        available=record.available,
                        connection_id=record.connection_id,
                        endpoint=record.endpoint or server.url,
                        headers=server.headers,
                        reason=record.reason,
                        tools=record.tools,
                    )
                )
            else:
                configurations.append(
                    HarnessServerConfig(
                        key=record.key,
                        transport=record.transport,
                        required=record.required,
                        available=record.available,
                        connection_id=record.connection_id,
                        endpoint=record.endpoint,
                        reason=record.reason,
                        tools=record.tools,
                    )
                )
        return tuple(configurations)

    def configurations(self) -> tuple[HarnessServerConfig, ...]:
        configurations = self._raw_configurations()
        if self._capture is None or not self._started:
            return configurations
        return tuple(
            self._capture._instrumented.get(config.connection_id, config)
            for config in configurations
        )

    @property
    def capture(self) -> McpCaptureManager | None:
        """Live capture context supplied to harness adapters."""

        return self._capture

    def register_tools(self, server: str, tools: Iterable[str]) -> ServerGroupSnapshot:
        record = self._records.get(server)
        if record is None:
            raise ServerUnavailableError("cannot register tools for an unknown server")
        self._records[server] = ServerRecord(
            key=record.key,
            server=record.server,
            required=record.required,
            available=record.available,
            connection_id=record.connection_id,
            transport=record.transport,
            endpoint=record.endpoint,
            reason=record.reason,
            tools=tuple(dict.fromkeys(str(tool) for tool in tools)),
        )
        return self.snapshot()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failures = 0
        for endpoint in tuple(self._endpoints.values()):
            try:
                await endpoint.close()
            except Exception:
                failures += 1
        self._endpoints.clear()
        if self._capture is not None:
            try:
                await self._capture.close()
            except Exception:
                failures += 1
            self._capture = None
        self._evidence = ServerLifecycleEvidence(
            started=self._started,
            closed=failures == 0,
            partial=failures != 0,
            error_kind="cleanup_failed" if failures else None,
            limitations=("owned_server_cleanup_incomplete",) if failures else (),
        )
        if failures:
            raise ServerCleanupError("MCP server group cleanup failed")


__all__ = [
    "AmbiguousToolError",
    "HarnessServerConfig",
    "ServerCleanupError",
    "ServerGroupError",
    "ServerGroupManager",
    "ServerGroupSnapshot",
    "ServerLifecycleEvidence",
    "ServerRecord",
    "ServerStartupError",
    "ServerUnavailableError",
]
