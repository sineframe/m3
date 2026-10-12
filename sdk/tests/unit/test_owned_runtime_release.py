from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
import time
from pathlib import Path

import pytest

from m3._startup_deadlines import StartupBudget
from m3.runtime.core import RuntimeLease
from m3.runtime.owned import OwnedRuntimeLease
from m3.types import HarnessTimeouts


class ObservedReleaseProvider:
    """Observe IPC scheduling without introducing filesystem work."""

    def __init__(self) -> None:
        self.cleanup_complete = False
        self.fenced = False
        self.releases = 0
        self.close_deadlines: list[float] = []

    def release_runtime(
        self, path: str, device: int, inode: int, *, deadline: float
    ) -> None:
        self.releases += 1

    def cancel_pending(self) -> None:
        self.fenced = True

    def close(self, *, deadline: float) -> None:
        self.close_deadlines.append(deadline)
        self.cleanup_complete = True


@pytest.mark.asyncio
async def test_saturated_executor_cannot_extend_owned_release_deadline(
    tmp_path: Path,
) -> None:
    loop = asyncio.get_running_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(executor)
    entered = asyncio.Event()
    unblock = threading.Event()

    def occupy_executor() -> None:
        loop.call_soon_threadsafe(entered.set)
        unblock.wait()

    occupant = loop.run_in_executor(None, occupy_executor)
    await entered.wait()
    marker = tmp_path / "lease"
    descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    identity = os.fstat(descriptor)
    clock = StartupBudget(HarnessTimeouts(total_seconds=1))
    clock.cleanup_deadline = time.monotonic() + 0.05
    deadline = clock.cleanup_deadline
    provider = ObservedReleaseProvider()
    owned = OwnedRuntimeLease(descriptor, marker, provider, clock)
    lease = RuntimeLease(tmp_path / "native", {}, _owned=owned)
    release = asyncio.create_task(lease.release())
    try:
        done, _ = await asyncio.wait((release,), timeout=0.3)
        assert done, (
            "release waited for executor availability past its cleanup deadline"
        )
        with pytest.raises(asyncio.TimeoutError):
            await release
        assert provider.fenced
        assert provider.releases == 0
        assert not owned._marker_released
        assert owned._release_identity == (identity.st_dev, identity.st_ino)
        assert owned.descriptor == -1
        assert owned._close_task is not None and not owned._close_task.done()
    finally:
        unblock.set()
        await occupant
        await asyncio.gather(release, return_exceptions=True)
        if owned._close_task is not None:
            await asyncio.wait_for(asyncio.shield(owned._close_task), timeout=1)
        executor.shutdown(wait=True)
    assert provider.cleanup_complete
    assert provider.close_deadlines == [deadline]
    assert clock.cleanup_deadline == deadline


@pytest.mark.asyncio
async def test_release_retry_keeps_original_deadline_and_marker_identity(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "lease"
    descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    identity = os.fstat(descriptor)
    clock = StartupBudget(HarnessTimeouts(total_seconds=1))
    deadline = time.monotonic() - 1
    clock.cleanup_deadline = deadline
    provider = ObservedReleaseProvider()
    owned = OwnedRuntimeLease(descriptor, marker, provider, clock)
    lease = RuntimeLease(tmp_path / "native", {}, _owned=owned)
    for _ in range(2):
        with pytest.raises(asyncio.TimeoutError):
            await lease.release()
        assert owned._close_task is not None
        await asyncio.wait_for(asyncio.shield(owned._close_task), timeout=1)
        assert clock.cleanup_deadline == deadline
        assert owned._release_identity == (identity.st_dev, identity.st_ino)
        assert not owned._marker_released
        assert owned.descriptor == -1
    assert provider.releases == 0
    assert provider.close_deadlines == [deadline, deadline]
    assert provider.fenced
