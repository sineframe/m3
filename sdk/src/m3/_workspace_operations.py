"""Fixed workspace operations executed only by owned preparation children."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from .types import WorkspacePolicy
from .workspace import WorkspaceError, WorkspaceManager


def _same_directory(path: Path, expected: object) -> bool:
    try:
        current = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISDIR(current.st_mode)
        or [current.st_dev, current.st_ino] != expected
    ):
        raise WorkspaceError("workspace directory identity changed")
    return True


def cleanup_workspace(request: dict[str, Any]) -> None:
    manager = WorkspaceManager(
        WorkspacePolicy.model_validate(request["policy"]), "cleanup"
    )
    manager._root = Path(request["root"])
    manager._owned_root = request["owned"]
    preparation_root = request["preparation_root"]
    manager._preparation_root = (
        Path(preparation_root) if preparation_root is not None else None
    )
    root_exists = _same_directory(manager._root, request["identity"])
    staging_exists = manager._preparation_root is not None and _same_directory(
        manager._preparation_root, request["preparation_identity"]
    )
    if root_exists:
        manager._cleanup(bounded=True)
    elif staging_exists:
        assert manager._preparation_root is not None
        manager._preparation_root.rmdir()
