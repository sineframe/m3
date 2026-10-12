"""Private POSIX native owner; stdin/stdout/stderr belong to its gated child.

This executable helper uses only the standard library and is launched with
isolated Python import settings. The slot descriptor never enters vendor code.
"""

from __future__ import annotations

import array
import fcntl
import json
import math
import os
import resource
import runpy
import selectors
import signal
import socket
import stat
import struct
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

_MAX_FRAME = 1024 * 1024


def _lock(descriptor: int) -> bool:
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _open(path: Path) -> int:
    descriptor = os.open(
        path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    status = os.fstat(descriptor)
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != os.getuid()
        or stat.S_IMODE(status.st_mode) != 0o600
        or status.st_nlink != 1
    ):
        os.close(descriptor)
        raise ValueError("startup admission storage is not private")
    return descriptor


def _directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    status = path.lstat()
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.getuid()
        or stat.S_IMODE(status.st_mode) != 0o700
    ):
        raise ValueError("startup admission storage is not private")


class _KernelQueue:
    @staticmethod
    def acquire(
        root: Path,
        slots_limit: int,
        threshold: float | None,
        deadline: float,
        channel: socket.socket,
    ) -> int:
        selector = selectors.DefaultSelector()
        selector.register(channel, selectors.EVENT_READ)

        def checkpoint() -> None:
            if time.monotonic() >= deadline:
                raise TimeoutError
            if selector.select(0):
                raise EOFError

        checkpoint()
        _directory(root)
        checkpoint()
        tickets, slots = root / "queue", root / "slots"
        _directory(tickets)
        checkpoint()
        _directory(slots)
        checkpoint()
        guard = _open(root / "guard")
        ticket = -1
        ticket_path: Path | None = None
        try:
            while True:
                checkpoint()
                if _lock(guard):
                    try:
                        checkpoint()
                        if ticket_path is None:
                            ticket_path, ticket = _KernelQueue._enqueue(guard, tickets)
                        checkpoint()
                        first = _KernelQueue._first_waiter(tickets)
                        checkpoint()
                        if first == ticket_path and _KernelQueue._load_ready(threshold):
                            descriptor = _KernelQueue._claim_slot(slots, slots_limit)
                            if descriptor is not None:
                                try:
                                    checkpoint()
                                    ticket_path.unlink()
                                    return descriptor
                                except BaseException:
                                    os.close(descriptor)
                                    raise
                    finally:
                        fcntl.flock(guard, fcntl.LOCK_UN)
                selector.select(min(0.025, max(0, deadline - time.monotonic())))
        finally:
            if ticket >= 0:
                os.close(ticket)
            os.close(guard)
            selector.close()

    @staticmethod
    def _enqueue(guard: int, tickets: Path) -> tuple[Path, int]:
        os.lseek(guard, 0, os.SEEK_SET)
        value = os.read(guard, 32)
        try:
            sequence = int(value or b"0") + 1
        except ValueError:
            raise ValueError("startup admission sequence is invalid") from None
        if not 0 < sequence < 10**20:
            raise ValueError("startup admission sequence is exhausted")
        os.lseek(guard, 0, os.SEEK_SET)
        os.write(guard, str(sequence).encode("ascii"))
        os.ftruncate(guard, len(str(sequence)))
        path = tickets / f"{sequence:020d}"
        descriptor = _open(path)
        if not _lock(descriptor):
            os.close(descriptor)
            raise ValueError("startup admission sequence collided")
        return path, descriptor

    @staticmethod
    def _first_waiter(tickets: Path) -> Path | None:
        for path in sorted(tickets.iterdir()):
            if (
                len(path.name) != 20
                or not path.name.isascii()
                or not path.name.isdigit()
            ):
                raise ValueError("startup admission queue is invalid")
            descriptor = _open(path)
            try:
                if not _lock(descriptor):
                    return path
                path.unlink()
            finally:
                os.close(descriptor)
        return None

    @staticmethod
    def _claim_slot(slots: Path, limit: int) -> int | None:
        available: list[int] = []
        occupied = 0
        effective_limit = limit
        try:
            for path in slots.iterdir():
                descriptor = _open(path)
                if _lock(descriptor):
                    available.append(descriptor)
                else:
                    occupied += 1
                    try:
                        recorded = os.read(descriptor, 4097)
                        held_limit = int(recorded)
                        if len(recorded) > 4096 or held_limit <= 0:
                            raise ValueError
                        effective_limit = min(effective_limit, held_limit)
                    except ValueError:
                        raise ValueError(
                            "startup admission slot policy is invalid"
                        ) from None
                    finally:
                        os.close(descriptor)
            if occupied >= effective_limit:
                return None
            if available:
                descriptor = available.pop()
            else:
                path = slots / f"{occupied:020d}"
                descriptor = _open(path)
                if not _lock(descriptor):
                    os.close(descriptor)
                    raise ValueError("startup admission slot collided")
            encoded = str(limit).encode("ascii")
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, encoded)
                os.ftruncate(descriptor, len(encoded))
            except BaseException:
                os.close(descriptor)
                raise
            return descriptor
        finally:
            for descriptor in available:
                os.close(descriptor)

    @staticmethod
    def _load_ready(threshold: object) -> bool:
        if threshold is None:
            return True
        assert isinstance(threshold, float)
        try:
            return os.getloadavg()[0] / max(1, os.cpu_count() or 1) <= threshold
        except OSError:
            raise ValueError("normalized host load is unavailable") from None


