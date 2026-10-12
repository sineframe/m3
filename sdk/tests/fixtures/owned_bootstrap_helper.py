"""Real helper fixture with an armed, kernel-blocked filesystem operation."""

from __future__ import annotations

import json
import os
import runpy
import signal
import sys
from pathlib import Path

from m3 import _bootstrap_operations as operations

mode, arm_name, marker_name, fifo_name, late_name = sys.argv[1:6]
arm = Path(arm_name)
marker = Path(marker_name)
late = Path(late_name)
original_open = os.open
original_resource = operations._resource
original_unlink = Path.unlink


def block_filesystem(target: Path) -> None:
    marker.write_text(json.dumps({"pid": os.getpid(), "root": str(target.parent)}))
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    descriptor = original_open(fifo_name, os.O_WRONLY)
    os.close(descriptor)


def blocked_open(path: str | Path, flags: int, *args: object, **kwargs: object) -> int:
    target = Path(path)
    writing = bool(flags & os.O_WRONLY)
    matching = (
        target.name == "capsule" and writing
        if mode == "write"
        else target.name == "capsule" and not writing
    )
    if mode != "remove" and arm.exists() and matching:
        block_filesystem(target)
    return original_open(path, flags, *args, **kwargs)


def blocked_unlink(path: Path, *args: object, **kwargs: object) -> None:
    if mode == "remove" and arm.exists() and path.name == "capsule":
        block_filesystem(path)
    original_unlink(path, *args, **kwargs)


def record_resource(root: Path, path: Path, **kwargs: object) -> dict[str, object]:
    if arm.exists() and path.name == "capsule":
        late.write_text(json.dumps({"pid": os.getpid(), "root": str(root)}))
    return original_resource(root, path, **kwargs)


operations.os.open = blocked_open
operations._resource = record_resource
Path.unlink = blocked_unlink
sys.argv = [sys.argv[0], *sys.argv[6:]]
runpy.run_module("m3._preparation_worker", run_name="__main__")
