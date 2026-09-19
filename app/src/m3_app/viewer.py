"""Read-only ASGI entrypoint used by ``m3 test --ui``."""

from .api import create_viewer_app

app = create_viewer_app()

__all__ = ["app"]
