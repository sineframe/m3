"""Controller-side control channel for an admitted native process owner."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from ..errors import CleanupError, UnsupportedFeature
from .admission import StartupLease
from .contracts import HarnessStartupError

_MAX_FRAME = 1024 * 1024


class NativeGuardian:
    def __init__(
        self, process: asyncio.subprocess.Process, channel: socket.socket
    ) -> None:
        self.process = process
        self._channel = channel
        self.native_pid: int | None = None
        self._commands = asyncio.Lock()
        self._closed = False

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
    ) -> NativeGuardian:
        if os.name != "posix" or not hasattr(os, "waitid"):
            raise UnsupportedFeature("owned native startup requires POSIX child status")
        parent, child = socket.socketpair()
        parent.setblocking(False)
        guardian: NativeGuardian | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                str(Path(__file__).with_name("_startup_guardian.py")),
                str(child.fileno()),
                str(lease.descriptor if lease is not None else -1),
                pass_fds=(child.fileno(), lease.descriptor)
                if lease is not None
                else (child.fileno(),),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                env={"PATH": os.defpath},
                start_new_session=True,
                limit=limit,
            )
            child.close()
            guardian = cls(process, parent)
            if lease is not None:
                lease.attach(guardian)
            if on_spawn is not None:
                on_spawn(guardian)
            await guardian._send(
                {"argv": argv, "environment": dict(environment), "cwd": str(cwd)}
            )
            received = await guardian._receive()
            pid = received.get("pid")
            if (
                received.get("event") != "received"
                or not isinstance(pid, int)
                or pid <= 1
            ):
                raise HarnessStartupError("native ownership acknowledgement is invalid")
            guardian.native_pid = pid
            # The guardian acknowledged both the slot FD and the gated child.
            # Only now may the sender drop its copy and authorize native exec.
            if lease is not None:
                lease.close()
            await guardian._send({"action": "begin"})
            started = await guardian._receive()
            if started != {"event": "started", "pid": pid}:
                raise HarnessStartupError("native startup acknowledgement is invalid")
            return guardian
        except BaseException:
            if guardian is not None:
                guardian.disconnect()
            else:
                parent.close()
            raise
        finally:
            child.close()

    async def _send(self, value: dict[str, object]) -> None:
        data = json.dumps(value).encode("utf-8")
        if len(data) > _MAX_FRAME:
            raise HarnessStartupError("native launch exceeds ownership frame limit")
        await asyncio.wait_for(
            asyncio.get_running_loop().sock_sendall(
                self._channel, struct.pack("!I", len(data)) + data
            ),
            timeout=15,
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

        return await asyncio.wait_for(receive(), timeout=15)

    async def ready(self) -> None:
        async with self._commands:
            await self._send({"action": "ready"})
            if await self._receive() != {"event": "ready"}:
                raise HarnessStartupError("native readiness acknowledgement is invalid")

    def disconnect(self) -> None:
        if not self._closed:
            self._closed = True
            self._channel.close()

    async def stop(self) -> None:
        self.disconnect()
        try:
            await asyncio.wait_for(asyncio.shield(self.process.wait()), timeout=2)
        except asyncio.TimeoutError:
            # Killing the guardian would drop its slot while a native child
            # may still write. EOF has fenced it; ownership remains there.
            raise CleanupError("native group remains fenced by its guardian") from None
