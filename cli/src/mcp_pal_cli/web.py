"""The bundled local web application used by ``mcp-pal test --ui``."""

from __future__ import annotations

import argparse
import os
import re
import sys
from importlib import resources
from pathlib import Path
from typing import NoReturn, cast

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response


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


def create_web_app(
    database: str | os.PathLike[str], *, ui_dir: str | os.PathLike[str] | None = None
) -> FastAPI:
    """Create the full application and mount the production SPA last."""

    from mcp_pal_app.api import create_app  # type: ignore[import-untyped]
    from mcp_pal_app.settings import Settings  # type: ignore[import-untyped]

    root = ui_directory(ui_dir)
    application = cast(
        FastAPI,
        create_app(
            Settings(database_path=str(Path(database).absolute())),
            v2_embedded_worker=True,
        ),
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
