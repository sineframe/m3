"""Pure normalization for servers selected by the M3 pytest integration."""

from __future__ import annotations

import ipaddress as _ipaddress
from collections.abc import Mapping as _Mapping
from typing import Any as _Any
from urllib.parse import urlsplit as _urlsplit

from ._types.base import TrustLevel as _TrustLevel
from ._types.specs import HTTPServer as _HTTPServer
from ._types.specs import StdioServer as _StdioServer


def _is_loopback_url(url: str) -> bool:
    try:
        hostname = _urlsplit(url).hostname
        if hostname is None:
            return False
        if hostname.lower() == "localhost":
            return True
        return _ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def normalize_server(value: _Any) -> _HTTPServer | _StdioServer:
    """Turn a marker/CLI server declaration into its immutable SDK value."""
    if isinstance(value, (_HTTPServer, _StdioServer)):
        if value.trust is _TrustLevel.SDK_LOOPBACK:
            raise ValueError("sdk_loopback trust is reserved for SDK-created servers")
        return value
    if not isinstance(value, _Mapping):
        raise ValueError("each m3 server must be a mapping, HTTPServer, or StdioServer")
    kind = value.get("type")
    name = value.get("name", "server")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("server name must be a non-empty string")
    if kind == "http":
        unknown = set(value) - {"type", "url", "name", "trust"}
        if unknown:
            raise ValueError(f"unknown HTTP server field {sorted(unknown)[0]!r}")
        url = value.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("HTTP server requires a non-empty url")
        parsed = _urlsplit(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("HTTP server url must have a valid endpoint") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port == 0
        ):
            raise ValueError("HTTP server url must be an http:// or https:// endpoint")
        supplied_trust = "trust" in value
        trust = value.get("trust")
        if not supplied_trust:
            trust = (
                _TrustLevel.TRUSTED_PRIVATE
                if _is_loopback_url(url)
                else _TrustLevel.UNTRUSTED
            )
        try:
            trust = _TrustLevel(trust)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "HTTP server trust must be untrusted, public, or trusted_private"
            ) from exc
        if trust is _TrustLevel.SDK_LOOPBACK:
            raise ValueError("sdk_loopback trust is reserved for SDK-created servers")
        return _HTTPServer(
            name=name.strip(),
            url=url,
            trust=trust,
            loopback_only=(not supplied_trust and _is_loopback_url(url)),
        )
    if kind == "stdio":
        unknown = set(value) - {"type", "command", "args", "name"}
        if unknown:
            raise ValueError(f"unknown stdio server field {sorted(unknown)[0]!r}")
        command = value.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("stdio server requires a non-empty command")
        args = value.get("args", ())
        if not isinstance(args, (list, tuple)) or any(
            not isinstance(arg, str) for arg in args
        ):
            raise ValueError("stdio server args must be a list of strings")
        return _StdioServer(name=name.strip(), command=command, args=tuple(args))
    raise ValueError("server type must be 'http' or 'stdio'")


def normalize_servers(values: _Any) -> tuple[_HTTPServer | _StdioServer, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("m3 servers must be a non-empty list")
    servers = tuple(normalize_server(value) for value in values)
    if any(server in servers[:index] for index, server in enumerate(servers)):
        raise ValueError("m3 servers must not contain duplicate entries")
    return servers
