"""Stable ASGI entrypoint for the Test Results API."""

from .api import create_app, create_viewer_app

app = create_app()

__all__ = ["app", "create_app", "create_viewer_app"]
