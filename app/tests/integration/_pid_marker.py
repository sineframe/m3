"""PID marker readiness helpers for subprocess integration tests."""

import time
from pathlib import Path


def read_pid(marker: Path) -> int | None:
    """Return a positive PID only after the marker contains one."""
    try:
        pid = int(marker.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return pid if pid > 0 else None


def wait_for_pid(marker: Path, *, timeout: float = 2.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = read_pid(marker)
        if pid is not None:
            return pid
        time.sleep(0.01)
    raise AssertionError(f"subprocess did not write a positive PID to {marker}")
