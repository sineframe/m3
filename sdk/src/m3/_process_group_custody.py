"""Waitable POSIX leaders reserve group identities until writers are dead."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


class ProcessStatusUnavailable(RuntimeError):
    """The host cannot provide the process inventory needed for ownership."""


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


def _linux_status(path: Path) -> tuple[int, bytes]:
    # comm can contain whitespace and parentheses. The final ')' terminates it.
    fields = path.read_bytes().rsplit(b")", 1)[1].split()
    return int(fields[2]), fields[0]


def _inventory() -> dict[int, tuple[int, bytes]]:
    try:
        if sys.platform.startswith("linux"):
            result = {}
            for entry in Path("/proc").iterdir():
                if not entry.name.isdecimal():
                    continue
                try:
                    result[int(entry.name)] = _linux_status(entry / "stat")
                except FileNotFoundError:
                    # A process disappearing cannot remain a writer.
                    continue
            return result
        environment = dict(os.environ, COMMAND_MODE="unix2003")
        result_ps = subprocess.run(
            ["/bin/ps", "-axo", "pid=,pgid=,stat="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=1,
            check=False,
        )
        if result_ps.returncode:
            raise ProcessStatusUnavailable("process inventory command failed")
        result = {}
        for line in result_ps.stdout.splitlines():
            member, group, state = line.split()
            result[int(member)] = (int(group), state)
        return result
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as error:
        raise ProcessStatusUnavailable("process inventory is unavailable") from error


class GroupInventory:
    """One lazy inventory shared by all exited jobs in a supervisor tick."""

    def __init__(self) -> None:
        self._entries: dict[int, tuple[int, bytes]] | None = None

    def snapshot(self, pid: int) -> tuple[bool, bool]:
        if self._entries is None:
            self._entries = _inventory()
        leader = self._entries.get(pid)
        if leader is None:
            raise ProcessStatusUnavailable("reserved process leader is missing")
        return leader[1].startswith(b"Z"), any(
            member != pid and group == pid and not state.startswith(b"Z")
            for member, (group, state) in self._entries.items()
        )


def group_snapshot(pid: int) -> tuple[bool, bool]:
    return GroupInventory().snapshot(pid)


@lru_cache(maxsize=1)
def validate_process_status() -> None:
    if sys.platform.startswith("linux"):
        try:
            mounts = Path("/proc/self/mountinfo").read_text()
            found = False
            for line in mounts.splitlines():
                fields = line.split()
                if fields[4] != "/proc":
                    continue
                found = True
                options = fields[5].split(",") + fields[-1].split(",")
                if any(
                    option.startswith("hidepid=") and option != "hidepid=0"
                    for option in options
                ):
                    raise ProcessStatusUnavailable(
                        "process inventory hides group members"
                    )
            if not found:
                raise ProcessStatusUnavailable(
                    "process inventory protection is unknown"
                )
        except (OSError, IndexError) as error:
            raise ProcessStatusUnavailable(
                "process inventory protection is unknown"
            ) from error
    entries = _inventory()
    if os.getpid() not in entries:
        raise ProcessStatusUnavailable("process inventory cannot observe its owner")
    if not has_unreaped_status() and not sys.platform.startswith("linux"):
        if not hasattr(select, "kqueue"):
            raise ProcessStatusUnavailable("unreaped exit notification is unavailable")


class ExitNotification:
    """Observe Darwin child exit without reaping or spawning a status command."""

    def __init__(self, pid: int) -> None:
        self._queue = select.kqueue()
        try:
            self._queue.control(
                [
                    select.kevent(
                        pid,
                        filter=select.KQ_FILTER_PROC,
                        flags=select.KQ_EV_ADD,
                        fflags=select.KQ_NOTE_EXIT,
                    )
                ],
                0,
                0,
            )
        except BaseException:
            self._queue.close()
            raise

    def exited(self) -> bool:
        return bool(self._queue.control(None, 1, 0))

    def close(self) -> None:
        self._queue.close()


def fallback_exited(pid: int, notification: ExitNotification | None) -> bool:
    if notification is not None:
        return notification.exited()
    try:
        return _linux_status(Path("/proc") / str(pid) / "stat")[1].startswith(b"Z")
    except (OSError, ValueError, IndexError) as error:
        raise ProcessStatusUnavailable(
            "reserved child status is unavailable"
        ) from error


def seal_group(pid: int) -> None:
    """Prevent a missed concurrent fork from surviving an empty inventory."""
    # The waitable leader still reserves the group identity. SIGKILL cannot be
    # ignored; a subsequent inventory must confirm death before it is reaped.
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        # Linux may report ESRCH for a group containing only dead tasks. This
        # is not our death proof: the next fresh inventory must still agree.
        pass
