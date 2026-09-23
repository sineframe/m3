"""The bundled local web application used by ``m3 test --ui``."""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
from importlib import resources
from pathlib import Path
from typing import NoReturn

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

_AUTH_TOKEN_MAX_BYTES = 256
_AUTH_TOKEN_PATTERN = re.compile(rb"^[A-Za-z0-9_-]{32,256}$")


def _fail(message: str) -> NoReturn:
    raise ValueError(message)


def _safe_startup_error(error: Exception, auth_token: str | None = None) -> str:
    """Keep startup failures useful without echoing ambient credentials."""

    message = str(error)[:1000]
    if auth_token:
        message = message.replace(auth_token, "<redacted>")
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
        root = Path(str(resources.files("m3_cli").joinpath("ui")))
    index = root / "index.html"
    assets = root / "assets"
    if not index.is_file() or not assets.is_dir():
        _fail("bundled M3 UI assets are unavailable")
    return root


def create_web_app(
    database: str | os.PathLike[str],
    *,
    auth_token: str,
    ui_dir: str | os.PathLike[str] | None = None,
) -> FastAPI:
    """Create the full application and mount the production SPA last."""

    if not isinstance(auth_token, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{32,256}", auth_token
    ):
        raise ValueError("invalid local UI credentials")

    from m3_app.api import create_app
    from m3_app.settings import Settings

    root = ui_directory(ui_dir)
    application = create_app(
        Settings(database_path=str(Path(database).absolute())),
        v2_embedded_worker=True,
        auth_token=auth_token,
    )
    application.mount(
        "/assets", StaticFiles(directory=str(root / "assets")), name="assets"
    )

    @application.api_route("/{path:path}", methods=["GET", "HEAD"], response_model=None)
    async def spa_fallback(request: Request, path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(root / "index.html")

    return application


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise ValueError("invalid web arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeParser(prog="m3_cli.web")
    parser.add_argument("--database-path", required=True)
    parser.add_argument("--port", required=True, type=int)
    return parser


def _read_auth_token(stream: object) -> str:
    """Read one bounded token line from the supervisor's private stdin pipe."""

    read = getattr(stream, "readline", None)
    if not callable(read):
        raise ValueError("missing local UI credentials")
    raw = read(_AUTH_TOKEN_MAX_BYTES + 2)
    if not isinstance(raw, bytes):
        raise ValueError("invalid local UI credentials")
    token = raw.rstrip(b"\r\n")
    if (
        not raw.endswith(b"\n")
        or len(raw) > _AUTH_TOKEN_MAX_BYTES + 1
        or not _AUTH_TOKEN_PATTERN.fullmatch(token)
    ):
        raise ValueError("invalid local UI credentials")
    if read(1) != b"":
        raise ValueError("invalid local UI credentials")
    return token.decode("ascii")


def main(argv: list[str] | None = None) -> int:
    auth_token: str | None = None
    try:
        args = _parser().parse_args(argv)
        if not 1 <= args.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        import uvicorn

        auth_token = _read_auth_token(sys.stdin.buffer)
        application = create_web_app(args.database_path, auth_token=auth_token)

        class _ReadinessServer(uvicorn.Server):
            async def startup(self, sockets: list[socket.socket] | None = None) -> None:
                await super().startup(sockets=sockets)
                if self.started:
                    sys.stdout.write("\x00M3_UI_SERVER_READY\x00\n")
                    sys.stdout.flush()

        config = uvicorn.Config(
            application, host="127.0.0.1", port=args.port, log_level="warning"
        )
        _ReadinessServer(config).run()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    except Exception as exc:
        print(
            f"m3 web: unable to start local UI ({_safe_startup_error(exc, auth_token)})",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
