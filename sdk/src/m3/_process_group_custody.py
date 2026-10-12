"""Waitable POSIX leaders reserve group identities until writers are dead."""

from __future__ import annotations

import os
import subprocess
import sys


def ownership_supported() -> bool:
    return os.name == "posix"


def has_unreaped_status() -> bool:
    return callable(getattr(os, "waitid", None)) and all(
        hasattr(os, name) for name in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    )


def exited_status(pid: int) -> int | None:
    """Observe an owned child exit without releasing its PID reservation."""
    waitid = getattr(os, "waitid", None)
    if not has_unreaped_status() or waitid is None:
        raise RuntimeError("process ownership requires unreaped child status")
    info = waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if info is None:
        return None
    status = int(info.si_status)
    return -status if info.si_code in {os.CLD_KILLED, os.CLD_DUMPED} else status


def group_snapshot(pid: int) -> tuple[bool, bool]:
    """Query only the reserved group identity; zombies cannot mutate state.

    The caller must still own the unreaped leader. Query failures retain
    ownership. This never discovers a process by command name or PID age.
    """
    try:
        # Darwin's documented -g selector accepts process-group leaders; -p
        # also includes our child before its setsid acknowledgement. Other
        # platforms retain the portable full listing because -g differs there.
        command = (
            ["/bin/ps", "-g", str(pid), "-p", str(pid), "-o", "pid=,pgid=,stat="]
            if sys.platform == "darwin"
            else ["/bin/ps", "-axo", "pid=,pgid=,stat="]
        )
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1,
            check=False,
        )
        if result.returncode:
            return False, True
        leader_zombie = False
        live_members = False
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) != 3:
                return False, True
            member, group, state = fields
            if int(member) == pid:
                leader_zombie = state.startswith(b"Z")
            elif int(group) == pid and not state.startswith(b"Z"):
                live_members = True
        return leader_zombie, live_members
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False, True


def members_alive(pid: int) -> bool:
    return group_snapshot(pid)[1]
