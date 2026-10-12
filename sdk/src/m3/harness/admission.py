"""Host-local FIFO admission using kernel locks, independent of native clocks."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..errors import OperationTimeout, UnsupportedFeature
from ..types import UNSET, StartupAdmission


class _NativeOwner(Protocol):
    async def ready(self) -> None: ...

    async def stop(self) -> None: ...

    def disconnect(self) -> None: ...


def validate_host_admission(settings: StartupAdmission) -> None:
    if not settings.enabled:
        return
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise UnsupportedFeature("host startup admission requires POSIX locks")
    threshold = settings.normalized_load_threshold
    if (
        threshold is not UNSET
        and threshold is not None
        and not hasattr(os, "getloadavg")
    ):
        raise UnsupportedFeature("normalized host load is unavailable")


@dataclass
class StartupLease:
    """One kernel-held startup slot, transferable to an owned native guardian.

    Closing a local descriptor does not unlock another process's inherited
    descriptor. Readiness must be reported to the guardian; failing startup
    retains that ownership until its native group has died.
    """

    descriptor: int
    _owner: _NativeOwner | None = field(default=None, repr=False, compare=False)
    _transferred: bool = field(default=False, repr=False, compare=False)

    @property
    def owner(self) -> _NativeOwner | None:
        return self._owner

    def transferred(self) -> None:
        self._transferred = True
        self.close()

    def attach(self, owner: _NativeOwner) -> None:
        if self._owner is not None:
            raise RuntimeError("startup slot already has a native owner")
        self._owner = owner

    async def ready(self) -> None:
        if self._owner is None:
            raise RuntimeError("startup slot has no native owner")
        await self._owner.ready()

    async def abort(self) -> None:
        if self._owner is not None:
            await self._owner.stop()
        self.close()

    def close(self) -> None:
        if self._owner is not None and not self._transferred:
            self._owner.disconnect()
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


class HostAdmission:
    """A private per-user host queue whose filesystem work has an owner."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    async def acquire(self, settings: StartupAdmission) -> StartupLease | None:
        if not settings.enabled:
            return None
        deadline = time.monotonic() + settings.queue_timeout_seconds
        validate_host_admission(settings)
        root = self._root or Path("/tmp") / f"m3-native-admission-{os.getuid()}"
        threshold = settings.normalized_load_threshold
        from .guardian import NativeGuardian

        try:
            owner = await NativeGuardian.acquire(
                root,
                settings.slots,
                None if threshold is UNSET else threshold,
                deadline,
            )
        except asyncio.TimeoutError:
            raise OperationTimeout("host startup admission queue timed out") from None
        return StartupLease(-1, owner)
