"""Small POSIX helper for ACP process-group cleanup.

The ACP SDK owns the subprocess transport and exposes only the direct child.
When a harness starts an MCP child, terminating that direct PID is not enough.
Call :func:`terminate_process_group` with the SDK process PID from the runner's
``finally`` block.  Windows deliberately uses the SDK's normal child cleanup;
the stronger group guarantee is POSIX-only.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time


def terminate_process_group(pid: int | None = None, *, pgid: int | None = None, grace_seconds: float = 1.0) -> bool:
    """TERM, then KILL, the process group rooted at *pid*.

    Returns whether a POSIX group signal was attempted.  It is safe to call
    after the SDK has already reaped the child.
    """

    if os.name == "nt":
        return False
    if pgid is None:
        if pid is None:
            return False
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            return False
    # Never signal the backend's own process group if an SDK implementation
    # failed to create a distinct one.
    if pgid == os.getpgrp():
        return False
    def members() -> list[int]:
        try:
            listing = subprocess.run(["ps", "-axo", "pid=,pgid="], capture_output=True, text=True, check=False).stdout
            return [int(pid) for line in listing.splitlines() if len((parts := line.split())) == 2 and parts[1] == str(pgid) for pid in [parts[0]] if int(pid) != os.getpid()]
        except (OSError, ValueError):
            return []

    attempted = False
    try:
        os.killpg(pgid, signal.SIGTERM)
        attempted = True
    except (ProcessLookupError, PermissionError):
        # macOS can report ESRCH once a session leader has exited even though
        # descendants remain in the group. Signal the retained members by PID.
        for member in members():
            try: os.kill(member, signal.SIGTERM); attempted = True
            except (ProcessLookupError, PermissionError): pass
    if not attempted:
        return False
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            if not members(): return True
            time.sleep(0.02)
            continue
        except PermissionError:
            return True
        time.sleep(0.02)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        for member in members():
            try: os.kill(member, signal.SIGKILL)
            except (ProcessLookupError, PermissionError): pass
    return True


__all__ = ["terminate_process_group"]
