"""Regression coverage for DNS-rebinding-safe HTTP transports."""

from __future__ import annotations

import asyncio
import importlib
import socket
import time
from pathlib import Path
from typing import Any

import anyio
import httpcore
import httpx
import httpx2
import pytest

import m3.transport.http_proxy as http_proxy_module
from m3.transport._http_pinning import (
    MAX_ADDRESS_CANDIDATES,
    MAX_PARALLEL_CONNECTS,
    ValidatingHTTPX2Transport,
    ValidatingHTTPXTransport,
    _ValidatingNetworkBackend,
)
from m3.transport.direct import EndpointTrustError, _validated_transport_policy
from m3.transport.http_proxy import McpHttpProxy, UnsafeUpstreamError
from m3.types import HTTPServer, TrustLevel

CLIENTS = (
    (httpx, ValidatingHTTPXTransport),
    (httpx2, ValidatingHTTPX2Transport),
)


def _transport(
    client_module: Any, transport_type: Any, handler: Any, **kwargs: Any
) -> Any:
    transport = transport_type(
        scheme="https",
        hostname="xn--bcher-kva.de",
        port=443,
        resolve_addresses=lambda host, port: ("93.184.216.34",),
        **kwargs,
    )
    transport._transport = client_module.MockTransport(handler)
    return transport


@pytest.mark.asyncio
@pytest.mark.parametrize(("client_module", "transport_type"), CLIENTS)
async def test_logical_url_idna_and_response_request_are_never_mutated(
    client_module: Any, transport_type: Any
) -> None:
    seen: list[Any] = []

    def handler(request: Any) -> Any:
        seen.append(request)
        return client_module.Response(200, text="ok")

    async with client_module.AsyncClient(
        transport=_transport(client_module, transport_type, handler), trust_env=False
    ) as client:
        response = await client.get("https://xn--bcher-kva.de/mcp")

    assert response.text == "ok"
    assert seen[0].url.host == "bücher.de"
    assert response.request.url.host == "bücher.de"


@pytest.mark.asyncio
@pytest.mark.parametrize(("client_module", "transport_type"), CLIENTS)
async def test_host_only_cookie_survives_across_requests(
    client_module: Any, transport_type: Any
) -> None:
    seen_cookies: list[str | None] = []

    def handler(request: Any) -> Any:
        seen_cookies.append(request.headers.get("cookie"))
        headers = {"set-cookie": "sid=abc; Path=/"} if len(seen_cookies) == 1 else {}
        return client_module.Response(200, headers=headers)

    async with client_module.AsyncClient(
        transport=_transport(client_module, transport_type, handler), trust_env=False
    ) as client:
        await client.get("https://xn--bcher-kva.de/first")
        await client.get("https://xn--bcher-kva.de/second")

    assert seen_cookies == [None, "sid=abc"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("client_module", "transport_type"), CLIENTS)