def _read_exact(channel: socket.socket, size: int, deadline: float | None) -> bytes:
    data = bytearray()
    while len(data) < size:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise TimeoutError
        channel.settimeout(remaining)
        block = channel.recv(size - len(data))
        if not block:
            raise EOFError
        data.extend(block)
    return bytes(data)


def _receive(
    channel: socket.socket, startup_deadline: float | None = None
) -> dict[str, object]:
    # Before native custody the controller owns the bounded wait; EOF fences
    # a delayed helper without imposing a second startup budget.
    size = struct.unpack("!I", _read_exact(channel, 4, startup_deadline))[0]
    if size > _MAX_FRAME:
        raise ValueError
    value = json.loads(_read_exact(channel, size, startup_deadline))
    if not isinstance(value, dict):
        raise ValueError
    return value


def _send(
    channel: socket.socket, *, descriptor: int | None = None, **value: object
) -> None:
    data = json.dumps(value).encode("utf-8")
    header = struct.pack("!I", len(data))
    if descriptor is not None:
        count = channel.sendmsg(
            [header],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [descriptor]))],
        )
        header = header[count:]
    channel.sendall(header + data)


# The isolated helper loads only this fixed, stdlib-only framework source.
_custody = runpy.run_path(str(Path(__file__).parents[1] / "_process_group_custody.py"))
_members_alive = cast(Callable[[int], bool], _custody["members_alive"])
_group_snapshot = cast(Callable[[int], tuple[bool, bool]], _custody["group_snapshot"])
_has_unreaped_status = cast(Callable[[], bool], _custody["has_unreaped_status"])
_exited_status = cast(Callable[[int], int | None], _custody["exited_status"])


