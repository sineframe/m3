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
    # A group id by itself is never sufficient ownership evidence: after a
    # leader exits, the id can be reused by an unrelated process group.  All
    # native launchers use ``start_new_session`` and therefore have pgid ==
    # pid.  Require the direct child for every signal attempt, while retaining
    # the captured group id so descendants can be cleaned after the leader is
    # reaped.
    if pid is None or not isinstance(pid, int) or pid <= 1:
        return False
    if pgid is None:
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            return False
    if not isinstance(pgid, int) or pgid <= 1:
        return False
    try:
        current_pgid = os.getpgid(pid)
    except ProcessLookupError:
        # The process owner may have reaped the direct child before cleanup.
        # Only the group rooted at that child's PID remains safely
        # attributable; never signal an arbitrary caller-supplied group.
        if pgid != pid:
            return False
        current_pgid = None
    except (PermissionError, OSError):
        return False
    else:
        if current_pgid != pgid:
            return False
        # A non-leader group is not one created by our native launchers.  Keep
        # this check even when the caller supplied a matching current pgid so
        # a future caller cannot accidentally widen the helper's scope.
        if pgid != pid:
            return False
    # Never signal the backend's own process group if an SDK implementation
    # failed to create a distinct one.
    if pgid == os.getpgrp():
        return False

    def members() -> list[int]:
        try:
            listing = subprocess.run(["ps", "-axo", "pid=,pgid="], capture_output=True, text=True, check=False).stdout
            return [
                int(member_pid)
                for line in listing.splitlines()
                if len((parts := line.split())) == 2 and parts[1] == str(pgid)
                for member_pid in [parts[0]]
                if int(member_pid) not in {os.getpid(), pid}
            ]
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
            try:
                os.kill(member, signal.SIGTERM)
                attempted = True
            except (ProcessLookupError, PermissionError, OSError):
                pass
    if not attempted:
        return False
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            if not members():
                return True
            time.sleep(0.02)
            continue
        except PermissionError:
            return True
        except OSError:
            return False
        time.sleep(0.02)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        for member in members():
            try:
                os.kill(member, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    # SIGKILL is asynchronous.  Do not return ownership to the runner while
    # descendants can still be executing in the harness workspace; otherwise
    # its subsequent rmtree can race with a child still creating files.  The
    # direct child is reaped by the caller, so this bounded poll only waits for
    # the remaining process-group members.
    reap_deadline = time.monotonic() + max(0.1, grace_seconds)
    while time.monotonic() < reap_deadline:
        if not members():
            break
        time.sleep(0.02)
    return True


__all__ = ["terminate_process_group"]