async def test_digest_auth_retries_the_unchanged_origin(
    client_module: Any, transport_type: Any
) -> None:
    seen: list[Any] = []

    def handler(request: Any) -> Any:
        seen.append(request)
        if "authorization" not in request.headers:
            return client_module.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Digest realm="mcp", nonce="abc", algorithm=MD5, qop="auth"'
                    )
                },
            )
        return client_module.Response(200)

    async with client_module.AsyncClient(
        transport=_transport(client_module, transport_type, handler),
        auth=client_module.DigestAuth("user", "password"),
        trust_env=False,
    ) as client:
        response = await client.get("https://xn--bcher-kva.de/mcp")

    assert response.status_code == 200
    assert len(seen) == 2
    assert all(request.url.host == "bücher.de" for request in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize(("client_module", "transport_type"), CLIENTS)
async def test_auth_flow_may_use_a_separate_public_origin(
    client_module: Any, transport_type: Any
) -> None:
    seen: list[str] = []

    class CrossOriginAuth(client_module.Auth):
        def auth_flow(self, request: Any) -> Any:
            yield request
            yield client_module.Request("POST", "https://auth.example/token")

    def handler(request: Any) -> Any:
        seen.append(request.url.host)
        return client_module.Response(401 if len(seen) == 1 else 200)

    async with client_module.AsyncClient(
        transport=_transport(
            client_module,
            transport_type,
            handler,
            allow_public_auth_origins=True,
        ),
        auth=CrossOriginAuth(),
        trust_env=False,
    ) as client:
        response = await client.get("https://xn--bcher-kva.de/mcp")

    assert response.status_code == 200
    assert seen == ["bücher.de", "auth.example"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("client_module", "transport_type"), CLIENTS)
async def test_primary_origin_scheme_cannot_change(
    client_module: Any, transport_type: Any
) -> None:
    transport = _transport(
        client_module,
        transport_type,
        lambda request: client_module.Response(200),
    )
    async with client_module.AsyncClient(
        transport=transport, trust_env=False
    ) as client:
        with pytest.raises(RuntimeError, match="escaped"):
            await client.get("http://xn--bcher-kva.de:443/token")


@pytest.mark.asyncio
@pytest.mark.parametrize(("client_module", "transport_type"), CLIENTS)
async def test_cross_origin_auth_endpoint_must_use_https(
    client_module: Any, transport_type: Any
) -> None:
    transport = _transport(
        client_module,
        transport_type,
        lambda request: client_module.Response(200),
        allow_public_auth_origins=True,
    )
    async with client_module.AsyncClient(
        transport=transport, trust_env=False
    ) as client:
        with pytest.raises(RuntimeError, match="escaped"):
            await client.post("http://auth.example/token")


@pytest.mark.asyncio
async def test_network_backend_connects_to_validated_addresses_not_hostname() -> None:
    connected: list[str] = []

    class Delegate:
        async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> object:
            connected.append(host)
            return object()

    backend = _ValidatingNetworkBackend(
        Delegate(),
        resolve_addresses=lambda host, port: ("93.184.216.34",),
        connect_error=httpcore.ConnectError,
        connect_timeout=httpcore.ConnectTimeout,
        retryable_errors=(httpcore.ConnectError, httpcore.ConnectTimeout),
    )
    await backend.connect_tcp("rebind.example", 443)
    assert connected == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_connect_timeout_includes_dns_and_policy_resolution() -> None:
    attempted = False

    def slow_resolver(host: str, port: int) -> tuple[str, ...]:
        time.sleep(0.08)
        return ("93.184.216.34",)

    class Delegate:
        async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> object:
            nonlocal attempted
            attempted = True
            return object()

    backend = _ValidatingNetworkBackend(
        Delegate(),
        resolve_addresses=slow_resolver,
        connect_error=httpcore.ConnectError,
        connect_timeout=httpcore.ConnectTimeout,
        retryable_errors=(httpcore.ConnectError, httpcore.ConnectTimeout),
    )
    started = anyio.current_time()
    with pytest.raises(httpcore.ConnectTimeout):
        await backend.connect_tcp("slow.example", 443, timeout=0.01)
    assert anyio.current_time() - started < 0.06
    assert attempted is False


@pytest.mark.asyncio
async def test_preflight_deadline_only_reduces_the_first_connect_timeout() -> None:
    timeouts: list[float | None] = []

    class Delegate:
        async def connect_tcp(
            self, host: str, port: int, *, timeout: float | None, **kwargs: Any
        ) -> object:
            timeouts.append(timeout)
            return object()

    backend = _ValidatingNetworkBackend(
        Delegate(),
        resolve_addresses=lambda host, port: ("93.184.216.34",),
        connect_error=httpcore.ConnectError,
        connect_timeout=httpcore.ConnectTimeout,
        retryable_errors=(httpcore.ConnectError, httpcore.ConnectTimeout),
        first_connect_deadline=time.monotonic() + 1.0,
    )
    await backend.connect_tcp("resource.example", 443, timeout=2.0)
    await backend.connect_tcp("resource.example", 443, timeout=2.0)

    assert timeouts[0] is not None and 0 < timeouts[0] < 1.0
    assert timeouts[1] is not None and 1.9 < timeouts[1] <= 2.0


@pytest.mark.asyncio
async def test_validated_addresses_race_so_blackhole_cannot_starve_fallback() -> None:
    attempts: list[tuple[str, float | None]] = []

    class Stream:
        async def aclose(self) -> None:
            pass

    class Delegate:
        async def connect_tcp(
            self, host: str, port: int, *, timeout: float | None, **kwargs: Any
        ) -> Stream:
            attempts.append((host, timeout))
            if host == "2001:db8::1":
                await anyio.sleep(0.08)
                raise httpcore.ConnectTimeout()
            return Stream()

    backend = _ValidatingNetworkBackend(
        Delegate(),
        resolve_addresses=lambda host, port: (
            "2001:db8::1",
            "93.184.216.34",
        ),
        connect_error=httpcore.ConnectError,
        connect_timeout=httpcore.ConnectTimeout,
        retryable_errors=(httpcore.ConnectError, httpcore.ConnectTimeout),
    )
    stream = await backend.connect_tcp("dual-stack.example", 443, timeout=0.03)
    assert isinstance(stream, Stream)
    assert [address for address, _timeout in attempts] == [
        "2001:db8::1",
        "93.184.216.34",
    ]
    assert all(timeout is not None and timeout > 0 for _address, timeout in attempts)


@pytest.mark.asyncio
async def test_attacker_dns_answer_has_bounded_attempts_and_concurrency() -> None:
    calls = 0
    active = 0
    peak_active = 0

    class Delegate:
        async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> object:
            nonlocal active, calls, peak_active
            calls += 1
            active += 1
            peak_active = max(peak_active, active)
            try:
                await anyio.sleep(0.005)
                raise httpcore.ConnectError()
            finally:
                active -= 1

    backend = _ValidatingNetworkBackend(
        Delegate(),
        resolve_addresses=lambda host, port: tuple(
            f"192.0.2.{index % 254 + 1}" for index in range(512)
        ),
        connect_error=httpcore.ConnectError,
        connect_timeout=httpcore.ConnectTimeout,
        retryable_errors=(httpcore.ConnectError, httpcore.ConnectTimeout),
    )
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("many.example", 443, timeout=1.0)
    assert calls == MAX_ADDRESS_CANDIDATES
    assert peak_active <= MAX_PARALLEL_CONNECTS


@pytest.mark.asyncio
async def test_non_numeric_resolver_result_never_reaches_socket_backend() -> None:
    calls = 0

    class Delegate:
        async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> object:
            nonlocal calls
            calls += 1
            return object()

    backend = _ValidatingNetworkBackend(
        Delegate(),
        resolve_addresses=lambda host, port: ("second-lookup.example",),
        connect_error=httpcore.ConnectError,
        connect_timeout=httpcore.ConnectTimeout,
        retryable_errors=(httpcore.ConnectError, httpcore.ConnectTimeout),
    )
    with pytest.raises(httpcore.ConnectError, match="non-numeric"):
        await backend.connect_tcp("resource.example", 443, timeout=1.0)
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("client_module", "transport_type"), CLIENTS)
async def test_real_connection_uses_validated_address_without_resolving_logical_host(
    client_module: Any, transport_type: Any
) -> None:
    observed: list[bytes] = []

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        observed.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    transport = transport_type(
        scheme="http",
        hostname="does-not-resolve.invalid",
        port=port,
        resolve_addresses=lambda host, requested_port: ("127.0.0.1",),
    )
    try:
        async with client_module.AsyncClient(
            transport=transport, trust_env=False
        ) as client:
            response = await client.get(f"http://does-not-resolve.invalid:{port}/mcp")
        assert response.text == "ok"
        assert response.request.url.host == "does-not-resolve.invalid"
        assert b"Host: does-not-resolve.invalid:" in observed[0]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_capture_proxy_rejects_private_address_during_bounded_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_getaddrinfo = socket.getaddrinfo
    answers = 0

    def rebinding_getaddrinfo(
        host: str, port: int, *args: Any, **kwargs: Any
    ) -> list[tuple[Any, ...]]:
        nonlocal answers
        if host != "rebind.example":
            return original_getaddrinfo(host, port, *args, **kwargs)
        answers += 1
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", port),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)
    proxy = McpHttpProxy(
        upstream_url="http://rebind.example/mcp",
        configured_headers=None,
        transport="streamable_http",
        capture_path=str(tmp_path / "capture.jsonl"),
        baseline_ns=0,
    )
    with pytest.raises(UnsafeUpstreamError, match="non-public"):
        await proxy.start()
    assert answers == 1


