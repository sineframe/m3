"""FastAPI application and API-layer schemas."""
from typing import Any

from .app import create_app, create_viewer_app

# Importing the ``.app`` submodule temporarily installs that module under the
# package's ``app`` attribute. Remove it so the historical FastAPI export can
# remain lazy rather than constructing a second application in viewer mode.
globals().pop("app", None)


def __getattr__(name: str) -> Any:
    # Keep the historical ``mcp_pal_app.api.app`` export without constructing
    # the normal application when the dedicated viewer module is imported.
    if name == "app":
        application = create_app()
        globals()[name] = application
        return application
    raise AttributeError(name)


__all__ = ["app", "create_app", "create_viewer_app"]
