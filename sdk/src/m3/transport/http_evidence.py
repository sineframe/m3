"""Credential-free HTTP exchange evidence for MCP requests.

The official MCP client folds a non-2xx response into a generic JSON-RPC
error, so the HTTP status and any authentication challenge must be observed
on the HTTP client itself.  Only the status, the request method and an
allowlisted subset of response headers are kept; ``WWW-Authenticate`` keeps
its schemes and the RFC 6750 / RFC 9728 parameters that explain a refusal.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Final

_SAFE_HEADERS: Final[tuple[str, ...]] = (
    "content-type",
    "content-length",
    "retry-after",
    "request-id",
    "x-request-id",
)
_CHALLENGE_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "realm",
        "error",
        "error_description",
        "error_uri",
        "scope",
        "resource_metadata",
    }
)
_MAX_HEADER_VALUE: Final[int] = 2048
_MAX_PARAM_VALUE: Final[int] = 512
_TOKEN: Final[str] = r"[A-Za-z0-9!#$%&'*+.^_`|~-]+"
_PARAM: Final[re.Pattern[str]] = re.compile(
    rf'\s*({_TOKEN})\s*=\s*("(?:[^"\\]|\\.)*"|{_TOKEN})\s*(?:,|$)'
)
_SCHEME: Final[re.Pattern[str]] = re.compile(rf"\s*({_TOKEN})(?=\s|,|$)")
_TOKEN68: Final[re.Pattern[str]] = re.compile(r"\s*[A-Za-z0-9\-._~+/]+=*\s*(?:,|$)")


def _quoted(value: str) -> str:
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = re.sub(r"\\(.)", r"\1", value[1:-1])
    value = value[:_MAX_PARAM_VALUE]
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def safe_challenge(value: str) -> str | None:
    """Return the challenge with only its schemes and explanatory parameters.

    Token68 credentials and unknown parameters are dropped.  Parsing stops at
    the first malformed element so nothing after it can leak through.
    """

    challenges: list[tuple[str, list[str]]] = []
    position = 0
    while position < len(value):
        if value[position] in " \t,":
            position += 1
            continue
        param = _PARAM.match(value, position)
        if param is not None and challenges:
            name = param.group(1).lower()
            if name in _CHALLENGE_PARAMS:
                challenges[-1][1].append(f"{name}={_quoted(param.group(2))}")
            position = param.end()
            continue
        scheme = _SCHEME.match(value, position)
        if scheme is not None:
            challenges.append((scheme.group(1), []))
            position = scheme.end()
            # Only whitespace separates a scheme from its own token68 or
            # parameters; a comma starts the next challenge or parameter.
            separated = value[position : position + 1] in (" ", "\t")
            rest = value[position:].lstrip(" \t")
            if separated and rest and not rest.startswith(","):
                token68 = _TOKEN68.match(value, position)
                if token68 is not None and _PARAM.match(value, position) is None:
                    position = token68.end()
            continue
        break
    if not challenges:
        return None
    rendered = ", ".join(
        f"{scheme} {', '.join(params)}" if params else scheme
        for scheme, params in challenges
    )
    return rendered[:_MAX_HEADER_VALUE]


def http_exchange_payload(
    method: str, status_code: int, headers: Any
) -> dict[str, Any]:
    """Build the JSON trace payload for one observed HTTP response."""

    safe: list[dict[str, str]] = []
    for name in _SAFE_HEADERS:
        value = headers.get(name)
        if isinstance(value, str) and value:
            safe.append({"name": name, "value": value[:_MAX_HEADER_VALUE]})
    get_list = getattr(headers, "get_list", None)
    challenges: Iterable[Any] = (
        get_list("www-authenticate")
        if callable(get_list)
        else (headers.get("www-authenticate"),)
    )
    for challenge in challenges:
        if isinstance(challenge, str):
            rendered = safe_challenge(challenge)
            if rendered is not None:
                safe.append({"name": "www-authenticate", "value": rendered})
    return {"method": method, "status_code": status_code, "headers": safe}


def jsonrpc_request_ids(content: bytes) -> tuple[int | str, ...]:
    """Return the ids of the JSON-RPC requests carried by an HTTP body."""

    if not content:
        return ()
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, ValueError):
        return ()
    items = payload if isinstance(payload, list) else [payload]
    ids: list[int | str] = []
    for item in items:
        if not isinstance(item, Mapping) or "method" not in item:
            continue
        request_id = item.get("id")
        if isinstance(request_id, (int, str)) and not isinstance(request_id, bool):
            ids.append(request_id)
    return tuple(ids)


__all__ = ["http_exchange_payload", "jsonrpc_request_ids", "safe_challenge"]