@pytest.mark.asyncio
async def test_capture_proxy_reuses_preflight_answer_then_revalidates_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_getaddrinfo = socket.getaddrinfo
    answers = iter(("93.184.216.34", "127.0.0.1"))
    lookups = 0
    captured_resolver: list[Any] = []

    def rebinding_getaddrinfo(
        host: str, port: int, *args: Any, **kwargs: Any
    ) -> list[tuple[Any, ...]]:
        nonlocal lookups
        if host != "rebind.example":
            return original_getaddrinfo(host, port, *args, **kwargs)
        lookups += 1
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (next(answers), port),
            )
        ]

    def transport_factory(**kwargs: Any) -> httpx.AsyncBaseTransport:
        captured_resolver.append(kwargs["resolve_addresses"])
        return httpx.MockTransport(lambda request: httpx.Response(200))

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)
    monkeypatch.setattr(
        http_proxy_module, "ValidatingHTTPXTransport", transport_factory
    )
    proxy = McpHttpProxy(
        upstream_url="http://rebind.example/mcp",
        configured_headers=None,
        transport="streamable_http",
        capture_path=str(tmp_path / "capture.jsonl"),
        baseline_ns=0,
    )
    await proxy.start()
    try:
        resolver = captured_resolver[0]
        assert resolver("rebind.example", 80) == ("93.184.216.34",)
        assert lookups == 1
        with pytest.raises(UnsafeUpstreamError, match="non-public"):
            resolver("rebind.example", 80)
        assert lookups == 2
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_capture_proxy_preflight_dns_has_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow_resolver(hostname: str, port: int) -> tuple[str, ...]:
        time.sleep(0.08)
        return ("93.184.216.34",)

    monkeypatch.setattr(http_proxy_module, "_UPSTREAM_CONNECT_TIMEOUT", 0.01)
    monkeypatch.setattr(http_proxy_module, "_resolve_public_host", slow_resolver)
    proxy = McpHttpProxy(
        upstream_url="https://slow.example/mcp",
        configured_headers=None,
        transport="streamable_http",
        capture_path=str(tmp_path / "capture.jsonl"),
        baseline_ns=0,
    )

    started = anyio.current_time()
    with pytest.raises(UnsafeUpstreamError, match="timed out"):
        await proxy.start()
    assert anyio.current_time() - started < 0.06


