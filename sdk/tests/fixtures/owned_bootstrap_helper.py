"""Real helper fixture with an armed, kernel-blocked filesystem operation."""

from __future__ import annotations

import json
import os
import runpy
import signal
import sys
from pathlib import Path

from m3 import _bootstrap_operations as operations
from m3.workspace import WorkspaceManager

mode, arm_name, marker_name, fifo_name, late_name = sys.argv[1:6]
arm = Path(arm_name)
marker = Path(marker_name)
late = Path(late_name)
original_open = os.open
original_resource = operations._resource
original_unlink = Path.unlink
original_workspace_cleanup = WorkspaceManager._cleanup
original_workspace_capture = WorkspaceManager.capture

active_directory: Path | None = None


def block_filesystem(target: Path) -> None:
    marker.write_text(json.dumps({"pid": os.getpid(), "root": str(target.parent)}))
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    descriptor = original_open(fifo_name, os.O_WRONLY)
    os.close(descriptor)


def blocked_open(path: str | Path, flags: int, *args: object, **kwargs: object) -> int:
    global active_directory
    target = Path(path)
    if flags & getattr(os, "O_DIRECTORY", 0):
        active_directory = target
    writing = bool(flags & os.O_WRONLY)
    matching = (
        target.name == "capsule" and writing
        if mode == "write"
        else target.name == "capsule" and not writing
    )
    if mode == "capture":
        matching = target.name.endswith(".jsonl") and writing
    if mode != "remove" and arm.exists() and matching:
        if mode == "capture" and active_directory is not None:
            target = active_directory / target.name
        block_filesystem(target)
    return original_open(path, flags, *args, **kwargs)


def blocked_unlink(path: Path, *args: object, **kwargs: object) -> None:
    matching = (mode == "remove" and path.name == "capsule") or (
        mode == "native_remove" and path.name == ".owner"
    )
    if matching and arm.exists():
        block_filesystem(path)
    original_unlink(path, *args, **kwargs)


def record_resource(root: Path, path: Path, **kwargs: object) -> dict[str, object]:
    if arm.exists() and path.name == "capsule":
        late.write_text(json.dumps({"pid": os.getpid(), "root": str(root)}))
    return original_resource(root, path, **kwargs)


def blocked_workspace_cleanup(manager: WorkspaceManager, *, bounded: bool) -> None:
    if mode == "workspace_remove" and arm.exists():
        block_filesystem(manager.root / "cleanup")
    original_workspace_cleanup(manager, bounded=bounded)


def blocked_workspace_capture(manager: WorkspaceManager, outcome: object) -> object:
    if mode == "workspace_capture" and arm.exists():
        block_filesystem(manager.root / "capture")
    return original_workspace_capture(manager, outcome)


operations.os.open = blocked_open
operations._resource = record_resource
Path.unlink = blocked_unlink
WorkspaceManager._cleanup = blocked_workspace_cleanup
WorkspaceManager.capture = blocked_workspace_capture
sys.argv = [sys.argv[0], *sys.argv[6:]]
runpy.run_module("m3._preparation_worker", run_name="__main__")
