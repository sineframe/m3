"""Waitable POSIX leaders reserve group identities until writers are dead."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
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
                except (FileNotFoundError, ProcessLookupError):
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
        return _parse_inventory(result_ps.stdout)
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as error:
        raise ProcessStatusUnavailable("process inventory is unavailable") from error


class ProcessStatusPending(Exception):
    """A waitable status query has not produced a fresh inventory yet."""


class InventoryCollector:
    """Read Darwin ps without blocking controller frames or job cancellation."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._output = bytearray()
        self._started = 0.0
        self._retry_at = 0.0
        self._failed = False
        self._readable = False

    def read(self) -> dict[int, tuple[int, bytes]]:
        if sys.platform.startswith("linux"):
            entries = _inventory()
            validate_collected_status(entries)
            return entries
        now = time.monotonic()
        if self._process is None:
            self._start(now)
        process = self._process
        assert process is not None
        self._consume(chunks=4)
        if now - self._started >= 1 or self._failed:
            self._failed = True
            if process.poll() is None:
                process.kill()
        status = process.poll()
        if status is None:
            raise ProcessStatusPending
        return self._finish(now, status)

    def _start(self, now: float) -> None:
        if now < self._retry_at:
            raise ProcessStatusPending
        self._started = now
        self._failed = False
        self._readable = False
        self._output.clear()
        try:
            self._process = subprocess.Popen(
                ["/bin/ps", "-axo", "pid=,pgid=,stat="],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=dict(os.environ, COMMAND_MODE="unix2003"),
            )
            assert self._process.stdout is not None
            os.set_blocking(self._process.stdout.fileno(), False)
            self._readable = True
        except OSError as error:
            if self._process is not None:
                self._failed = True
                self._process.kill()
                return  # Report failure once, after this query is reaped.
            self._retry_at = now + 0.3
            raise ProcessStatusUnavailable(
                "process inventory is unavailable"
            ) from error

    def _consume(self, *, chunks: int) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        if not self._readable:
            return
        try:
            for _ in range(chunks):
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                self._output.extend(chunk)
                if len(self._output) > 4 * 1024 * 1024:
                    self._failed = True
                    break
        except BlockingIOError:
            pass
        except OSError:
            self._failed = True

    def _finish(self, now: float, status: int) -> dict[int, tuple[int, bytes]]:
        process = self._process
        assert process is not None and process.stdout is not None
        # Drain the bounded remaining output after the child was reaped.
        self._consume(chunks=65)
        process.stdout.close()
        self._process = None
        if status or self._failed:
            self._retry_at = now + 0.3
            raise ProcessStatusUnavailable("process inventory command failed")
        try:
            entries = _parse_inventory(bytes(self._output))
            validate_collected_status(entries)
            return entries
        except (ValueError, IndexError, ProcessStatusUnavailable) as error:
            self._retry_at = now + 0.3
            raise ProcessStatusUnavailable(
                "process inventory is unavailable"
            ) from error

    @property
    def output_descriptor(self) -> int | None:
        process = self._process
        if process is None or process.stdout is None or not self._readable:
            return None
        return process.stdout.fileno()

    def close(self) -> bool:
        process = self._process
        if process is None:
            return True
        if process.poll() is None:
            process.kill()
            return False
        if process.stdout is not None:
            process.stdout.close()
        self._process = None
        return True


def _parse_inventory(output: bytes) -> dict[int, tuple[int, bytes]]:
    entries = {}
    for line in output.splitlines():
        member, group, state = line.split()
        entries[int(member)] = (int(group), state)
    return entries


class GroupInventory:
    """One lazy inventory, including a failed query, shared by a helper tick."""

    def __init__(self, collector: InventoryCollector | None = None) -> None:
        self._collector = collector
        self._entries: dict[int, tuple[int, bytes]] | None = None
        self.failed = False
        self._pending = False

    def read(self) -> dict[int, tuple[int, bytes]]:
        if self.failed:
            raise ProcessStatusUnavailable("process inventory is unavailable")
        if self._pending:
            raise ProcessStatusPending
        if self._entries is None:
            try:
                self._entries = (
                    self._collector.read() if self._collector else _inventory()
                )
            except ProcessStatusUnavailable:
                self.failed = True
                raise
            except ProcessStatusPending:
                self._pending = True
                raise
        return self._entries

    @property
    def completed(self) -> bool:
        return self._entries is not None

    def snapshot(self, pid: int) -> tuple[bool, bool]:
        entries = self.read()
        leader = entries.get(pid)
        if leader is None:
            if self._collector is not None and not sys.platform.startswith("linux"):
                # This child may have been forked after an asynchronous query
                # began. Wait for a new inventory instead of treating it as a
                # failed backend or abandoning its waitable identity.
                raise ProcessStatusPending
            self.failed = True
            raise ProcessStatusUnavailable("reserved process leader is missing")
        return leader[1].startswith(b"Z"), any(
            member != pid and group == pid and not state.startswith(b"Z")
            for member, (group, state) in entries.items()
        )


def group_snapshot(pid: int) -> tuple[bool, bool]:
    # Standalone native guardians retain ownership when status is unknown.
    # Preparation uses GroupInventory directly to report unavailable status.
    try:
        return GroupInventory().snapshot(pid)
    except ProcessStatusUnavailable:
        return False, True


def members_alive(pid: int) -> bool:
    """Query live members while the caller retains its waitable leader."""
    return group_snapshot(pid)[1]


@lru_cache(maxsize=1)
def validate_capabilities() -> None:
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
    if not has_unreaped_status() and not sys.platform.startswith("linux"):
        if not hasattr(select, "kqueue"):
            raise ProcessStatusUnavailable("unreaped exit notification is unavailable")


def validate_collected_status(entries: dict[int, tuple[int, bytes]]) -> None:
    validate_capabilities()
    if os.getpid() not in entries:
        raise ProcessStatusUnavailable("process inventory cannot observe its owner")


@lru_cache(maxsize=1)
def validate_process_status() -> None:
    validate_collected_status(_inventory())


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
