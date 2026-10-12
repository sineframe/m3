"""Controller-side control channel for an admitted native process owner."""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import struct
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from ..errors import CleanupError, UnsupportedFeature
from .admission import StartupLease
from .contracts import HarnessStartupError

_MAX_FRAME = 1024 * 1024
_OWNED_GUARDIANS: set[NativeGuardian] = set()


class NativeGuardian:
    """Own a pending helper spawn, private channel and eventual native group."""

    def __init__(self, channel: socket.socket, deadline: float | None) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._spawn_task: asyncio.Task[asyncio.subprocess.Process] | None = None
        self._reap_task: asyncio.Task[None] | None = None
        self._channel = channel
        self.native_pid: int | None = None
        self._commands = asyncio.Lock()
        self._closed = False
        self._deadline = deadline
        self._ready = False

    @property
    def process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise HarnessStartupError("native owner has not started")
        return self._process

    @property
    def process_if_started(self) -> asyncio.subprocess.Process | None:
        return self._process

    @classmethod
    async def _create(
        cls,
        deadline: float | None,
        limit: int,
        on_spawn: Callable[[NativeGuardian], None] | None = None,
        inherited_slot: int = -1,
    ) -> NativeGuardian:
        if os.name != "posix":
            raise UnsupportedFeature("owned native startup requires POSIX child status")
        if deadline is not None:
            if not math.isfinite(deadline):
                raise ValueError("native ownership deadline is invalid")
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError
        retained_slot = os.dup(inherited_slot) if inherited_slot >= 0 else -1
        try:
            parent, child = socket.socketpair()
        except OSError:
            if retained_slot >= 0:
                os.close(retained_slot)
            raise
        parent.setblocking(False)
        guardian = cls(parent, deadline)
        _OWNED_GUARDIANS.add(guardian)
        if on_spawn is not None:
            try:
                on_spawn(guardian)
            except BaseException:
                parent.close()
                child.close()
                if retained_slot >= 0:
                    os.close(retained_slot)
                _OWNED_GUARDIANS.discard(guardian)
                raise

        async def create() -> asyncio.subprocess.Process:
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",
                    str(Path(__file__).with_name("_startup_guardian.py")),
                    str(child.fileno()),
                    str(retained_slot),
                    pass_fds=(child.fileno(), retained_slot)
                    if retained_slot >= 0
                    else (child.fileno(),),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={"PATH": os.defpath},
                    start_new_session=True,
                    limit=limit,
                )
                guardian._process = process
                return process
            finally:
                child.close()
                if retained_slot >= 0:
                    os.close(retained_slot)

        guardian._spawn_task = asyncio.create_task(create())
        try:
            await asyncio.wait_for(
                asyncio.shield(guardian._spawn_task), timeout=guardian._remaining()
            )
            if on_spawn is not None:
                on_spawn(guardian)
            return guardian
        except BaseException:
            # The task remains owned: late helper creation observes EOF before
            # any native begin gate, then the retained reaper collects it.
            guardian.disconnect()
            raise

    @classmethod
    async def acquire(
        cls,
        root: Path,
        slots: int,
        threshold: float | None,
        deadline: float,
    ) -> NativeGuardian:
        guardian = await cls._create(deadline, _MAX_FRAME + 1)
        try:
            await guardian._send(
                {
                    "action": "admit",
                    "root": str(root),
                    "slots": slots,
                    "threshold": threshold,
                    "deadline": deadline,
                }
            )
            receipt = await guardian._receive()
            if receipt == {"event": "queue_timeout"}:
                raise asyncio.TimeoutError
            if receipt != {"event": "admitted"}:
                raise HarnessStartupError("native admission acknowledgement is invalid")
            guardian._deadline = None
            return guardian
        except BaseException:
            guardian.disconnect()
            raise

    @classmethod
    async def spawn(
        cls,
        lease: StartupLease | None,
        argv: list[str],
        environment: Mapping[str, str],
        cwd: Path | str,
        *,
        limit: int,
        on_spawn: Callable[[NativeGuardian], None] | None = None,
        deadline: float | None = None,
    ) -> NativeGuardian:
        if lease is not None and isinstance(lease.owner, cls):
            guardian = lease.owner
            if guardian._closed or guardian.native_pid is not None:
                raise HarnessStartupError(
                    "native ownership lease has already been used"
                )
            guardian._deadline = deadline
            reused = True
        else:
            guardian = await cls._create(
                deadline,
                limit,
                on_spawn,
                inherited_slot=lease.descriptor if lease is not None else -1,
            )
            reused = False
        try:
            if reused and on_spawn is not None:
                on_spawn(guardian)
            if lease is not None and lease.owner is not guardian:
                lease.attach(guardian)
            await guardian._send(
                {
                    "argv": argv,
                    "environment": dict(environment),
                    "cwd": str(cwd),
                    "deadline": deadline,
                }
            )
            received = await guardian._receive()
            pid = received.get("pid")
            if (
                received.get("event") != "received"
                or not isinstance(pid, int)
                or isinstance(pid, bool)
                or pid <= 1
            ):
                raise HarnessStartupError("native ownership acknowledgement is invalid")
            guardian.native_pid = pid
            if lease is not None:
                lease.transferred()
            await guardian._send({"action": "begin"})
            if await guardian._receive() != {"event": "started", "pid": pid}:
                raise HarnessStartupError("native startup acknowledgement is invalid")
            return guardian
        except BaseException:
            guardian.disconnect()
            raise

    async def _send(self, value: dict[str, object]) -> None:
        data = json.dumps(value).encode("utf-8")
        if len(data) > _MAX_FRAME:
            raise HarnessStartupError("native launch exceeds ownership frame limit")
        await asyncio.wait_for(
            asyncio.get_running_loop().sock_sendall(
                self._channel, struct.pack("!I", len(data)) + data
            ),
            timeout=self._remaining(),
        )

    async def _receive(self) -> dict[str, object]:
        async def receive() -> dict[str, object]:
            async def exact(size: int) -> bytes:
                result = bytearray()
                while len(result) < size:
                    block = await asyncio.get_running_loop().sock_recv(
                        self._channel, size - len(result)
                    )
                    if not block:
                        raise HarnessStartupError("native ownership channel closed")
                    result.extend(block)
                return bytes(result)

            size = struct.unpack("!I", await exact(4))[0]
            if size > _MAX_FRAME:
                raise HarnessStartupError("native ownership frame exceeds limit")
            value = json.loads(await exact(size))
            if not isinstance(value, dict):
                raise HarnessStartupError("native ownership frame is invalid")
            return value

        return await asyncio.wait_for(receive(), timeout=self._remaining())

    def _remaining(self) -> float:
        remaining = (
            15.0 if self._deadline is None else self._deadline - time.monotonic()
        )
        if remaining <= 0:
            raise asyncio.TimeoutError
        return remaining

    async def ready(self) -> None:
        async with self._commands:
            if self._ready:
                return
            await self._send({"action": "ready"})
            if await self._receive() != {"event": "ready"}:
                raise HarnessStartupError("native readiness acknowledgement is invalid")
            self._ready = True
            self._deadline = None

    async def _reap(self) -> None:
        try:
            if self._spawn_task is not None:
                process = await asyncio.shield(self._spawn_task)
                await process.wait()
        except (OSError, ValueError):
            # A failed helper spawn created no native begin gate.
            pass
        finally:
            _OWNED_GUARDIANS.discard(self)

    def disconnect(self) -> None:
        if not self._closed:
            self._closed = True
            self._channel.close()
        if self._reap_task is None:
            self._reap_task = asyncio.create_task(self._reap())

    async def stop(self) -> None:
        self.disconnect()
        assert self._reap_task is not None
        try:
            await asyncio.wait_for(asyncio.shield(self._reap_task), timeout=2)
        except asyncio.TimeoutError:
            raise CleanupError("native group remains fenced by its guardian") from None
