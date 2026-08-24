"""Idempotent records that should exist in every MCP Pal database."""

from copy import deepcopy

from sqlalchemy.orm import Session

from ..domain.builtin_profiles import (
    EXCALIDRAW_MCP_CONFIG,
    EXCALIDRAW_PROFILE_ID,
    EXCALIDRAW_REVISION_ID,
)
from .models import McpProfile, McpProfileRevision


def ensure_builtin_profiles(db: Session) -> None:
    """Create built-in profiles once without overwriting user revisions."""
    if db.get(McpProfile, EXCALIDRAW_PROFILE_ID):
        return

    profile = McpProfile(
        id=EXCALIDRAW_PROFILE_ID,
        name="Excalidraw",
        description="Built-in Excalidraw MCP server over HTTP.",
    )
    revision = McpProfileRevision(
        id=EXCALIDRAW_REVISION_ID,
        profile_id=EXCALIDRAW_PROFILE_ID,
        revision_number=1,
        mcp_json=deepcopy(EXCALIDRAW_MCP_CONFIG),
    )
    db.add(profile)
    db.add(revision)
    db.flush()
    profile.current_revision_id = revision.id


__all__ = ["ensure_builtin_profiles"]
