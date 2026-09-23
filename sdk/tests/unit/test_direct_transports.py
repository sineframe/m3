"""Contract tests for remote transport lifecycle and policy."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

import httpx2
import pytest
from mcp import ClientSession as OfficialClientSession
from mcp.types import Implementation, SamplingCapability

import m3.transport.direct as direct_module
from m3.transport.direct import (
    EndpointTrustError,
    EnvironmentSecretResolver,
    StreamableHTTPConnection,
    TransportConnectionError,
    resolve_headers,
    validate_endpoint_trust,
)
from m3.types import HTTPServer, SecretReference, TrustLevel


def test_secret_references_resolve_only_into_headers_and_never_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_TEST_TOKEN", "transport-secret")
    headers = resolve_headers(
        {"X-Test": "plain"},
        resolver=EnvironmentSecretResolver(),
        bearer_token=SecretReference(source="environment", name="MCP_TEST_TOKEN"),
    )
    assert headers == {"X-Test": "plain", "Authorization": "Bearer transport-secret"}
    reference = SecretReference(source="environment", name="MCP_TEST_TOKEN")
    assert "transport-secret" not in repr(reference)

    HTTPServer(
        name="remote",
        url="https://example.test/mcp?access_token=transport-secret",
        headers={"Authorization": reference},
    )
    assert "transport-secret" not in repr(reference)


def test_header_secret_observer_classifies_api_keys_but_not_ordinary_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = "resolved-header-reference"
    monkeypatch.setenv("MCP_HEADER_REFERENCE", resolved)
    observed: list[str] = []
    headers = resolve_headers(
        {
            "X-API-Key": "literal-x-api-key",
            "ANTHROPIC_API_KEY": "literal-anthropic-api-key",
            "PATH": "ordinary-path",
            "X-Reference": SecretReference(
                source="environment", name="MCP_HEADER_REFERENCE"
            ),
        },
        secret_observer=observed.append,
    )
    assert headers["PATH"] == "ordinary-path"
    assert observed == [
        "literal-x-api-key",
        "literal-anthropic-api-key",
        resolved,
    ]


def test_private_endpoint_requires_explicit_trust_and_agent_exposure_is_stricter() -> (
    None
):
    untrusted = HTTPServer(name="local", url="http://127.0.0.1:8765/mcp")
    with pytest.raises(EndpointTrustError):
        validate_endpoint_trust(
            untrusted, resolve_host=lambda host, port: ("127.0.0.1",)
        )

    trusted = untrusted.model_copy(update={"trust": TrustLevel.TRUSTED_PRIVATE})
    assert (
        validate_endpoint_trust(trusted, resolve_host=lambda host, port: ("127.0.0.1",))
        == "http://127.0.0.1:8765/mcp"
    )
    loopback = untrusted.model_copy(update={"trust": TrustLevel.SDK_LOOPBACK})
    with pytest.raises(EndpointTrustError):
        validate_endpoint_trust(
            loopback, for_agent=True, resolve_host=lambda host, port: ("127.0.0.1",)
        )

    public_untrusted = HTTPServer(name="public", url="https://example.test/mcp")
    with pytest.raises(EndpointTrustError):
        validate_endpoint_trust(
            public_untrusted,
            for_agent=True,
            resolve_host=lambda host, port: ("93.184.216.34",),
        )
    public = public_untrusted.model_copy(update={"trust": TrustLevel.PUBLIC})
    assert (
        validate_endpoint_trust(
            public,
            for_agent=True,
            resolve_host=lambda host, port: ("93.184.216.34",),
        )
        == "https://example.test/mcp"
    )


def test_loopback_only_rejects_mixed_dns_answers_and_rebinding_without_values() -> None:
    server = HTTPServer(
        name="local",
        url="http://localhost:8765/mcp",
        trust=TrustLevel.TRUSTED_PRIVATE,
        loopback_only=True,
    )
    mixed = ("127.0.0.1", "10.0.0.5")
    message = "loopback-only MCP endpoint resolved to a non-loopback address"
    with pytest.raises(EndpointTrustError) as caught:
        validate_endpoint_trust(server, resolve_host=lambda host, port: mixed)
    assert str(caught.value) == message
    assert all(
        part not in str(caught.value) for part in ("localhost", *mixed, server.url)
    )

    answers = iter((("127.0.0.1",), ("127.0.0.1", "192.168.1.8")))
    _origin, guarded_resolver = direct_module._validated_transport_policy(
        server,
        for_agent=False,
        resolve_host=lambda host, port: next(answers),
    )
    assert guarded_resolver("localhost", 8765) == ("127.0.0.1",)
    with pytest.raises(EndpointTrustError) as rebound:
        guarded_resolver("localhost", 8765)
    assert str(rebound.value) == message


@pytest.mark.parametrize(
    ("trust", "expected"),
    (
        (
            TrustLevel.UNTRUSTED,
            "untrusted MCP endpoint resolved to a private or local address",
        ),
        (
            TrustLevel.PUBLIC,
            "public MCP endpoint resolved to a non-public address",
        ),
    ),
)
def test_direct_trust_failures_are_safe_and_specific(
    trust: TrustLevel, expected: str
) -> None:
    server = HTTPServer(name="remote", url="https://private.example/mcp", trust=trust)
    with pytest.raises(EndpointTrustError) as caught:
        validate_endpoint_trust(server, resolve_host=lambda host, port: ("10.0.0.9",))
    assert str(caught.value) == expected
    assert all(
        value not in str(caught.value)
        for value in (server.url, "private.example", "10.0.0.9")
    )


def test_credential_query_parameters_are_rejected_before_connecting() -> None:
    server = HTTPServer(
        name="remote", url="https://example.test/mcp?access_token=fixture"
    )
    with pytest.raises(EndpointTrustError, match="credential query"):
        validate_endpoint_trust(
            server, resolve_host=lambda host, port: ("93.184.216.34",)
        )


@pytest.mark.parametrize(
    "address",
    ("169.254.169.254", "10.0.0.1", "::1", "fe80::1", "::ffff:169.254.169.254"),
)
def test_untrusted_endpoints_reject_metadata_private_and_ipv6_addresses(
    address: str,
) -> None:
    server = HTTPServer(name="local", url="https://example.test/mcp")
    with pytest.raises(EndpointTrustError):
        validate_endpoint_trust(server, resolve_host=lambda host, port: (address,))


def test_endpoint_evidence_removes_credentials_and_query_tokens() -> None:
    server = HTTPServer(
        name="remote",
        url="https://user:password@example.test/mcp?token=secret&safe=1",
    )
    connection = StreamableHTTPConnection(
        server, resolve_host=lambda host, port: ("93.184.216.34",)
    )
    assert connection.evidence.endpoint == "https://example.test/mcp"
    assert "secret" not in repr(connection)
    assert "password" not in repr(connection.evidence)
    assert "secret" not in repr(connection.evidence)


async def _streamable_fixture(
    scope: MutableMapping[str, Any],
    receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
    send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
) -> None:
    # This minimal ASGI fixture covers initialize and initialized notification;
    # all protocol behavior remains in the official ClientSession.
    body = b""
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
    request = json.loads(body or b"{}")
    if scope["method"] == "DELETE":
        response = {}
        status = 200
        headers = [(b"content-type", b"application/json")]
    elif request.get("method") == "initialize":
        response = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "serverInfo": {"name": "fixture", "version": "1"},
            },
        }
        status = 200
        headers = [
            (b"content-type", b"application/json"),
            (b"mcp-session-id", b"fixture-session"),
        ]
    else:
        response = {}
        status = 202
        headers = []
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send(
        {
            "type": "http.response.body",
            "body": json.dumps(response).encode() if response else b"",
        }
    )


def test_streamable_http_uses_official_client_and_closes_cleanly() -> None:
    async def run() -> None:
        transport = httpx2.ASGITransport(app=_streamable_fixture)

        def factory(headers: Mapping[str, str]) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(transport=transport, headers=dict(headers))

        server = HTTPServer(
            name="fixture",
            url="http://localhost/mcp",
            trust=TrustLevel.TRUSTED_PRIVATE,
        )
        connection = StreamableHTTPConnection(
            server,
            http_client_factory=factory,
            resolve_host=lambda host, port: ("127.0.0.1",),
        )
        async with connection as session:
            assert session.protocol_version == "2025-11-25"
            active_evidence = connection.evidence
            assert active_evidence.server_name == "fixture"
            assert active_evidence.state == "initialized"
        closed_evidence = connection.evidence
        assert closed_evidence.state == "closed"
        assert closed_evidence.events[-1].kind == "closed"

    asyncio.run(run())


def test_remote_transport_forwards_official_client_session_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        transport = httpx2.ASGITransport(app=_streamable_fixture)

        def factory(headers: Mapping[str, str]) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(transport=transport, headers=dict(headers))

        async def callback(*args: object, **kwargs: object) -> object:
            return None

        client_info = Implementation(name="fixture-client", version="1")
        sampling_capabilities = SamplingCapability()
        dispatcher = None
        options: dict[str, Any] = {
            "sampling_callback": callback,
            "list_roots_callback": callback,
            "logging_callback": callback,
            "message_handler": callback,
            "client_info": client_info,
            "log_level": "info",
            "sampling_capabilities": sampling_capabilities,
            "extensions": {"fixture.extension": {}},
            "notification_bindings": (),
            "dispatcher": dispatcher,
        }
        observed: dict[str, Any] = {}

        def spy(*args: Any, **kwargs: Any) -> OfficialClientSession:
            observed.update(kwargs)
            return OfficialClientSession(*args, **kwargs)

        monkeypatch.setattr(direct_module, "ClientSession", spy)
        server = HTTPServer(
            name="fixture",
            url="http://localhost/mcp",
            trust=TrustLevel.TRUSTED_PRIVATE,
        )
        connection = StreamableHTTPConnection(
            server,
            http_client_factory=factory,
            resolve_host=lambda host, port: ("127.0.0.1",),
            timeout=0.25,
            session_options=options,
        )
        async with connection:
            assert connection.session.protocol_version == "2025-11-25"
        for key, value in options.items():
            assert observed[key] is value
        assert observed["read_timeout_seconds"] == 0.25

    asyncio.run(run())


def test_missing_authentication_is_safe_and_does_not_retain_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_MISSING_TOKEN", raising=False)
    with pytest.raises(TransportConnectionError) as caught:
        resolve_headers(
            {},
            resolver=EnvironmentSecretResolver(),
            bearer_token=SecretReference(
                source="environment", name="MCP_MISSING_TOKEN"
            ),
        )
    assert "MCP_MISSING_TOKEN" not in str(caught.value)


def test_hostile_secret_resolver_and_observer_fail_closed_without_values() -> None:
    class HostileResolver:
        def resolve(self, reference: SecretReference) -> str:
            raise RuntimeError("provider secret must not escape")

    observed: list[str] = []
    with pytest.raises(TransportConnectionError) as caught:
        resolve_headers(
            {"Authorization": SecretReference(source="provider", name="credential")},
            resolver=HostileResolver(),
            secret_observer=observed.append,
        )
    assert observed == []
    assert "provider secret" not in str(caught.value)

    with pytest.raises(TransportConnectionError) as caught:
        resolve_headers(
            {"Authorization": "resolved-secret"},
            secret_observer=lambda _value: (_ for _ in ()).throw(
                RuntimeError("observer secret")
            ),
        )
    assert "observer secret" not in str(caught.value)


def test_default_http_client_does_not_follow_untrusted_redirects() -> None:
    server = HTTPServer(name="remote", url="https://example.test/mcp")
    connection = StreamableHTTPConnection(
        server,
        resolve_host=lambda host, port: ("93.184.216.34",),
    )
    client = connection._http_client({})
    assert client.follow_redirects is False
    asyncio.run(client.aclose())


def test_remote_connection_rejects_double_enter_and_does_not_retain_closed_session() -> (
    None
):
    async def run() -> None:
        transport = httpx2.ASGITransport(app=_streamable_fixture)

        def factory(headers: Mapping[str, str]) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(transport=transport, headers=dict(headers))

        server = HTTPServer(
            name="fixture",
            url="http://localhost/mcp",
            trust=TrustLevel.TRUSTED_PRIVATE,
        )
        connection = StreamableHTTPConnection(
            server,
            http_client_factory=factory,
            resolve_host=lambda host, port: ("127.0.0.1",),
        )
        async with connection:
            with pytest.raises(TransportConnectionError):
                await connection.__aenter__()
            second_close = asyncio.create_task(connection.aclose())
            await asyncio.sleep(0)
            await connection.aclose()
            with pytest.raises(TransportConnectionError):
                await second_close
            assert connection.evidence.state == "closed"
            assert [event.kind for event in connection.evidence.events].count(
                "closed"
            ) == 1
        with pytest.raises(TransportConnectionError):
            _ = connection.session

    asyncio.run(run())


def test_connection_failure_is_not_masked_by_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        server = HTTPServer(name="local", url="http://127.0.0.1/mcp")
        connection = StreamableHTTPConnection(
            server,
            resolve_host=lambda host, port: ("127.0.0.1",),
        )

        async def broken_close() -> None:
            raise TransportConnectionError("streamable_http", "close")

        monkeypatch.setattr(connection, "aclose", broken_close)
        with pytest.raises(EndpointTrustError):
            await connection.__aenter__()

    asyncio.run(run())


def test_hostile_resolvers_fail_closed_without_propagating_their_messages() -> None:
    class HostileResolver:
        def resolve(self, reference: SecretReference) -> str:
            raise RuntimeError("resolver-secret")

    with pytest.raises(TransportConnectionError) as caught:
        resolve_headers(
            {"Authorization": SecretReference(source="provider", name="token")},
            resolver=HostileResolver(),
        )
    assert "resolver-secret" not in str(caught.value)

    server = HTTPServer(name="remote", url="https://example.test/mcp")
    with pytest.raises(EndpointTrustError) as caught_endpoint:
        validate_endpoint_trust(
            server,
            resolve_host=lambda host, port: (_ for _ in ()).throw(
                RuntimeError("dns-secret")
            ),
        )
    assert "dns-secret" not in str(caught_endpoint.value)
