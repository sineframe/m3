"""Stable ASGI entrypoint for the MCP Testing Platform."""
from .api import create_app, create_viewer_app

app = create_app()

__all__ = ["app", "create_app", "create_viewer_app"]
