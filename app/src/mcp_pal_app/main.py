"""Canonical ASGI entrypoint for the MCP Testing Platform."""
from .api import app, create_app

__all__ = ["app", "create_app"]
