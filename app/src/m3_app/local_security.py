"""Security boundary for M3's unauthenticated local HTTP applications."""

from __future__ import annotations

import hmac
import ipaddress
from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_parts(value: str) -> tuple[str, str, int] | None:
    """Parse and normalize a browser HTTP origin."""

    try:
        parsed = urlsplit(value)
        if parsed.scheme not in _DEFAULT_PORTS or not parsed.netloc:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        if parsed.path or parsed.query or parsed.fragment:
            return None
        hostname = parsed.hostname
        if not hostname:
            return None
        port = parsed.port or _DEFAULT_PORTS[parsed.scheme]
    except ValueError:
        return None
    return parsed.scheme, hostname.rstrip(".").lower(), port


def _request_origin(scope: Scope) -> tuple[str, str, int] | None:
    try:
        scheme = str(scope.get("scheme", "")).lower()
        scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
        host = Headers(scope=scope).get("host", "")
        parsed = urlsplit(f"{scheme}://{host}")
        if scheme not in _DEFAULT_PORTS or not parsed.hostname:
            return None
        return (
            scheme,
            parsed.hostname.rstrip(".").lower(),
            parsed.port or _DEFAULT_PORTS[scheme],
        )
    except ValueError:
        return None


def _host_name(value: str) -> str | None:
    """Extract a hostname from a Host header, including bracketed IPv6."""

    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            return None
        hostname = value[1:closing]
        suffix = value[closing + 1 :]
        if suffix and (not suffix.startswith(":") or not suffix[1:].isdigit()):
            return None
    elif value.count(":") == 0:
        hostname = value
    elif value.count(":") == 1:
        hostname, port = value.rsplit(":", 1)
        if not hostname or not port.isdigit():
            return None
    else:
        return None
    return hostname.rstrip(".").lower() if hostname else None


def _is_loopback_client(scope: Scope) -> bool:
    client = scope.get("client")
    if not client:
        return False
    try:
        address = ipaddress.ip_address(client[0].split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped is not None and mapped.is_loopback)


def _path_relative_to_root(scope: Scope) -> str:
    """Return the application path after removing its ASGI root path."""

    path = str(scope.get("path", ""))
    root_path = str(scope.get("root_path", "")).rstrip("/")
    if root_path and (path == root_path or path.startswith(f"{root_path}/")):
        return path[len(root_path) :] or "/"
    return path


class LocalSecurityMiddleware:
    """Enforce the network and browser boundary for local API applications."""

    def __init__(self, app: ASGIApp, auth_token: str | None = None) -> None:
        self.app = app
        self.auth_token = auth_token

    def _authorized(self, scope: Scope) -> bool:
        """Check one raw Authorization header without accepting duplicates."""

        authorization_values = [
            value.decode("latin-1")
            for name, value in scope.get("headers", ())
            if name.lower() == b"authorization"
        ]
        if len(authorization_values) != 1:
            return False
        scheme, separator, credential = authorization_values[0].partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not credential
            or any(character.isspace() for character in credential)
        ):
            return False
        return hmac.compare_digest(
            credential.encode("utf-8"), (self.auth_token or "").encode("utf-8")
        )

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        message: str,
        status_code: int,
    ) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": message})
            return
        response = (
            JSONResponse({"detail": message}, status_code=status_code)
            if status_code == 403 and message == "Forbidden"
            else PlainTextResponse(message, status_code=status_code)
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if not _is_loopback_client(scope):
            await self._reject(
                scope,
                receive,
                send,
                message="Local access only",
                status_code=403,
            )
            return
        if _host_name(headers.get("host", "")) not in _LOOPBACK_HOSTS:
            await self._reject(
                scope,
                receive,
                send,
                message="Invalid host header",
                status_code=400,
            )
            return

        if scope["type"] == "websocket":
            supplied_origin = headers.get("origin")
            if supplied_origin is not None and (
                _origin_parts(supplied_origin.strip()) != _request_origin(scope)
            ):
                await self._reject(
                    scope,
                    receive,
                    send,
                    message="Forbidden",
                    status_code=403,
                )
                return
        elif scope["method"] in _MUTATING_METHODS:
            fetch_site = headers.get("sec-fetch-site", "").strip().lower()
            supplied_origin = headers.get("origin")
            if fetch_site == "cross-site" or (
                supplied_origin is not None
                and _origin_parts(supplied_origin.strip()) != _request_origin(scope)
            ):
                await self._reject(
                    scope,
                    receive,
                    send,
                    message="Forbidden",
                    status_code=403,
                )
                return

        # Keep auth after all local network, Host, and Origin checks so those
        # protections retain their existing response precedence. Applying it
        # here, before routing, also covers unknown API paths and SPA fallbacks.
        routed_path = _path_relative_to_root(scope)
        if (
            self.auth_token is not None
            and scope["type"] == "http"
            and routed_path.startswith("/api/")
            and not self._authorized(scope)
        ):
            response = JSONResponse(
                {"detail": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def install_local_security(
    application: FastAPI, *, auth_token: str | None = None
) -> None:
    """Install the mandatory boundary on a local FastAPI application."""

    if auth_token is not None and (not isinstance(auth_token, str) or not auth_token):
        raise ValueError("auth_token must be a non-empty string when provided")
    application.add_middleware(LocalSecurityMiddleware, auth_token=auth_token)
    if auth_token is not None:
        original_openapi = application.openapi

        def openapi_with_bearer_security() -> dict[str, object]:
            schema = original_openapi()
            components = schema.setdefault("components", {})
            security_schemes = components.setdefault("securitySchemes", {})
            security_schemes["BearerAuth"] = {
                "type": "http",
                "scheme": "bearer",
            }
            for path, path_item in schema.get("paths", {}).items():
                if not path.startswith("/api/") or not isinstance(path_item, dict):
                    continue
                for operation in path_item.values():
                    if isinstance(operation, dict) and "responses" in operation:
                        operation["security"] = [{"BearerAuth": []}]
            application.openapi_schema = schema
            return schema

        # FastAPI explicitly supports overriding this bound method per instance.
        application.openapi = openapi_with_bearer_security  # type: ignore[method-assign]


__all__ = ["LocalSecurityMiddleware", "install_local_security"]