def test_direct_connection_policy_blocks_private_primary_and_oauth_origins() -> None:
    answers = {
        "resource.example": iter((("127.0.0.1",),)),
        "auth.example": iter((("10.0.0.1",),)),
    }

    def resolver(host: str, port: int) -> tuple[str, ...]:
        return next(answers[host])

    _origin, guarded_resolver = _validated_transport_policy(
        HTTPServer(
            name="resource",
            url="https://resource.example/mcp",
            trust=TrustLevel.PUBLIC,
        ),
        for_agent=False,
        resolve_host=resolver,
    )
    with pytest.raises(EndpointTrustError):
        guarded_resolver("resource.example", 443)
    with pytest.raises(EndpointTrustError):
        guarded_resolver("auth.example", 443)


def test_direct_policy_validates_answers_beyond_connection_candidate_cap() -> None:
    public = tuple(f"8.8.8.{index}" for index in range(1, 9))

    _origin, guarded_resolver = _validated_transport_policy(
        HTTPServer(
            name="resource",
            url="https://resource.example/mcp",
            trust=TrustLevel.PUBLIC,
        ),
        for_agent=False,
        resolve_host=lambda host, port: (*public, "127.0.0.1"),
    )

    with pytest.raises(EndpointTrustError):
        guarded_resolver("resource.example", 443)


def test_direct_public_policy_rejects_non_global_shared_address_space() -> None:
    _origin, guarded_resolver = _validated_transport_policy(
        HTTPServer(
            name="resource",
            url="https://resource.example/mcp",
            trust=TrustLevel.PUBLIC,
        ),
        for_agent=False,
        resolve_host=lambda host, port: ("100.64.0.1",),
    )

    with pytest.raises(EndpointTrustError):
        guarded_resolver("resource.example", 443)


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_url", ("ftp://example.com/mcp", "http://x:bad"))
async def test_capture_proxy_rejects_invalid_upstream_before_serving(
    tmp_path: Path, upstream_url: str
) -> None:
    proxy = McpHttpProxy(
        upstream_url=upstream_url,
        configured_headers=None,
        transport="streamable_http",
        capture_path=str(tmp_path / "capture.jsonl"),
        baseline_ns=0,
    )
    with pytest.raises(UnsafeUpstreamError, match=r"HTTP\(S\)"):
        await proxy.start()


@pytest.mark.parametrize(
    ("transport_module", "transport_type"),
    (
        ("httpx._transports.default", ValidatingHTTPXTransport),
        ("httpx2._transports.default", ValidatingHTTPX2Transport),
    ),
)
def test_inner_transport_retains_environment_ca_loading(
    monkeypatch: pytest.MonkeyPatch, transport_module: str, transport_type: Any
) -> None:
    module = importlib.import_module(transport_module)
    original = module.create_ssl_context
    observed: list[bool] = []

    def create_ssl_context(*args: Any, **kwargs: Any) -> Any:
        observed.append(kwargs["trust_env"])
        kwargs["trust_env"] = False
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "create_ssl_context", create_ssl_context)
    transport_type(
        scheme="https",
        hostname="example.com",
        port=443,
        resolve_addresses=lambda host, port: ("93.184.216.34",),
    )
    assert observed == [True]
