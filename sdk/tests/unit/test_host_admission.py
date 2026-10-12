"""Kernel locks enforce FIFO/cancellation without process-id stale heuristics."""

import asyncio
import sys
from pathlib import Path

import pytest

from m3.errors import OperationTimeout
from m3.harness.admission import HostAdmission
from m3.harness.guardian import NativeGuardian
from m3.harness.native import ProcessOwner
from m3.types import StartupAdmission


@pytest.mark.asyncio
async def test_disabled_admission_creates_no_files(tmp_path: Path) -> None:
    root = tmp_path / "must-not-exist"
    assert await HostAdmission(root).acquire(StartupAdmission()) is None
    assert not root.exists()


@pytest.mark.asyncio
async def test_fifo_waiters_cannot_skip_busy_head(tmp_path: Path) -> None:
    admission = HostAdmission(tmp_path / "host")
    settings = StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=2)
    first = await admission.acquire(settings)
    assert first is not None
    entered: list[int] = []

    async def waiter(number: int) -> None:
        lease = await admission.acquire(settings)
        assert lease is not None
        entered.append(number)
        await asyncio.sleep(0.02)
        lease.close()

    tasks = []
    for number in range(4):
        tasks.append(asyncio.create_task(waiter(number)))
        await asyncio.sleep(0.03)
    assert not entered
    first.close()
    await asyncio.gather(*tasks)
    assert entered == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_cancelled_head_and_queue_timeout_release_ticket(tmp_path: Path) -> None:
    admission = HostAdmission(tmp_path / "host")
    settings = StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=2)
    first = await admission.acquire(settings)
    assert first is not None
    head = asyncio.create_task(admission.acquire(settings))
    await asyncio.sleep(0.05)
    head.cancel()
    with pytest.raises(asyncio.CancelledError):
        await head
    with pytest.raises(OperationTimeout, match="queue timed out"):
        await admission.acquire(
            StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=0.05)
        )
    first.close()
    last = await admission.acquire(settings)
    assert last is not None
    last.close()


@pytest.mark.asyncio
async def test_load_gate_keeps_capacity_idle_until_load_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    load = [100.0]
    monkeypatch.setattr(os, "getloadavg", lambda: (load[0], 0.0, 0.0))
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    settings = StartupAdmission(
        enabled=True,
        slots=1,
        normalized_load_threshold=0.5,
        queue_timeout_seconds=2,
    )
    pending = asyncio.create_task(HostAdmission(tmp_path / "host").acquire(settings))
    await asyncio.sleep(0.05)
    assert not pending.done()
    load[0] = 0.5
    lease = await pending
    assert lease is not None
    lease.close()


@pytest.mark.asyncio
async def test_native_ready_releases_slot_but_keeps_group_owned(tmp_path: Path) -> None:
    admission = HostAdmission(tmp_path / "host")
    settings = StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=2)
    lease = await admission.acquire(settings)
    assert lease is not None
    guardian = await NativeGuardian.spawn(
        lease,
        [
            sys.executable,
            "-c",
            "import time; print('native-ready',flush=True); time.sleep(60)",
        ],
        {},
        tmp_path,
        limit=1024 * 1024,
    )
    assert guardian.process.stdout is not None
    assert await guardian.process.stdout.readline() == b"native-ready\n"
    waiting = asyncio.create_task(admission.acquire(settings))
    try:
        await asyncio.sleep(0.05)
        assert not waiting.done()
        await lease.ready()
        next_lease = await waiting
        assert next_lease is not None
        assert guardian.process.returncode is None
        next_lease.close()
    finally:
        await guardian.stop()


@pytest.mark.asyncio
async def test_control_eof_keeps_slot_until_ignored_term_group_dies(
    tmp_path: Path,
) -> None:
    admission = HostAdmission(tmp_path / "host")
    settings = StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=2)
    lease = await admission.acquire(settings)
    assert lease is not None
    guardian = await NativeGuardian.spawn(
        lease,
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "print('ignoring-term',flush=True); time.sleep(60)",
        ],
        {},
        tmp_path,
        limit=1024 * 1024,
    )
    assert guardian.process.stdout is not None
    assert await guardian.process.stdout.readline() == b"ignoring-term\n"
    guardian.disconnect()
    waiting = asyncio.create_task(admission.acquire(settings))
    await asyncio.sleep(0.075)
    assert not waiting.done()
    next_lease = await waiting
    assert next_lease is not None
    await guardian.process.wait()
    next_lease.close()


@pytest.mark.asyncio
async def test_owned_startup_without_admission_uses_same_guardian(
    tmp_path: Path,
) -> None:
    root = tmp_path / "native"
    root.mkdir()
    owner = ProcessOwner(root)
    await owner.spawn(
        [
            sys.executable,
            "-c",
            "import time; print('owned',flush=True); time.sleep(60)",
        ],
        {},
        owned_startup=True,
    )
    assert owner.process is not None and owner.process.stdout is not None
    assert await owner.process.stdout.readline() == b"owned\n"
    assert owner._guardian is not None
    await owner.close()
    assert not root.exists()


@pytest.mark.asyncio
async def test_default_process_spawn_has_no_guardian(tmp_path: Path) -> None:
    root = tmp_path / "native"
    root.mkdir()
    owner = ProcessOwner(root)
    await owner.spawn([sys.executable, "-c", "print('direct',flush=True)"], {})
    assert owner._guardian is None
    assert owner.process is not None and owner.process.stdout is not None
    assert await owner.process.stdout.readline() == b"direct\n"
    await owner.close()
