from __future__ import annotations

import httpx2
import pytest

from m3.transport.http_evidence import (
    http_exchange_payload,
    jsonrpc_request_ids,
    safe_challenge,
)


@pytest.mark.parametrize(
    ("challenge", "expected"),
    [
        (
            'Bearer realm="m3", error="invalid_token", nonce="secret"',
            'Bearer realm="m3", error="invalid_token"',
        ),
        (
            "Bearer error=invalid_token, scope=read",
            'Bearer error="invalid_token", scope="read"',
        ),
        ("Negotiate c2VjcmV0LXRpY2tldA==", "Negotiate"),
        ("Negotiate c2VjcmV0", "Negotiate"),
        (
            'Basic realm="a", Bearer realm="b", resource_metadata="https://x/.well-known"',
            'Basic realm="a", Bearer realm="b", resource_metadata="https://x/.well-known"',
        ),
        ('Bearer realm="say \\"hi\\""', 'Bearer realm="say \\"hi\\""'),
        ('Bearer realm="m3", ;garbage token="leak"', 'Bearer realm="m3"'),
        ("Bearer", "Bearer"),
        ("Negotiate, NTLM", "Negotiate, NTLM"),
        ('Basic, Bearer, Digest realm="x"', 'Basic, Bearer, Digest realm="x"'),
        ("Basic , Bearer", "Basic, Bearer"),
        ("Negotiate c2VjcmV0, NTLM", "Negotiate, NTLM"),
        ("", None),
        (
            'Bearer realm="api", eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.c2ln',
            'Bearer realm="api"',
        ),
        ('Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.c2ln realm="api"', "Bearer"),
        ('Basic realm="x", dXNlcjpwYXNz', 'Basic realm="x"'),
        ("Bearer abc!def", "Bearer"),
        ('Bearer realm="x" Basic', "Bearer"),
        ('Bearer realm="x", X-Vendor-Token realm="y"', 'Bearer realm="x"'),
        ("bearer REALM=x", 'Bearer realm="x"'),
        ('realm="orphan"', None),
    ],
)
def test_safe_challenge_keeps_only_schemes_and_explanatory_parameters(
    challenge: str, expected: str | None
) -> None:
    assert safe_challenge(challenge) == expected


def test_safe_challenge_bounds_parameter_values() -> None:
    rendered = safe_challenge(f'Bearer error_description="{"x" * 5000}"')
    assert rendered is not None
    assert len(rendered) < 600


def test_http_exchange_payload_drops_unlisted_headers() -> None:
    headers = httpx2.Headers(
        [
            ("content-type", "application/json"),
            ("set-cookie", "session=secret"),
            ("www-authenticate", 'Bearer realm="a"'),
            ("www-authenticate", "Negotiate c2VjcmV0"),
        ]
    )
    assert http_exchange_payload("POST", 401, headers) == {
        "method": "POST",
        "status_code": 401,
        "headers": [
            {"name": "content-type", "value": "application/json"},
            {"name": "www-authenticate", "value": 'Bearer realm="a"'},
            {"name": "www-authenticate", "value": "Negotiate"},
        ],
    }


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b'{"jsonrpc":"2.0","id":1,"method":"initialize"}', (1,)),
        (b'{"jsonrpc":"2.0","method":"notifications/initialized"}', ()),
        (b'{"jsonrpc":"2.0","id":"a","result":{}}', ()),
        (
            b'[{"jsonrpc":"2.0","id":"a","method":"x"},'
            b'{"jsonrpc":"2.0","id":true,"method":"y"},'
            b'{"jsonrpc":"2.0","id":2,"method":"z"}]',
            ("a", 2),
        ),
        (b"", ()),
        (b"not json", ()),
        (b"\xff", ()),
    ],
)
def test_jsonrpc_request_ids(content: bytes, expected: tuple[object, ...]) -> None:
    assert jsonrpc_request_ids(content) == expected
