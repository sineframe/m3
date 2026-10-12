"""Controller-side control channel for an admitted native process owner."""

from __future__ import annotations

import array
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
_SocketMessage = tuple[bytes, list[tuple[int, int, bytes]], int, object]
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
        self._death_descriptor: int | None = None
        self._death_confirmed = False
        self._admission_descriptor: int | None = None
        self._reader: tuple[asyncio.AbstractEventLoop, int] | None = None
        self._runtime_handles: list[int] = []
        self._writer: tuple[asyncio.AbstractEventLoop, int] | None = None

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
        try:
            death_read, death_write = os.pipe()
        except OSError:
            parent.close()
            child.close()
            if retained_slot >= 0:
                os.close(retained_slot)
            raise
        parent.setblocking(False)
        guardian = cls(parent, deadline)
        guardian._death_descriptor = death_read
        _OWNED_GUARDIANS.add(guardian)
        if on_spawn is not None:
            try:
                on_spawn(guardian)
            except BaseException:
                parent.close()
                child.close()
                if retained_slot >= 0:
                    os.close(retained_slot)
                os.close(death_read)
                os.close(death_write)
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
                    str(death_write),
                    pass_fds=(child.fileno(), death_write, retained_slot)
                    if retained_slot >= 0
                    else (child.fileno(), death_write),
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
                os.close(death_write)
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
            receipt = await guardian._receive(admission=True)
            if receipt == {"event": "queue_timeout"}:
                raise asyncio.TimeoutError
            if receipt != {"event": "admitted"}:
                raise HarnessStartupError("native admission acknowledgement is invalid")
            if guardian._admission_descriptor is None:
                raise HarnessStartupError("native admission handle is unavailable")
            await guardian._send({"action": "admission_received"})
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
        runtime_descriptors: tuple[int, ...] = (),
    ) -> NativeGuardian:
        if len(runtime_descriptors) > 32 or not all(
            type(handle) is int and handle >= 0 for handle in runtime_descriptors
        ):
            raise HarnessStartupError("native runtime custody handle is invalid")
        retained: list[int] = []
        guardian: NativeGuardian | None = None
        try:
            # Retain package custody before helper creation can yield to cleanup.
            for handle in runtime_descriptors:
                retained.append(os.dup(handle))
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
            guardian._runtime_handles.extend(retained)
            retained.clear()
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
                    "runtime_descriptor_count": len(guardian._runtime_handles),
                },
                descriptors=tuple(guardian._runtime_handles),
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
            if guardian is not None:
                guardian.disconnect()
            raise
        finally:
            for handle in retained:
                os.close(handle)

    async def _send(
        self, value: dict[str, object], *, descriptors: tuple[int, ...] = ()
    ) -> None:
        data = json.dumps(value).encode("utf-8")
        if len(data) > _MAX_FRAME:
            raise HarnessStartupError("native launch exceeds ownership frame limit")
        header = struct.pack("!I", len(data))
        loop = asyncio.get_running_loop()
        if descriptors:
            if not all(type(handle) is int and handle >= 0 for handle in descriptors):
                raise HarnessStartupError("native runtime custody handle is invalid")
            if len(descriptors) > 32:
                raise HarnessStartupError("native runtime custody exceeds handle limit")
            sent: asyncio.Future[int] = loop.create_future()

            def remove_writer() -> None:
                if self._writer is binding:
                    if self._channel.fileno() == descriptor:
                        loop.remove_writer(descriptor)
                    self._writer = None

            def transfer() -> None:
                if sent.done():
                    remove_writer()
                    return
                try:
                    count = self._channel.sendmsg(
                        [header],
                        [
                            (
                                socket.SOL_SOCKET,
                                socket.SCM_RIGHTS,
                                array.array("i", descriptors),
                            )
                        ],
                    )
                except BlockingIOError:
                    return
                except OSError as exc:
                    sent.set_exception(exc)
                else:
                    sent.set_result(count)
                remove_writer()

            descriptor = self._channel.fileno()
            binding = (loop, descriptor)
            self._writer = binding
            loop.add_writer(descriptor, transfer)
            try:
                count = await asyncio.wait_for(sent, timeout=self._remaining())
            finally:
                remove_writer()
            header = header[count:]
        await asyncio.wait_for(
            loop.sock_sendall(self._channel, header + data), timeout=self._remaining()
        )

    async def _read_block(self, size: int) -> _SocketMessage:
        loop = asyncio.get_running_loop()
        available: asyncio.Future[_SocketMessage] = loop.create_future()
        descriptor = self._channel.fileno()

        def receive_block() -> None:
            if available.done():
                loop.remove_reader(descriptor)
                return
            try:
                value = self._channel.recvmsg(
                    size, socket.CMSG_SPACE(array.array("i").itemsize)
                )
            except BlockingIOError:
                return
            except OSError as exc:
                available.set_exception(exc)
            else:
                available.set_result(value)
            loop.remove_reader(descriptor)

        binding = (loop, descriptor)
        self._reader = binding
        loop.add_reader(descriptor, receive_block)
        delivered = False
        try:
            message = await available
            delivered = True
            return message
        finally:
            if self._reader is binding:
                if self._channel.fileno() == descriptor:
                    loop.remove_reader(descriptor)
                self._reader = None
            if (
                not delivered
                and available.done()
                and not available.cancelled()
                and available.exception() is None
            ):
                for level, kind, raw in available.result()[1]:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        handles = array.array("i")
                        handles.frombytes(raw[: len(raw) - len(raw) % handles.itemsize])
                        for handle in handles:
                            os.close(handle)

    async def _receive(self, *, admission: bool = False) -> dict[str, object]:
        async def receive() -> dict[str, object]:
            async def exact(size: int) -> bytes:
                result = bytearray()
                while len(result) < size:
                    block, ancillary, flags, _ = await self._read_block(
                        size - len(result)
                    )
                    handles: list[int] = []
                    for level, kind, raw in ancillary:
                        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                            values = array.array("i")
                            values.frombytes(
                                raw[: len(raw) - len(raw) % values.itemsize]
                            )
                            handles.extend(values)
                    if flags or (
                        handles
                        and (
                            not admission
                            or self._admission_descriptor is not None
                            or len(handles) != 1
                        )
                    ):
                        for handle in handles:
                            os.close(handle)
                        raise HarnessStartupError(
                            "native admission handle receipt is invalid"
                        )
                    if handles:
                        self._admission_descriptor = handles[0]
                        os.set_inheritable(handles[0], False)
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
            self._close_admission()

    def _close_admission(self) -> None:
        if self._admission_descriptor is not None:
            os.close(self._admission_descriptor)
            self._admission_descriptor = None

    async def _reap(self) -> None:
        try:
            if self._spawn_task is not None:
                process = await asyncio.shield(self._spawn_task)
                await process.wait()
                if self._death_descriptor is not None:
                    self._death_confirmed = os.read(self._death_descriptor, 1) == b"D"
        except (OSError, ValueError):
            # A failed helper spawn created no native begin gate.
            pass
        finally:
            if self._death_descriptor is not None:
                os.close(self._death_descriptor)
                self._death_descriptor = None
            if self.native_pid is None or self._death_confirmed:
                self._close_admission()
                for handle in self._runtime_handles:
                    os.close(handle)
                self._runtime_handles.clear()
                _OWNED_GUARDIANS.discard(self)

    def disconnect(self) -> None:
        if not self._closed:
            self._closed = True
            if self._reader is not None:
                loop, descriptor = self._reader
                if self._channel.fileno() == descriptor:
                    loop.remove_reader(descriptor)
                self._reader = None
            if self._writer is not None:
                loop, descriptor = self._writer
                if self._channel.fileno() == descriptor:
                    loop.remove_writer(descriptor)
                self._writer = None
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
        if self.native_pid is not None and not self._death_confirmed:
            raise CleanupError(
                "native guardian lost custody; native resources remain quarantined"
            )
