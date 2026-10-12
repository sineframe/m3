"""Kernel locks enforce FIFO/cancellation without process-id stale heuristics."""

import asyncio
import os
import signal
import sys
import time
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
async def test_larger_request_cannot_widen_active_smaller_host_limit(
    tmp_path: Path,
) -> None:
    admission = HostAdmission(tmp_path / "host")
    first = await admission.acquire(
        StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=2)
    )
    assert first is not None
    pending = asyncio.create_task(
        admission.acquire(
            StartupAdmission(enabled=True, slots=4, queue_timeout_seconds=2)
        )
    )
    await asyncio.sleep(0.075)
    assert not pending.done()
    first.close()
    next_lease = await pending
    assert next_lease is not None
    next_lease.close()


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


@pytest.mark.asyncio
async def test_real_controller_death_retains_slot_while_native_writer_lives(
    tmp_path: Path,
) -> None:
    root = tmp_path / "host"
    controller = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).parents[1] / "fixtures" / "admission_controller.py"),
        str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert controller.stdout is not None
    writer = int(await asyncio.wait_for(controller.stdout.readline(), timeout=5))
    controller.kill()
    await controller.wait()
    pending = asyncio.create_task(
        HostAdmission(root).acquire(
            StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=3)
        )
    )
    try:
        await asyncio.sleep(0.075)
        os.kill(writer, 0)
        assert not pending.done()
        lease = await pending
        assert lease is not None
        lease.close()
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        try:
            os.kill(writer, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.asyncio
async def test_exited_leader_stays_reserved_until_descendant_death(
    tmp_path: Path,
) -> None:
    root = tmp_path / "native"
    root.mkdir()
    lease = await HostAdmission(tmp_path / "host").acquire(
        StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=3)
    )
    assert lease is not None
    guardian = await NativeGuardian.spawn(
        lease,
        [
            sys.executable,
            "-c",
            "import os,signal,time; child=os.fork(); "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "print('descendant-ready',flush=True) if child==0 else None; "
            "time.sleep(60) if child==0 else os._exit(0)",
        ],
        {},
        root,
        limit=1024 * 1024,
    )
    assert guardian.process.stdout is not None
    assert await guardian.process.stdout.readline() == b"descendant-ready\n"
    try:
        assert guardian.native_pid is not None
        listing = await asyncio.create_subprocess_exec(
            "/bin/ps",
            "-p",
            str(guardian.native_pid),
            "-o",
            "stat=",
            stdout=asyncio.subprocess.PIPE,
        )
        status, _ = await listing.communicate()
        assert status.strip().startswith(b"Z")
        assert lease.descriptor == -1  # Only guardian owns the kernel slot.
        await asyncio.wait_for(guardian.process.wait(), timeout=3)
    finally:
        await guardian.stop()


@pytest.mark.asyncio
async def test_expired_owned_deadline_never_executes_native(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-execute"
    with pytest.raises(asyncio.TimeoutError):
        await NativeGuardian.spawn(
            None,
            [sys.executable, "-c", f"open({str(marker)!r},'w').close()"],
            {},
            tmp_path,
            limit=1024 * 1024,
            deadline=time.monotonic() - 1,
        )
    assert not marker.exists()


@pytest.mark.asyncio
async def test_guardian_autonomously_fences_at_shared_startup_deadline(
    tmp_path: Path,
) -> None:
    guardian = await NativeGuardian.spawn(
        None,
        [
            sys.executable,
            "-c",
            "import time; print('spawned',flush=True); time.sleep(60)",
        ],
        {},
        tmp_path,
        limit=1024 * 1024,
        deadline=time.monotonic() + 0.2,
    )
    assert guardian.process.stdout is not None
    assert await guardian.process.stdout.readline() == b"spawned\n"
    try:
        await asyncio.wait_for(guardian.process.wait(), timeout=3)
        assert guardian.process.returncode != 0
    finally:
        await guardian.stop()