def _signal(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _leader_exited(pid: int) -> bool:
    if _has_unreaped_status():
        return _exited_status(pid) is not None
    # The leader must be a zombie before an empty group proves no later fork.
    return _group_snapshot(pid)[0]


def _fork(
    argv: list[str], environment: dict[str, str], cwd: str, deadline: float | None
) -> tuple[int, int]:
    ready_read, ready_write = os.pipe()
    gate_read, gate_write = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(ready_read)
            os.close(gate_write)
            end = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
            maximum = int(end) if end != resource.RLIM_INFINITY else 1048576
            start = 3
            for descriptor in sorted((ready_write, gate_read)):
                os.closerange(start, descriptor)
                start = descriptor + 1
            os.closerange(start, maximum)
            os.setsid()
            os.write(ready_write, b"S")
            os.close(ready_write)
            if os.read(gate_read, 1) != b"G":
                os._exit(0)
            os.close(gate_read)
            if deadline is not None and time.monotonic() >= deadline:
                os._exit(124)
            os.chdir(cwd)
            if deadline is not None and time.monotonic() >= deadline:
                os._exit(124)
            os.execvpe(argv[0], argv, environment)
        except BaseException:
            os._exit(127)
    os.close(ready_write)
    os.close(gate_read)
    if os.read(ready_read, 1) != b"S":
        os.close(gate_write)
        os.close(ready_read)
        os.waitpid(pid, 0)
        raise RuntimeError
    os.close(ready_read)
    return pid, gate_write


def main(channel_fd: int, slot_fd: int, death_fd: int) -> int:
    channel = socket.socket(fileno=channel_fd)
    channel.settimeout(15)
    request = _receive(channel)
    if request.get("action") == "admit":
        root, slots, threshold, queue_deadline = (
            request.get("root"),
            request.get("slots"),
            request.get("threshold"),
            request.get("deadline"),
        )
        if (
            not isinstance(root, str)
            or "\x00" in root
            or not isinstance(slots, int)
            or isinstance(slots, bool)
            or slots <= 0
            or not isinstance(queue_deadline, (float, int))
            or isinstance(queue_deadline, bool)
            or not math.isfinite(queue_deadline)
            or (
                threshold is not None
                and (
                    not isinstance(threshold, (int, float))
                    or isinstance(threshold, bool)
                    or not math.isfinite(threshold)
                    or threshold <= 0
                )
            )
        ):
            raise ValueError
        try:
            slot_fd = _KernelQueue.acquire(
                Path(root),
                slots,
                float(threshold) if threshold is not None else None,
                float(queue_deadline),
                channel,
            )
        except TimeoutError:
            _send(channel, event="queue_timeout")
            raise
        _send(channel, event="admitted", descriptor=slot_fd)
        if _receive(channel, float(queue_deadline)) != {"action": "admission_received"}:
            raise ValueError("native admission handle acknowledgement is invalid")
        # Credential preparation follows admission. No native child exists
        # here; controller EOF fences a queued or admitted helper immediately.
        selector = selectors.DefaultSelector()
        selector.register(channel, selectors.EVENT_READ)
        selector.select()
        selector.close()
        request = _receive(channel)
    argv = request.get("argv")
    environment = request.get("environment")
    cwd = request.get("cwd")
    deadline_value = request.get("deadline")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(value, str) and "\x00" not in value for value in argv)
        or not isinstance(environment, dict)
        or not all(
            isinstance(key, str)
            and isinstance(value, str)
            and "\x00" not in key + value
            for key, value in environment.items()
        )
        or not isinstance(cwd, str)
        or "\x00" in cwd
        or (
            deadline_value is not None
            and (
                isinstance(deadline_value, bool)
                or not isinstance(deadline_value, (int, float))
                or not math.isfinite(deadline_value)
            )
        )
    ):
        raise ValueError
    deadline = float(deadline_value) if deadline_value is not None else None
    pid, gate = _fork(argv, environment, cwd, deadline)
    for descriptor in (0, 1, 2):
        os.close(descriptor)
    stopping: float | None = None
    began = False
    reaped = False
    try:
        _send(channel, event="received", pid=pid)
        channel.settimeout(None)
        selector = selectors.DefaultSelector()
        selector.register(channel, selectors.EVENT_READ)
        while True:
            if (
                deadline is not None
                and time.monotonic() >= deadline
                and stopping is None
            ):
                stopping = time.monotonic()
                if gate >= 0:
                    os.close(gate)
                    gate = -1
                _signal(pid, signal.SIGTERM)
            if selector.select(0.025):
                try:
                    command = _receive(channel, deadline).get("action")
                except (EOFError, OSError, ValueError):
                    command = "stop"
                    selector.unregister(channel)
                if command == "begin" and not began and stopping is None:
                    os.write(gate, b"G")
                    os.close(gate)
                    gate = -1
                    began = True
                    _send(channel, event="started", pid=pid)
                elif command == "ready" and began and stopping is None:
                    deadline = None
                    if slot_fd >= 0:
                        os.close(slot_fd)
                        slot_fd = -1
                    _send(channel, event="ready")
                else:
                    if stopping is None:
                        stopping = time.monotonic()
                        if gate >= 0:
                            os.close(gate)
                            gate = -1
                        _signal(pid, signal.SIGTERM)
            exited = _leader_exited(pid)
            if exited and stopping is None:
                stopping = time.monotonic()
                _signal(pid, signal.SIGTERM)
            if stopping is not None:
                if time.monotonic() - stopping >= 0.25:
                    _signal(pid, signal.SIGKILL)
                if exited and not _members_alive(pid):
                    _, status = os.waitpid(pid, 0)
                    reaped = True
                    if slot_fd >= 0:
                        os.close(slot_fd)
                        slot_fd = -1
                    return os.waitstatus_to_exitcode(status)
    finally:
        # A failed control channel never authorizes slot release while native
        # processes survive. Keep this owner alive until group death is proven.
        if gate >= 0:
            os.close(gate)
        if not reaped:
            _signal(pid, signal.SIGKILL)
        while not reaped:
            if _leader_exited(pid) and not _members_alive(pid):
                os.waitpid(pid, 0)
                reaped = True
                break
            time.sleep(0.05)
        os.write(death_fd, b"D")
        os.close(death_fd)
        channel.close()
        if slot_fd >= 0:
            os.close(slot_fd)


if __name__ == "__main__":
    try:
        result = main(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]))
    except BaseException:
        result = 127
    os._exit(result if result >= 0 else 128 - result)
