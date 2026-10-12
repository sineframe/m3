"""Controlled publication boundary in the real preparation helper.

The tiny artifact replaces native eligibility for unit tests only. The
guardian, process groups, lease, private channel and filesystem IO are real.
"""

from __future__ import annotations

import hashlib
import os
import runpy
import signal
import sys
from dataclasses import replace
from pathlib import Path

from m3 import _cache_guard as guard
from m3 import _immutable_cache as cache

mode, arm_name, ready_name, gate_name, late_name = sys.argv[1:6]
payload = b"controlled-ripgrep-artifact"
artifact = replace(
    cache.RIPGREP,
    binary_size=len(payload),
    binary_sha256=hashlib.sha256(payload).hexdigest(),
)
cache.RIPGREP = guard.RIPGREP = artifact
guard.validate_native = lambda request: None
guard.download_binary = lambda *, timeout: payload
original_replace = os.replace


def controlled_replace(source: Path, target: Path) -> None:
    if (
        Path(arm_name).exists()
        and source.name.startswith(artifact.key + ".pending-")
        and target.name == artifact.key
    ):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        Path(ready_name).write_text(str(os.getpid()))
        descriptor = os.open(gate_name, os.O_RDONLY)
        try:
            os.read(descriptor, 1)
        finally:
            os.close(descriptor)
        original_replace(source, target)
        Path(late_name).touch()
    else:
        original_replace(source, target)


guard.os.replace = controlled_replace
sys.argv = [sys.argv[0], *sys.argv[6:]]
runpy.run_module("m3._preparation_worker", run_name="__main__")
