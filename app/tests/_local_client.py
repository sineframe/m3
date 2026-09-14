"""Test client whose ASGI scope represents the supported local deployment."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient as FastAPITestClient
from starlette.types import ASGIApp


class TestClient(FastAPITestClient):
    def __init__(self, app: ASGIApp, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", "http://127.0.0.1")
        kwargs.setdefault("client", ("127.0.0.1", 50000))
        super().__init__(app, **kwargs)
