"""Cache-specific lease custody inside an acknowledged preparation group."""

from __future__ import annotations

import fcntl
import json
import os
import select
import shutil
import signal
import socket
import time
from pathlib import Path

from ._bootstrap_operations import (
    PrivateOperationError,
    _private,
    _receive_private,
    _remaining,
    _resource,
    _send_private,
)
from ._immutable_cache import (
    RIPGREP,
    CacheRequest,
    download_binary,
    materialize_binary,
    validate_native,
    verified_binary,
)
from ._preparation_worker import TERM_GRACE, _ForkJob


def _peer_open(channel: socket.socket) -> bool:
    readable, _, _ = select.select([channel], [], [], 0)
    return not readable or bool(channel.recv(1, socket.MSG_PEEK))


def _wait_writer(writer: _ForkJob, channel: socket.socket, deadline: float) -> None:
    """Retain the waitable child identity until every mutable writer is dead."""
    stop_at: float | None = None
    killed = False
    while writer.poll() is None or writer.alive():
        expired = time.monotonic() >= deadline or not _peer_open(channel)
        if expired and stop_at is None:
            stop_at = time.monotonic()
            writer.signal(signal.SIGTERM)
        if stop_at is not None and not killed:
            if time.monotonic() - stop_at >= TERM_GRACE:
                writer.signal(signal.SIGKILL)
                killed = True
        # Waiting does not synchronize work; the child's group ACK/start gate
        # establishes ownership. This interval only bounds cancellation poll.
        select.select([], [], [], 0.01)
    if stop_at is not None or writer.returncode != 0:
        raise PrivateOperationError("immutable cache writer failed")
    _remaining(deadline)


def _acquire(descriptor: int, channel: socket.socket, deadline: float) -> None:
    while True:
        _remaining(deadline)
        if not _peer_open(channel):
            raise PrivateOperationError("immutable cache caller ended")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            select.select([channel], [], [], min(0.02, _remaining(deadline)))


def _valid_seed(seed: Path) -> bool:
    try:
        _private(seed.stat(follow_symlinks=False), directory=True)
        descriptor = os.open(
            seed / "accepted", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        try:
            _private(os.fstat(descriptor), directory=False)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                metadata = json.loads(stream.read(1025))
            if metadata != {"version": 1, "artifact": RIPGREP.key}:
                return False
        finally:
            os.close(descriptor)
    except (OSError, ValueError, PrivateOperationError):
        return False
    return verified_binary(seed / "rg")


def execute_cache_guard(channel: socket.socket, root: Path, deadline: float) -> None:
    """Hold the seed lease through writer death and accepted publication.

    This group is established and acknowledged by the ordinary preparation
    owner before entering here. The fixed child has its own group and start
    gate; the guardian is its waitable parent and retains the lease throughout
    cancellation. No external command or user callback can run in that child.
    """
    request, unexpected = _receive_private(channel, deadline)
    if unexpected is not None:
        os.close(unexpected)
        raise PrivateOperationError("invalid immutable cache request")
    if (
        set(request)
        != {
            "version",
            "deadline",
            "root",
            "native_executable",
            "harness_version",
        }
        or request["deadline"] != deadline
    ):
        raise PrivateOperationError("invalid immutable cache request")
    if any(
        not isinstance(request[key], str)
        for key in ("root", "native_executable", "harness_version")
    ):
        raise PrivateOperationError("invalid immutable cache request")
    inputs = CacheRequest(
        Path(request["root"]),
        Path(request["native_executable"]),
        request["harness_version"],
    )
    validate_native(inputs)
    _remaining(deadline)
    inputs.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _private(inputs.root.stat(follow_symlinks=False), directory=True)
    lock = os.open(
        inputs.root / (RIPGREP.key + ".lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    candidate = inputs.root / (RIPGREP.key + ".pending-" + root.name)
    seed = inputs.root / RIPGREP.key
    destination = root / "cache" / "opencode" / "bin" / "rg"
    writer: _ForkJob | None = None
    cold = False
    committed = False
    death_reported = False
    try:
        _private(os.fstat(lock), directory=False)
        _acquire(lock, channel, deadline)
        _remaining(deadline)
        cold = not _valid_seed(seed)
        if cold and seed.exists():
            quarantine = inputs.root / (RIPGREP.key + ".corrupt-" + root.name)
            os.replace(seed, quarantine)
            _remaining(deadline)

        def materialize() -> None:
            if cold:
                candidate.mkdir(mode=0o700)
                raw = download_binary(timeout=_remaining(deadline))
                with (candidate / "rg").open("xb") as stream:
                    stream.write(raw)
                (candidate / "rg").chmod(0o500)
                if not verified_binary(candidate / "rg"):
                    raise PrivateOperationError("immutable cache seed is invalid")
                materialize_binary(candidate / "rg", destination)
                _remaining(deadline)
                os.replace(candidate, seed)
            else:
                materialize_binary(seed / "rg", destination)
            _remaining(deadline)

        writer = _ForkJob(materialize, output=False, gated=True, keep_fds=(lock,))
        # setsid receipt precedes the start gate. No PID guessing or discovery.
        readable, _, _ = select.select(
            [writer.ready, channel], [], [], _remaining(deadline)
        )
        if writer.ready not in readable or not _peer_open(channel):
            raise PrivateOperationError("immutable cache writer did not start")
        if writer.ready.read(1) != b"s":
            raise PrivateOperationError("immutable cache writer group unavailable")
        writer.ready.close()
        writer.group_established = True
        writer.begin()
        _wait_writer(writer, channel, deadline)
        os.write(1, b'{"cache_writers_dead":true}\n')
        death_reported = True
        _send_private(
            channel,
            {"version": 1, "resource": _resource(root, destination, native_home=True)},
            deadline,
        )
        # Generic ready is normally emitted after the whole job dies. Here
        # writer death is proved above while lease custody must remain alive.
        os.write(1, b'{"cache_ready":true}\n')
        channel.settimeout(_remaining(deadline))
        if channel.recv(1) != b"c":
            raise PrivateOperationError("immutable cache acceptance ended")
        _remaining(deadline)
        if cold:
            descriptor = os.open(
                seed / "accepted",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(
                    json.dumps({"version": 1, "artifact": RIPGREP.key}).encode("ascii")
                )
            _remaining(deadline)
        committed = True
    finally:
        if writer is not None:
            if writer.poll() is None or writer.alive():
                writer.signal(signal.SIGKILL)
                while writer.poll() is None or writer.alive():
                    select.select([], [], [], 0.01)
            writer.ready.close()
        if not death_reported:
            os.write(1, b'{"cache_writers_dead":true}\n')
        # The lock is deliberately retained if filesystem cleanup stalls.
        # A late rename cannot become eligible to another reader meanwhile.
        if not committed:
            if cold and seed.exists():
                os.replace(seed, inputs.root / (RIPGREP.key + ".rejected-" + root.name))
            if candidate.exists():
                shutil.rmtree(candidate)
        os.close(lock)
