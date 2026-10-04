"""Request identity and client identification for control-plane HTTP calls."""

from __future__ import annotations

import platform
import uuid
from importlib.metadata import PackageNotFoundError, version

REQUEST_ID_HEADER = "X-Request-ID"


def new_request_id() -> str:
    """Return a fresh client request ID; use one per HTTP attempt."""
    return str(uuid.uuid4())


def cli_version() -> str:
    try:
        return version("sf-m3-cli")
    except PackageNotFoundError:
        return "0+unknown"


def user_agent() -> str:
    return f"m3-cli/{cli_version()} python/{platform.python_version()}"


def base_headers(request_id: str) -> dict[str, str]:
    return {REQUEST_ID_HEADER: request_id, "User-Agent": user_agent()}
