"""Private POSIX native owner; stdin/stdout/stderr belong to its gated child.

This executable helper uses only the standard library and is launched with
isolated Python import settings. The slot descriptor never enters vendor code.
"""

from __future__ import annotations

import json
import math
import os
import resource
import selectors
import signal
import socket
import struct
import subprocess
import sys
import time

_MAX_FRAME = 1024 * 1024


def _read_exact(channel: socket.socket, size: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
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
    deadline = time.monotonic() + 15
    if startup_deadline is not None:
        deadline = min(deadline, startup_deadline)
    size = struct.unpack("!I", _read_exact(channel, 4, deadline))[0]
    if size > _MAX_FRAME:
        raise ValueError
    value = json.loads(_read_exact(channel, size, deadline))
    if not isinstance(value, dict):
        raise ValueError
    return value


def _send(channel: socket.socket, **value: object) -> None:
    data = json.dumps(value).encode("utf-8")
    channel.sendall(struct.pack("!I", len(data)) + data)


def _members_alive(pid: int) -> bool:
    """The unreaped leader reserves PID/PGID while descendants are checked."""
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,pgid=,stat="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1,
            check=False,
        )
        if completed.returncode:
            return True
        for line in completed.stdout.splitlines():
            fields = line.split()
            if len(fields) != 3:
                return True
            member, group, state = fields
            if int(group) == pid and int(member) != pid and not state.startswith(b"Z"):
                return True
        return False
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return True


def _signal(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _leader_exited(pid: int) -> bool:
    waitid = getattr(os, "waitid", None)
    if waitid is None:
        raise RuntimeError("native ownership requires unreaped child status")
    return waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


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


def main(channel_fd: int, slot_fd: int) -> int:
    channel = socket.socket(fileno=channel_fd)
    channel.settimeout(15)
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
        channel.close()
        if slot_fd >= 0:
            os.close(slot_fd)


if __name__ == "__main__":
    try:
        result = main(int(sys.argv[1]), int(sys.argv[2]))
    except BaseException:
        result = 127
    os._exit(result if result >= 0 else 128 - result)
