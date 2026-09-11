"""The bundled local web application used by ``mcp-pal test --ui``."""

from __future__ import annotations

import argparse
import os
import re
import sys
from importlib import resources
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _fail(message: str) -> NoReturn:
    raise ValueError(message)


def _safe_startup_error(error: Exception) -> str:
    """Keep startup failures useful without echoing ambient credentials."""

    message = str(error)[:1000]
    for key, value in os.environ.items():
        if (
            value
            and len(value) >= 4
            and any(
                part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")
            )
        ):
            message = message.replace(value, "<redacted>")
    message = re.sub(
        r"(?i)(\b(?:api[_-]?key|token|secret|password|credential)\b\s*(?:=|:)\s*)[^\s,;]+",
        r"\1<redacted>",
        message,
    )
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


def ui_directory(value: str | os.PathLike[str] | None = None) -> Path:
    """Return the packaged UI directory, or a test-only directory override."""

    if value is not None:
        root = Path(value).expanduser()
    else:
        root = Path(str(resources.files("mcp_pal_cli").joinpath("ui")))
    index = root / "index.html"
    assets = root / "assets"
    if not index.is_file() or not assets.is_dir():
        _fail("bundled MCP Pal UI assets are unavailable")
    return root


def _origin_parts(value: str) -> tuple[str, str, int] | None:
    """Parse an HTTP origin and normalize its default port.

    Browser ``Origin`` headers contain only scheme, authority, and an
    optional port.  Rejecting the other URL components avoids accidentally
    treating a malformed value as a same-origin request.
    """

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


def _request_origin(request: Request) -> tuple[str, str, int] | None:
    try:
        scheme = request.url.scheme.lower()
        hostname = request.url.hostname
        if scheme not in _DEFAULT_PORTS or not hostname:
            return None
        return (
            scheme,
            hostname.rstrip(".").lower(),
            request.url.port or _DEFAULT_PORTS[scheme],
        )
    except ValueError:
        return None


async def _same_origin_mutations(request: Request, call_next):
    """Reject browser cross-site writes to the local application.

    The CLI app is intentionally local and has no login/session flow.  This
    lightweight browser boundary protects state-changing endpoints while
    preserving non-browser API clients that omit ``Origin``.
    """

    if request.method in _MUTATING_METHODS:
        fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
        if fetch_site == "cross-site":
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None:
            supplied = _origin_parts(origin.strip())
            expected = _request_origin(request)
            if supplied is None or expected is None or supplied != expected:
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
    return await call_next(request)


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
        # An IPv6 Host header must use brackets around the address.
        return None
    return hostname.rstrip(".").lower() if hostname else None


class _LoopbackHostMiddleware:
    """Allow only local Host values without TrustedHost's IPv6 limitation."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        host = _host_name(Headers(scope=scope).get("host", ""))
        if host not in _LOOPBACK_HOSTS:
            await PlainTextResponse("Invalid host header", status_code=400)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


def create_web_app(
    database: str | os.PathLike[str], *, ui_dir: str | os.PathLike[str] | None = None
) -> FastAPI:
    """Create the full application and mount the production SPA last."""

    from mcp_pal_app.api import create_app  # type: ignore[import-untyped]
    from mcp_pal_app.settings import Settings  # type: ignore[import-untyped]

    root = ui_directory(ui_dir)
    application = create_app(
        Settings(database_path=str(Path(database).absolute())), v2_embedded_worker=True
    )
    application.add_middleware(_LoopbackHostMiddleware)
    application.middleware("http")(_same_origin_mutations)
    application.mount(
        "/assets", StaticFiles(directory=str(root / "assets")), name="assets"
    )

    @application.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def spa_fallback(request: Request, path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(root / "index.html")

    return application


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise ValueError("invalid web arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeParser(prog="mcp_pal_cli.web")
    parser.add_argument("--database-path", required=True)
    parser.add_argument("--port", required=True, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if not 1 <= args.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        import uvicorn

        application = create_web_app(args.database_path)
        uvicorn.run(application, host="127.0.0.1", port=args.port, log_level="warning")
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    except Exception as exc:
        print(
            f"mcp-pal web: unable to start local UI ({_safe_startup_error(exc)})",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
