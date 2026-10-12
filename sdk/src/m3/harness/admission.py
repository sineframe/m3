"""Host-local FIFO admission using kernel locks, independent of native clocks."""

from __future__ import annotations

import asyncio
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..errors import OperationTimeout, UnsupportedFeature
from ..types import UNSET, StartupAdmission

_POLL_SECONDS = 0.025


class _NativeOwner(Protocol):
    async def ready(self) -> None: ...

    async def stop(self) -> None: ...


def validate_host_admission(settings: StartupAdmission) -> None:
    if not settings.enabled:
        return
    if (
        os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "waitid")
        or not hasattr(os, "WNOWAIT")
    ):
        raise UnsupportedFeature("host startup admission requires POSIX locks")
    threshold = settings.normalized_load_threshold
    if (
        threshold is not UNSET
        and threshold is not None
        and not hasattr(os, "getloadavg")
    ):
        raise UnsupportedFeature("normalized host load is unavailable")


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
        raise UnsupportedFeature("startup admission storage is not private")
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
        raise UnsupportedFeature("startup admission storage is not private")


@dataclass
class StartupLease:
    """One kernel-held startup slot, transferable to an owned native guardian.

    Closing a local descriptor does not unlock another process's inherited
    descriptor. Readiness must be reported to the guardian; failing startup
    retains that ownership until its native group has died.
    """

    descriptor: int
    _owner: _NativeOwner | None = field(default=None, repr=False, compare=False)

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
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


class HostAdmission:
    """A private per-user host queue shared by independent SDK processes."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    async def acquire(self, settings: StartupAdmission) -> StartupLease | None:
        if not settings.enabled:
            return None
        validate_host_admission(settings)
        threshold = settings.normalized_load_threshold
        root = self._root or Path("/tmp") / (f"m3-native-admission-{os.getuid()}")
        _directory(root)
        tickets = root / "queue"
        slots = root / "slots"
        _directory(tickets)
        _directory(slots)
        guard = _open(root / "guard")
        ticket = -1
        ticket_path: Path | None = None
        deadline = time.monotonic() + settings.queue_timeout_seconds
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise OperationTimeout("host startup admission queue timed out")
                if _lock(guard):
                    try:
                        if ticket_path is None:
                            ticket_path, ticket = self._enqueue(guard, tickets)
                        first = self._first_waiter(tickets)
                        if first == ticket_path and self._load_ready(threshold):
                            lease = self._claim_slot(slots, settings.slots)
                            if lease is not None:
                                ticket_path.unlink()
                                return lease
                    finally:
                        import fcntl

                        fcntl.flock(guard, fcntl.LOCK_UN)
                await asyncio.sleep(min(_POLL_SECONDS, deadline - time.monotonic()))
        finally:
            # Queue entries are live only while their kernel lock is held.
            # Cancellation/death cannot obstruct subsequent waiters.
            if ticket >= 0:
                os.close(ticket)
            os.close(guard)

    @staticmethod
    def _enqueue(guard: int, tickets: Path) -> tuple[Path, int]:
        os.lseek(guard, 0, os.SEEK_SET)
        value = os.read(guard, 32)
        try:
            sequence = int(value or b"0") + 1
        except ValueError:
            raise UnsupportedFeature("startup admission sequence is invalid") from None
        if not 0 < sequence < 10**20:
            raise UnsupportedFeature("startup admission sequence is exhausted")
        os.lseek(guard, 0, os.SEEK_SET)
        os.write(guard, str(sequence).encode("ascii"))
        os.ftruncate(guard, len(str(sequence)))
        path = tickets / f"{sequence:020d}"
        descriptor = _open(path)
        if not _lock(descriptor):
            os.close(descriptor)
            raise UnsupportedFeature("startup admission sequence collided")
        return path, descriptor

    @staticmethod
    def _first_waiter(tickets: Path) -> Path | None:
        for path in sorted(tickets.iterdir()):
            if (
                len(path.name) != 20
                or not path.name.isascii()
                or not path.name.isdigit()
            ):
                raise UnsupportedFeature("startup admission queue is invalid")
            descriptor = _open(path)
            try:
                if not _lock(descriptor):
                    return path
                path.unlink()
            finally:
                os.close(descriptor)
        return None

    @staticmethod
    def _claim_slot(slots: Path, limit: int) -> StartupLease | None:
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
                        raise UnsupportedFeature(
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
                    raise UnsupportedFeature("startup admission slot collided")
            encoded = str(limit).encode("ascii")
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, encoded)
                os.ftruncate(descriptor, len(encoded))
            except BaseException:
                os.close(descriptor)
                raise
            return StartupLease(descriptor)
        finally:
            for descriptor in available:
                os.close(descriptor)

    @staticmethod
    def _load_ready(threshold: object) -> bool:
        if threshold is UNSET or threshold is None:
            return True
        assert isinstance(threshold, float)
        try:
            return os.getloadavg()[0] / max(1, os.cpu_count() or 1) <= threshold
        except OSError:
            raise UnsupportedFeature("normalized host load is unavailable") from None
