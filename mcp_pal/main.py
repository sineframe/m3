"""Compatibility entry point for running the FastAPI service."""
from .api import app, create_app

__all__ = ["app", "create_app"]
