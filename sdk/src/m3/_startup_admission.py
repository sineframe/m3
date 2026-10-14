"""Host-wide FIFO admission for native harness startups.

Every waiter and holder owns one ticket file, ``tickets/<sequence>-<pid>``,
kept open with an exclusive ``flock`` until release.  The first ``slots``
tickets in sequence order are admitted.  The kernel drops the lock when its
process dies, so a crashed holder's ticket is recognised (it can be locked)
and removed; no pid bookkeeping is needed.  Tickets are created while the
``sequence`` file's lock is held and locked before it is released, and
reaping happens under that same lock, so a new ticket is never mistaken for
an abandoned one.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .configuration import StartupAdmissionSettings
    from .harness.contracts import HarnessStartupError

_TICKET = re.compile(r"^\d{20}-\d+$")
_POLL_SECONDS = 0.1
_REAP_INTERVAL_SECONDS = 2.0
_LOAD_POLL_SECONDS = 1.0
_QUEUE_SETTING = "startup_admission.queue_timeout_seconds"


def _admission_root() -> Path:
    from .runtime.core import _platform_cache_base

    return _platform_cache_base() / "admission"


def _startup_error(message: str) -> HarnessStartupError:
    from .harness.contracts import HarnessStartupError

    return HarnessStartupError(message)


class StartupSlot:
    """One ticket in the host queue; its lock lives as long as the descriptor."""

    def __init__(self, path: Path, descriptor: int) -> None:
        self.path = path
        self._descriptor: int | None = descriptor

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            os.close(descriptor)


def _tickets(directory: Path) -> list[str]:
    return sorted(name for name in os.listdir(directory) if _TICKET.match(name))


@contextlib.contextmanager
def _sequence_lock(root: Path) -> Iterator[int]:
    import fcntl

    descriptor = os.open(
        root / "sequence", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield descriptor
    finally:
        os.close(descriptor)


def _enqueue(root: Path) -> StartupSlot:
    import fcntl

    tickets = root / "tickets"
    with _sequence_lock(root) as sequence:
        raw = os.read(sequence, 64).decode("ascii", "replace").strip()
        try:
            number = int(raw) if raw else 0
        except ValueError:
            existing = _tickets(tickets)
            number = int(existing[-1].split("-", 1)[0]) + 1 if existing else 0
        path = tickets / f"{number:020d}-{os.getpid()}"
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        slot = StartupSlot(path, descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.lseek(sequence, 0, os.SEEK_SET)
            os.write(sequence, str(number + 1).encode("ascii"))
            os.ftruncate(sequence, os.lseek(sequence, 0, os.SEEK_CUR))
        except BaseException:
            slot.release()
            raise
    return slot


def _reap(root: Path, names: list[str]) -> None:
    """Remove tickets ahead of ours whose owners no longer hold their lock."""

    import fcntl

    tickets = root / "tickets"
    with _sequence_lock(root):
        for name in names:
            try:
                descriptor = os.open(tickets / name, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                continue
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            else:
                with contextlib.suppress(FileNotFoundError):
                    (tickets / name).unlink()
            finally:
                os.close(descriptor)


def _prepare_root(root: Path) -> None:
    for directory in (root, root / "tickets"):
        if directory.is_symlink():
            raise _startup_error("startup admission directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)


def _release_enqueued(future: asyncio.Future[StartupSlot]) -> None:
    if not future.cancelled() and future.exception() is None:
        future.result().release()


def _load_per_cpu() -> float:
    return os.getloadavg()[0] / (os.cpu_count() or 1)


async def acquire_startup_slot(
    settings: StartupAdmissionSettings, *, root: Path | None = None
) -> StartupSlot | None:
    """Wait for a host-wide startup slot; ``None`` when admission is disabled."""

    if not settings.enabled:
        return None
    if sys.platform == "win32":
        raise _startup_error(
            "startup admission is not supported on Windows (startup_admission.enabled)"
        )
    root = _admission_root() if root is None else root
    await asyncio.to_thread(_prepare_root, root)
    # Cancelling the caller cannot stop the worker thread, which may still
    # create and lock a ticket; release it whenever the thread finishes.
    enqueue = asyncio.ensure_future(asyncio.to_thread(_enqueue, root))
    try:
        slot = await asyncio.shield(enqueue)
    except BaseException:
        enqueue.add_done_callback(_release_enqueued)
        raise
    try:
        loop = asyncio.get_running_loop()
        seconds = settings.queue_timeout_seconds
        deadline = loop.time() + seconds
        next_reap = loop.time()
        mine = slot.path.name
        while True:
            names = await asyncio.to_thread(_tickets, root / "tickets")
            ahead = [name for name in names if name < mine]
            if len(ahead) < settings.slots:
                break
            if loop.time() >= next_reap:
                await asyncio.to_thread(_reap, root, ahead)
                next_reap = loop.time() + _REAP_INTERVAL_SECONDS
                continue
            if loop.time() >= deadline:
                from .harness.contracts import startup_timeout

                raise startup_timeout(
                    "m3", "startup admission", _QUEUE_SETTING, seconds
                )
            await asyncio.sleep(_POLL_SECONDS)
        limit = settings.max_load_per_cpu
        if limit is not None:
            while (load := _load_per_cpu()) > limit:
                if loop.time() >= deadline:
                    raise _startup_error(
                        f"m3 startup admission did not finish within {seconds:g}s "
                        f"({_QUEUE_SETTING}): load per CPU {load:.2f} "
                        f"stayed above startup_admission.max_load_per_cpu {limit:g}"
                    )
                await asyncio.sleep(_LOAD_POLL_SECONDS)
    except BaseException:
        slot.release()
        raise
    return slot


__all__ = ["StartupSlot", "acquire_startup_slot"]
