"""Read-only ASGI entrypoint used by ``mcp-pal test --ui``."""

from .api import create_viewer_app

app = create_viewer_app()

__all__ = ["app"]
