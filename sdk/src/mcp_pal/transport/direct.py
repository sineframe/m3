"""Remote MCP transport adapters built on the official MCP client.

This module owns connection lifecycle and transport policy only.  JSON-RPC
encoding, request dispatch, pagination, and callbacks remain in
``mcp.ClientSession``.  Resolved credentials exist only in the HTTP client
created for a connection and are never included in evidence or reprs.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from types import MappingProxyType, TracebackType
from typing import Any, Literal, Protocol, TypeAlias, cast
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import anyio
import httpx2
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import (
    MCP_DEFAULT_SSE_READ_TIMEOUT,
    MCP_DEFAULT_TIMEOUT,
    McpHttpClientFactory,
)

from ..direct_trace import DirectTraceBridge
from ..trace.redaction import is_sensitive_key
from ..types import HTTPServer, SecretReference, SSEServer, TrustLevel
from ._http_pinning import ValidatingHTTPX2Transport, canonical_hostname

TransportName: TypeAlias = Literal["streamable_http", "sse"]
HostResolver: TypeAlias = Callable[[str, int], tuple[str, ...]]


def _safe_mcp_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    *,
    validated_origin: tuple[str, str, int] | None = None,
    resolve_addresses: HostResolver | None = None,
    allow_public_auth_origins: bool = False,
    first_connect_deadline: float | None = None,
) -> httpx2.AsyncClient:
    """Create an MCP client that never follows an untrusted redirect.

    The official helper enables redirects globally.  A redirect can otherwise
    move a previously validated public endpoint to a private or metadata
    address without re-running endpoint trust validation.
    """

    effective_timeout = timeout or httpx2.Timeout(
        MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT
    )
    transport: httpx2.AsyncBaseTransport = (
        ValidatingHTTPX2Transport(
            scheme=validated_origin[0],
            hostname=validated_origin[1],
            port=validated_origin[2],
            resolve_addresses=resolve_addresses,
            allow_public_auth_origins=allow_public_auth_origins,
            first_connect_deadline=first_connect_deadline,
        )
        if validated_origin is not None and resolve_addresses is not None
        else httpx2.AsyncHTTPTransport(trust_env=True)
    )
    return httpx2.AsyncClient(
        headers=headers,
        timeout=effective_timeout,
        auth=auth,
        follow_redirects=False,
        transport=transport,
        trust_env=False,
    )


class TransportConnectionError(RuntimeError):
    """Safe lifecycle failure; the underlying provider error is not retained."""

    def __init__(
        self,
        transport: TransportName,
        phase: str,
        *,
        evidence: TransportEvidence | None = None,
    ) -> None:
        self.transport = transport
        self.phase = phase
        self.evidence = evidence
        super().__init__(f"{transport} connection failed during {phase}")


class EndpointTrustError(ValueError):
    """Raised when an endpoint is not safe for the requested trust level."""

    def __init__(
        self, reason: str = "endpoint trust policy rejected the destination"
    ) -> None:
        # Keep this vocabulary value-free: hostnames can contain tenant or
        # credential material supplied by an untrusted profile.
        self.reason = (
            reason
            if isinstance(reason, str)
            and reason
            in {
                "endpoint trust policy rejected the destination",
                "endpoint URL is invalid",
                "endpoint hostname could not be resolved",
                "endpoint URL contains credential query parameters",
            }
            else "endpoint trust policy rejected the destination"
        )
        super().__init__(self.reason)


class SecretResolver(Protocol):
    """Resolve a secret reference at connection time only."""

    def resolve(self, reference: SecretReference) -> str: ...


SecretObserver: TypeAlias = Callable[[str], None]


class EnvironmentSecretResolver:
    """Resolver for environment references, with optional provider secrets."""

    def __init__(self, *, providers: Mapping[str, str] | None = None) -> None:
        self._providers = dict(providers or {})

    def resolve(self, reference: SecretReference) -> str:
        if reference.source == "environment":
            value = os.environ.get(reference.name)
        else:
            value = self._providers.get(reference.name)
        if not value:
            raise TransportConnectionError("streamable_http", "authentication")
        return value


@dataclass(frozen=True, slots=True)
class TransportEvent:
    """Safe lifecycle evidence; payloads and headers are intentionally absent."""

    kind: str
    offset_ms: float
    details: Mapping[str, str | int | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True, slots=True, repr=False)
class TransportEvidence:
    """Immutable sanitized evidence available after failed or partial setup."""

    transport: TransportName
    endpoint: str
    state: Literal["created", "connecting", "initialized", "closed", "failed"]
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    instructions: bool = False
    capabilities: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    events: tuple[TransportEvent, ...] = ()
    error_type: str | None = None

    def __repr__(self) -> str:
        return (
            "TransportEvidence("
            f"transport={self.transport!r}, endpoint={self.endpoint!r}, "
            f"state={self.state!r}, protocol_version={self.protocol_version!r}, "
            f"events={len(self.events)}, error_type={self.error_type!r})"
        )


def _safe_endpoint(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError
        host = parts.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host
        if parts.port is not None:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path or "/", "", ""))
    except (TypeError, ValueError):
        return "<invalid-endpoint>"


def _default_host_resolver(host: str, port: int) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        raise EndpointTrustError("endpoint hostname could not be resolved") from None
    addresses = tuple(dict.fromkeys(str(item[4][0]) for item in records))
    if not addresses:
        raise EndpointTrustError("endpoint hostname could not be resolved")
    return addresses


def _is_private_or_local(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return True
    return not parsed.is_global


def _is_credential_query_name(name: str) -> bool:
    normalized = "".join(character for character in name.lower() if character.isalnum())
    return (
        normalized
        in {
            "accesskey",
            "accesstoken",
            "apikey",
            "auth",
            "authorization",
            "clientid",
            "clientsecret",
            "credential",
            "idtoken",
            "jwt",
            "password",
            "privatekey",
            "refreshtoken",
            "secret",
            "sessiontoken",
            "token",
        }
        or normalized.endswith("token")
        or normalized.endswith("secret")
    )


def _endpoint_trust_details(
    server: HTTPServer | SSEServer,
    *,
    for_agent: bool,
    resolve_host: HostResolver,
) -> tuple[str, str, str, int, tuple[str, ...]]:
    try:
        parsed = urlsplit(server.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise EndpointTrustError("endpoint URL is invalid")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError):
        raise EndpointTrustError("endpoint URL is invalid") from None
    if any(
        _is_credential_query_name(name)
        for name, _value in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        raise EndpointTrustError("endpoint URL contains credential query parameters")
    try:
        addresses = resolve_host(parsed.hostname, port)
    except EndpointTrustError:
        raise
    except BaseException:
        raise EndpointTrustError("endpoint hostname could not be resolved") from None
    if not addresses:
        raise EndpointTrustError("endpoint hostname could not be resolved")
    private = any(_is_private_or_local(address) for address in addresses)
    trusted = server.trust in {TrustLevel.TRUSTED_PRIVATE, TrustLevel.SDK_LOOPBACK}
    if private and not trusted:
        raise EndpointTrustError()
    if for_agent and server.trust is TrustLevel.UNTRUSTED:
        raise EndpointTrustError()
    if for_agent and private and server.trust is not TrustLevel.TRUSTED_PRIVATE:
        raise EndpointTrustError()
    return _safe_endpoint(server.url), parsed.scheme, parsed.hostname, port, addresses


def validate_endpoint_trust(
    server: HTTPServer | SSEServer,
    *,
    for_agent: bool = False,
    resolve_host: HostResolver = _default_host_resolver,
) -> str:
    """Validate a remote endpoint before opening an outbound connection.

    Private/local destinations require an explicit trusted classification.  A
    public endpoint is still resolved before connecting to prevent a hostname
    that currently points at a private address from bypassing the policy.
    ``for_agent`` is explicit so callers cannot accidentally expose an
    untrusted direct endpoint to an agent runtime.
    """

    safe_endpoint, _scheme, _hostname, _port, _addresses = _endpoint_trust_details(
        server, for_agent=for_agent, resolve_host=resolve_host
    )
    return safe_endpoint


def _validated_transport_policy(
    server: HTTPServer | SSEServer,
    *,
    for_agent: bool,
    resolve_host: HostResolver,
) -> tuple[tuple[str, str, int], HostResolver]:
    """Validate the primary origin and build a per-connection DNS policy."""

    try:
        parsed = urlsplit(server.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise EndpointTrustError("endpoint URL is invalid")
        scheme = parsed.scheme
        hostname = parsed.hostname
        port = parsed.port or (443 if scheme == "https" else 80)
    except (TypeError, ValueError):
        raise EndpointTrustError("endpoint URL is invalid") from None
    if any(
        _is_credential_query_name(name)
        for name, _value in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        raise EndpointTrustError("endpoint URL contains credential query parameters")
    if for_agent and server.trust is TrustLevel.UNTRUSTED:
        raise EndpointTrustError()
    primary_hostname = canonical_hostname(hostname)

    def guarded_resolver(request_host: str, request_port: int) -> tuple[str, ...]:
        try:
            addresses = resolve_host(request_host, request_port)
        except EndpointTrustError:
            raise
        except BaseException:
            raise EndpointTrustError(
                "endpoint hostname could not be resolved"
            ) from None
        if not addresses:
            raise EndpointTrustError("endpoint hostname could not be resolved")

        is_primary = (
            canonical_hostname(request_host) == primary_hostname
            and request_port == port
        )
        private = any(_is_private_or_local(address) for address in addresses)
        if is_primary:
            trusted = server.trust in {
                TrustLevel.TRUSTED_PRIVATE,
                TrustLevel.SDK_LOOPBACK,
            }
            if private and not trusted:
                raise EndpointTrustError()
            if for_agent and server.trust is TrustLevel.UNTRUSTED:
                raise EndpointTrustError()
            if for_agent and private and server.trust is not TrustLevel.TRUSTED_PRIVATE:
                raise EndpointTrustError()
        elif private:
            # Authentication metadata and token endpoints may be cross-origin,
            # but they never inherit the resource server's private trust grant.
            raise EndpointTrustError()
        return addresses

    return (scheme, hostname, port), guarded_resolver


def _header_value(
    value: SecretReference | str,
    resolver: SecretResolver,
) -> str:
    try:
        resolved = (
            resolver.resolve(value) if isinstance(value, SecretReference) else value
        )
        unsafe = "\r" in resolved or "\n" in resolved
    except TransportConnectionError:
        raise
    except BaseException:
        raise TransportConnectionError("streamable_http", "authentication") from None
    if unsafe:
        raise TransportConnectionError("streamable_http", "authentication")
    return resolved


def _observe_secret(observer: SecretObserver | None, value: str) -> None:
    if observer is None:
        return
    try:
        observer(value)
    except TransportConnectionError:
        raise
    except BaseException:
        raise TransportConnectionError("streamable_http", "authentication") from None


def resolve_headers(
    headers: Mapping[str, SecretReference | str],
    *,
    resolver: SecretResolver | None = None,
    bearer_token: SecretReference | None = None,
    secret_observer: SecretObserver | None = None,
) -> dict[str, str]:
    """Resolve transport headers without returning them from evidence."""

    secret_resolver = resolver or EnvironmentSecretResolver()
    resolved: dict[str, str] = {}
    try:
        items = tuple(headers.items())
    except BaseException:
        raise TransportConnectionError("streamable_http", "authentication") from None
    for name, value in items:
        try:
            unsafe = not name or "\r" in name or "\n" in name
        except BaseException:
            raise TransportConnectionError(
                "streamable_http", "authentication"
            ) from None
        if unsafe:
            raise TransportConnectionError("streamable_http", "authentication")
        resolved_value = _header_value(value, secret_resolver)
        if isinstance(value, SecretReference) or is_sensitive_key(name):
            _observe_secret(secret_observer, resolved_value)
        resolved[name] = resolved_value
    if bearer_token is not None:
        if any(name.lower() == "authorization" for name in resolved):
            raise TransportConnectionError("streamable_http", "authentication")
        bearer_value = _header_value(bearer_token, secret_resolver)
        _observe_secret(secret_observer, bearer_value)
        resolved["Authorization"] = f"Bearer {bearer_value}"
    return resolved


def _safe_capabilities(session: ClientSession) -> tuple[str, ...]:
    capabilities = session.server_capabilities
    if capabilities is None:
        return ()
    values = [
        name
        for name, value in vars(capabilities).items()
        if value is not None and not name.startswith("_")
    ]
    return tuple(sorted(values))


def _safe_error_type(error: BaseException) -> str:
    """Return a bounded category, never a provider-controlled class name."""

    builtin_name = type(error).__name__
    if type(error).__module__ == "builtins" and builtin_name in {
        "CancelledError",
        "TimeoutError",
        "ValueError",
        "OSError",
        "RuntimeError",
    }:
        return builtin_name
    return "TransportError"


class _RemoteConnection:
    _transport: TransportName

    def __init__(
        self,
        server: HTTPServer | SSEServer,
        *,
        resolver: SecretResolver | None = None,
        bearer_token: SecretReference | None = None,
        auth: httpx2.Auth | None = None,
        timeout: float = 30.0,
        read_timeout: float = 300.0,
        for_agent: bool = False,
        initialize: bool = True,
        resolve_host: HostResolver = _default_host_resolver,
        http_client_factory: McpHttpClientFactory | None = None,
        session_options: Mapping[str, Any] | None = None,
        trace_bridge: DirectTraceBridge | None = None,
        secret_observer: SecretObserver | None = None,
    ) -> None:
        self.server = server
        self._resolver = resolver or EnvironmentSecretResolver()
        self._bearer_token = bearer_token
        self._auth = auth
        self._timeout = timeout
        self._read_timeout = read_timeout
        self._for_agent = for_agent
        self._initialize_on_enter = initialize
        self._resolve_host = resolve_host
        self._http_client_factory = http_client_factory
        self._session_options = dict(session_options or {})
        self._session_options["read_timeout_seconds"] = self._timeout
        self._trace_bridge = trace_bridge
        self._secret_observer = secret_observer
        self._opened = False
        self._close_complete = False
        self._close_lock = asyncio.Lock()
        self._owner_task: asyncio.Task[Any] | None = None
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._started = time.monotonic()
        self._events: list[TransportEvent] = []
        self._state: Literal[
            "created", "connecting", "initialized", "closed", "failed"
        ] = "created"
        self._protocol_version: str | None = None
        self._server_name: str | None = None
        self._server_version: str | None = None
        self._instructions = False
        self._capabilities: tuple[str, ...] = ()
        self._extensions: tuple[str, ...] = ()
        self._error_type: str | None = None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(transport={self._transport!r}, "
            f"endpoint={_safe_endpoint(self.server.url)!r}, state={self._state!r})"
        )

    @property
    def evidence(self) -> TransportEvidence:
        return TransportEvidence(
            transport=self._transport,
            endpoint=_safe_endpoint(self.server.url),
            state=self._state,
            protocol_version=self._protocol_version,
            server_name=self._server_name,
            server_version=self._server_version,
            instructions=self._instructions,
            capabilities=self._capabilities,
            extensions=self._extensions,
            events=tuple(self._events),
            error_type=self._error_type,
        )

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise TransportConnectionError(
                self._transport, "not_open", evidence=self.evidence
            )
        return self._session

    def _event(self, kind: str, **details: str | int | bool | None) -> None:
        self._events.append(
            TransportEvent(
                kind=kind,
                offset_ms=(time.monotonic() - self._started) * 1000,
                details=dict(details),
            )
        )

    def _resolved_headers(self) -> dict[str, str]:
        headers = self.server.headers
        return resolve_headers(
            headers,
            resolver=self._resolver,
            bearer_token=self._bearer_token,
            secret_observer=self._secret_observer,
        )

    def _http_client(
        self,
        headers: dict[str, str],
        validated_origin: tuple[str, str, int] | None = None,
        resolve_addresses: HostResolver | None = None,
        first_connect_deadline: float | None = None,
    ) -> httpx2.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory(headers)
        if validated_origin is None or resolve_addresses is None:
            validated_origin, resolve_addresses = _validated_transport_policy(
                self.server,
                for_agent=self._for_agent,
                resolve_host=self._resolve_host,
            )
        timeout = httpx2.Timeout(self._timeout, read=self._read_timeout)
        return _safe_mcp_http_client(
            headers=headers,
            timeout=timeout,
            auth=self._auth,
            validated_origin=validated_origin,
            resolve_addresses=resolve_addresses,
            allow_public_auth_origins=self._auth is not None,
            first_connect_deadline=first_connect_deadline,
        )

    def capture_session_metadata(self) -> None:
        """Capture safe initialization metadata after an external initializer."""

        session = self.session
        self._protocol_version = session.protocol_version
        if session.server_info is not None:
            self._server_name = session.server_info.name
            self._server_version = session.server_info.version
        self._instructions = session.instructions is not None
        self._capabilities = _safe_capabilities(session)
        advertised = session.server_capabilities
        extensions = cast(
            Mapping[str, object], getattr(advertised, "extensions", None) or {}
        )
        self._extensions = tuple(sorted(extensions))
        if self._state == "connecting":
            self._state = "initialized"
            self._event("initialized")

    def _clear_secret_material(self) -> None:
        self._resolver = EnvironmentSecretResolver()
        self._bearer_token = None
        self._auth = None
        self._secret_observer = None
        self._session_options.clear()
        try:
            self.server = self.server.model_copy(update={"headers": {}})
        except BaseException:
            # The typed server models are copyable; retain only the safe
            # connection evidence if a hostile test double violates that
            # contract.
            pass

    async def __aenter__(self) -> ClientSession:
        if self._opened or self._close_complete:
            raise TransportConnectionError(
                self._transport, "already_open", evidence=self.evidence
            )
        self._opened = True
        self._owner_task = asyncio.current_task()
        self._stack = AsyncExitStack()
        self._state = "connecting"
        self._event("connect_started")
        try:
            connect_deadline = time.monotonic() + self._timeout
            validated_origin, resolve_addresses = _validated_transport_policy(
                self.server,
                for_agent=self._for_agent,
                resolve_host=self._resolve_host,
            )
            _scheme, hostname, port = validated_origin
            with anyio.fail_after(self._timeout):
                initial_addresses = await anyio.to_thread.run_sync(
                    resolve_addresses,
                    hostname,
                    port,
                    abandon_on_cancel=True,
                )
            initial_available = True
            initial_lock = threading.Lock()

            def connection_resolver(host: str, requested_port: int) -> tuple[str, ...]:
                nonlocal initial_available
                with initial_lock:
                    if (
                        initial_available
                        and canonical_hostname(host) == canonical_hostname(hostname)
                        and requested_port == port
                    ):
                        initial_available = False
                        return initial_addresses
                return resolve_addresses(host, requested_port)

            headers = self._resolved_headers()
            if connect_deadline <= time.monotonic():
                raise TimeoutError
            streams: Any
            if self._transport == "streamable_http":
                client = self._http_client(
                    headers,
                    validated_origin,
                    connection_resolver,
                    connect_deadline,
                )
                await self._stack.enter_async_context(client)
                streams = await self._stack.enter_async_context(
                    streamable_http_client(self.server.url, http_client=client)
                )
            else:
                factory = self._http_client_factory
                if factory is None:
                    factory = lambda headers=None, timeout=None, auth=None: (
                        _safe_mcp_http_client(
                            headers=headers,
                            timeout=timeout,
                            auth=auth,
                            validated_origin=validated_origin,
                            resolve_addresses=connection_resolver,
                            allow_public_auth_origins=auth is not None,
                            first_connect_deadline=connect_deadline,
                        )
                    )
                streams = await self._stack.enter_async_context(
                    sse_client(
                        self.server.url,
                        headers=headers,
                        timeout=self._timeout,
                        sse_read_timeout=self._read_timeout,
                        auth=self._auth,
                        httpx_client_factory=factory,
                    )
                )
            read_stream, write_stream = streams
            if self._trace_bridge is not None:
                read_stream, write_stream = self._trace_bridge.wrap_streams(
                    read_stream, write_stream
                )
            session = ClientSession(read_stream, write_stream, **self._session_options)
            self._session = await self._stack.enter_async_context(session)
            if not self._initialize_on_enter:
                return self._session
            await asyncio.wait_for(self._session.initialize(), timeout=self._timeout)
            self.capture_session_metadata()
            return self._session
        except asyncio.CancelledError:
            self._state = "failed"
            self._error_type = "CancelledError"
            self._event("connect_failed", error_type=self._error_type)
            try:
                await self.aclose()
            except Exception:
                pass
            raise
        except EndpointTrustError:
            self._state = "failed"
            self._error_type = "EndpointTrustError"
            self._event("connect_failed", error_type=self._error_type)
            try:
                await self.aclose()
            except Exception:
                pass
            raise
        except Exception as exc:
            self._state = "failed"
            self._error_type = _safe_error_type(exc)
            self._event("connect_failed", error_type=self._error_type)
            try:
                await self.aclose()
            except Exception:
                pass
            raise TransportConnectionError(
                self._transport, "initialize", evidence=self.evidence
            ) from None

    async def aclose(self) -> None:
        if self._close_complete:
            return
        owner = self._owner_task
        current = asyncio.current_task()
        if owner is not None and owner is not current:
            # Official MCP transports contain AnyIO cancel scopes that must
            # exit in the task that entered them.  Waiting for another task's
            # close would violate that ownership and can cancel both scopes.
            raise TransportConnectionError(self._transport, "close_owner")
        async with self._close_lock:
            if self._close_complete:
                return
            stack, self._stack = self._stack, None
            if stack is not None:
                try:
                    await stack.aclose()
                except asyncio.CancelledError:
                    self._stack = stack
                    raise
                except Exception as exc:
                    self._error_type = _safe_error_type(exc)
                    self._state = "failed"
                    self._event("close_failed", error_type=self._error_type)
                    self._clear_secret_material()
                    self._close_complete = True
                    raise TransportConnectionError(
                        self._transport, "close", evidence=self.evidence
                    ) from None
            self._session = None
            self._clear_secret_material()
            if self._state != "failed":
                self._state = "closed"
                self._event("closed")
            self._close_complete = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()


class StreamableHTTPConnection(_RemoteConnection):
    """Lifecycle adapter for the official Streamable HTTP transport."""

    _transport: TransportName = "streamable_http"

    def __init__(self, server: HTTPServer, **kwargs: Any) -> None:
        super().__init__(server, **kwargs)


class SSEConnection(_RemoteConnection):
    """Lifecycle adapter for the official legacy SSE transport."""

    _transport: TransportName = "sse"

    def __init__(self, server: SSEServer, **kwargs: Any) -> None:
        super().__init__(server, **kwargs)


def remote_connection(
    server: HTTPServer | SSEServer,
    **kwargs: Any,
) -> StreamableHTTPConnection | SSEConnection:
    """Select the official transport adapter from a typed server value."""

    if isinstance(server, HTTPServer):
        return StreamableHTTPConnection(server, **kwargs)
    if isinstance(server, SSEServer):
        return SSEConnection(server, **kwargs)
    raise TypeError("remote_connection requires a HTTPServer or SSEServer")


__all__ = [
    "EndpointTrustError",
    "EnvironmentSecretResolver",
    "HostResolver",
    "RemoteConnection",
    "SSEConnection",
    "SecretResolver",
    "StreamableHTTPConnection",
    "TransportConnectionError",
    "TransportEvent",
    "TransportEvidence",
    "TransportName",
    "remote_connection",
    "resolve_headers",
    "validate_endpoint_trust",
]


RemoteConnection = _RemoteConnection
