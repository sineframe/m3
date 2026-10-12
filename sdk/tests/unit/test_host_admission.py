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
async def test_fifo_spans_real_processes_and_dead_waiter_is_removed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "host"
    first = await HostAdmission(root).acquire(
        StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=5)
    )
    assert first is not None
    workers: list[asyncio.subprocess.Process] = []
    try:
        for number in range(4):
            worker = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).parents[1] / "fixtures" / "admission_waiter.py"),
                str(root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            workers.append(worker)

            async def registered(expected: int) -> None:
                while len(tuple((root / "queue").iterdir())) < expected:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(registered(number + 1), timeout=5)
        workers[0].kill()
        await workers[0].wait()
        first.close()
        for worker in workers[1:]:
            assert worker.stdout is not None and worker.stdin is not None
            assert (
                await asyncio.wait_for(worker.stdout.readline(), timeout=5)
                == b"admitted\n"
            )
            worker.stdin.write(b"release\n")
            await worker.stdin.drain()
            assert await asyncio.wait_for(worker.wait(), timeout=5) == 0
    finally:
        first.close()
        for worker in workers:
            if worker.returncode is None:
                worker.kill()
            await worker.wait()


def test_load_gate_calculates_normalized_load_and_none_skips_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m3.harness._startup_guardian import _KernelQueue

    load = [100.0]
    monkeypatch.setattr(os, "getloadavg", lambda: (load[0], 0.0, 0.0))
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert not _KernelQueue._load_ready(0.5)
    load[0] = 0.5
    assert _KernelQueue._load_ready(0.5)
    monkeypatch.setattr(
        os, "getloadavg", lambda: pytest.fail("disabled load was queried")
    )
    assert _KernelQueue._load_ready(None)


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
@pytest.mark.parametrize("fallback", [False, True])
async def test_exited_leader_stays_reserved_until_descendant_death(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fallback: bool
) -> None:
    if fallback:
        original_spawn = asyncio.create_subprocess_exec

        async def fallback_spawn(
            *args: object, **kwargs: object
        ) -> asyncio.subprocess.Process:
            values = list(args)
            if len(values) > 2 and str(values[2]).endswith("_startup_guardian.py"):
                values[2:3] = [
                    str(
                        Path(__file__).parents[1]
                        / "fixtures"
                        / "fallback_admission_helper.py"
                    ),
                    values[2],
                ]
            return await original_spawn(*values, **kwargs)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fallback_spawn)
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


@pytest.mark.asyncio
async def test_cancelled_pending_helper_spawn_stays_owned_and_is_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = asyncio.Event()
    release_result = asyncio.Event()
    original_spawn = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []

    async def delayed_return(
        *args: object, **kwargs: object
    ) -> asyncio.subprocess.Process:
        process = await original_spawn(*args, **kwargs)
        processes.append(process)
        created.set()
        await release_result.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_return)
    root = tmp_path / "native"
    root.mkdir()
    marker = tmp_path / "must-not-execute"
    owner = ProcessOwner(root)
    pending = asyncio.create_task(
        owner.spawn(
            [sys.executable, "-c", f"open({str(marker)!r},'w').close()"],
            {},
            owned_startup=True,
        )
    )
    try:
        await asyncio.wait_for(created.wait(), timeout=3)
        assert owner._guardian is not None
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=0.5)
        assert not marker.exists()
        release_result.set()
        await owner.close()
        assert processes[0].returncode is not None
        assert not root.exists()
        assert not marker.exists()
    finally:
        release_result.set()
        await owner.terminate()


@pytest.mark.asyncio
async def test_owned_stalled_queue_filesystem_has_bounded_return_and_no_late_admit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m3.harness.guardian import _OWNED_GUARDIANS

    marker, gate = tmp_path / "blocked", tmp_path / "release"
    os.mkfifo(gate)
    processes: list[asyncio.subprocess.Process] = []
    original_spawn = asyncio.create_subprocess_exec

    async def fixture_spawn(
        *args: object, **kwargs: object
    ) -> asyncio.subprocess.Process:
        values = list(args)
        helper = str(values[2])
        values[2:3] = [
            str(Path(__file__).parents[1] / "fixtures" / "stalled_admission_helper.py"),
            helper,
            str(marker),
            str(gate),
        ]
        process = await original_spawn(*values, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fixture_spawn)
    root = tmp_path / "host"
    started = time.monotonic()
    try:
        with pytest.raises(OperationTimeout, match="queue timed out"):
            await HostAdmission(root).acquire(
                StartupAdmission(enabled=True, slots=1, queue_timeout_seconds=0.5)
            )
        assert time.monotonic() - started < 1.5
        assert marker.exists()
        assert processes[0].returncode is None
        assert any(
            owner.process_if_started is processes[0] for owner in _OWNED_GUARDIANS
        )
    finally:
        release = os.open(gate, os.O_WRONLY | os.O_NONBLOCK)
        os.write(release, b"r")
        os.close(release)
        await asyncio.wait_for(processes[0].wait(), timeout=3)
    assert not (root / "queue").exists()


@pytest.mark.asyncio
async def test_killed_guardian_cannot_authorize_native_root_cleanup(
    tmp_path: Path,
) -> None:
    from m3.errors import CleanupError
    from m3.harness.guardian import _OWNED_GUARDIANS

    root = tmp_path / "native"
    root.mkdir()
    sentinel = root / "must-remain"
    sentinel.write_text("native resources")
    owner = ProcessOwner(root)
    await owner.spawn(
        [
            sys.executable,
            "-c",
            "import pathlib,time; print('ready',flush=True); time.sleep(.5); "
            "pathlib.Path('late-write').write_text('writer completed'); "
            "print('finished',flush=True)",
        ],
        {},
        owned_startup=True,
    )
    guardian = owner._guardian
    assert guardian is not None and owner.process is not None
    assert owner.process.stdout is not None
    assert await owner.process.stdout.readline() == b"ready\n"
    guardian.process.kill()
    with pytest.raises(CleanupError, match="lost custody"):
        await owner.close()
    assert sentinel.exists()
    assert guardian in _OWNED_GUARDIANS
    assert await owner.process.stdout.readline() == b"finished\n"
    assert (root / "late-write").exists()
