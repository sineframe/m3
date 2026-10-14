"""Host-wide startup admission: slot cap, FIFO order, crash recovery, limits."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from m3 import _startup_admission as admission
from m3._startup_admission import acquire_startup_slot
from m3.configuration import StartupAdmissionSettings
from m3.harness.contracts import HarnessStartupError

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="startup admission is POSIX only"
)

SDK_SRC = Path(__file__).parents[2] / "src"

_HOLDER = """
import asyncio, json, sys, time
from pathlib import Path
from m3._startup_admission import acquire_startup_slot
from m3.configuration import StartupAdmissionSettings

async def main() -> None:
    root, log, slots, hold = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    settings = StartupAdmissionSettings(enabled=True, slots=slots, queue_timeout_seconds=30)
    slot = await acquire_startup_slot(settings, root=root)
    start = time.time()
    await asyncio.sleep(hold)
    end = time.time()
    slot.release()
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps([start, end]) + "\\n")

asyncio.run(main())
"""

_CRASHER = """
import asyncio, os, signal, sys
from pathlib import Path
from m3._startup_admission import acquire_startup_slot
from m3.configuration import StartupAdmissionSettings

async def main() -> None:
    settings = StartupAdmissionSettings(enabled=True, slots=1)
    await acquire_startup_slot(settings, root=Path(sys.argv[1]))
    Path(sys.argv[2]).write_text("held")
    os.kill(os.getpid(), signal.SIGKILL)

asyncio.run(main())
"""


def _settings(**values: object) -> StartupAdmissionSettings:
    return StartupAdmissionSettings(enabled=True, **values)  # type: ignore[arg-type]


def _tickets(root: Path) -> list[str]:
    return sorted(path.name for path in (root / "tickets").iterdir())


def _child_env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(SDK_SRC)}


async def _wait_for_tickets(root: Path, count: int) -> None:
    while len(_tickets(root)) < count:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_disabled_admission_touches_nothing(tmp_path: Path) -> None:
    root = tmp_path / "admission"
    assert await acquire_startup_slot(StartupAdmissionSettings(), root=root) is None
    assert not root.exists()


@pytest.mark.process_lifecycle
def test_processes_never_exceed_the_slot_count(tmp_path: Path) -> None:
    log = tmp_path / "holds.jsonl"
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _HOLDER, str(tmp_path / "q"), str(log), "2", "0.4"],
            env=_child_env(),
        )
        for _ in range(6)
    ]
    try:
        assert [child.wait(timeout=30) for child in children] == [0] * 6
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
    holds = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(holds) == 6
    events = sorted(
        [(start, 1) for start, _ in holds] + [(end, -1) for _, end in holds]
    )
    active = peak = 0
    for _, change in events:
        active += change
        peak = max(peak, active)
    assert peak == 2
    assert _tickets(tmp_path / "q") == []


@pytest.mark.asyncio
async def test_waiters_are_admitted_in_arrival_order(tmp_path: Path) -> None:
    settings = _settings(slots=1)
    holder = await acquire_startup_slot(settings, root=tmp_path)
    assert holder is not None
    order: list[int] = []

    async def wait(index: int) -> None:
        slot = await acquire_startup_slot(settings, root=tmp_path)
        assert slot is not None
        order.append(index)
        await asyncio.sleep(0.05)
        slot.release()

    waiters = []
    for index in range(3):
        waiters.append(asyncio.create_task(wait(index)))
        await _wait_for_tickets(tmp_path, index + 2)
    holder.release()
    await asyncio.wait_for(asyncio.gather(*waiters), 5)
    assert order == [0, 1, 2]


@pytest.mark.process_lifecycle
def test_a_killed_holder_frees_its_slot(tmp_path: Path) -> None:
    marker = tmp_path / "held"
    root = tmp_path / "q"
    child = subprocess.run(
        [sys.executable, "-c", _CRASHER, str(root), str(marker)],
        env=_child_env(),
        timeout=30,
        check=False,
    )
    assert child.returncode == -9
    assert marker.read_text() == "held"
    assert len(_tickets(root)) == 1

    started = time.monotonic()
    slot = asyncio.run(acquire_startup_slot(_settings(slots=1), root=root))
    assert slot is not None
    assert time.monotonic() - started < 3
    assert _tickets(root) == [slot.path.name]
    slot.release()


@pytest.mark.asyncio
async def test_queue_timeout_names_its_setting_and_leaves_no_ticket(
    tmp_path: Path,
) -> None:
    holder = await acquire_startup_slot(_settings(slots=1), root=tmp_path)
    assert holder is not None
    with pytest.raises(HarnessStartupError) as caught:
        await acquire_startup_slot(
            _settings(slots=1, queue_timeout_seconds=0.3), root=tmp_path
        )
    assert str(caught.value) == (
        "m3 startup admission did not finish within 0.3s "
        "(startup_admission.queue_timeout_seconds)"
    )
    assert _tickets(tmp_path) == [holder.path.name]
    holder.release()


@pytest.mark.asyncio
async def test_cancelled_waiter_leaves_no_ticket(tmp_path: Path) -> None:
    holder = await acquire_startup_slot(_settings(slots=1), root=tmp_path)
    assert holder is not None
    waiter = asyncio.create_task(
        acquire_startup_slot(_settings(slots=1), root=tmp_path)
    )
    await _wait_for_tickets(tmp_path, 2)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert _tickets(tmp_path) == [holder.path.name]
    holder.release()


@pytest.mark.asyncio
async def test_cancel_during_enqueue_releases_the_ticket_it_creates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, proceed, created = threading.Event(), threading.Event(), threading.Event()
    enqueue = admission._enqueue

    def stalled_enqueue(root: Path) -> admission.StartupSlot:
        entered.set()
        proceed.wait(5)
        try:
            return enqueue(root)
        finally:
            created.set()

    monkeypatch.setattr(admission, "_enqueue", stalled_enqueue)
    waiter = asyncio.create_task(
        acquire_startup_slot(_settings(slots=1), root=tmp_path)
    )
    await asyncio.to_thread(entered.wait, 5)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    proceed.set()
    # The cancelled call's ticket now exists (or would, without the fix) and
    # is ahead of the next one.
    await asyncio.to_thread(created.wait, 5)
    monkeypatch.setattr(admission, "_enqueue", enqueue)
    # A leaked, still-locked ticket would sit ahead of this one forever.
    slot = await acquire_startup_slot(
        _settings(slots=1, queue_timeout_seconds=2), root=tmp_path
    )
    assert slot is not None
    assert _tickets(tmp_path) == [slot.path.name]
    slot.release()


@pytest.mark.asyncio
async def test_load_gate_waits_until_load_falls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readings = iter([8.0, 8.0, 0.5])
    monkeypatch.setattr(admission, "_load_per_cpu", lambda: next(readings))
    monkeypatch.setattr(admission, "_LOAD_POLL_SECONDS", 0.01)
    slot = await acquire_startup_slot(
        _settings(slots=1, max_load_per_cpu=1.0), root=tmp_path
    )
    assert slot is not None
    with pytest.raises(StopIteration):
        next(readings)
    slot.release()


@pytest.mark.asyncio
async def test_load_gate_times_out_naming_the_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(admission, "_load_per_cpu", lambda: 8.0)
    monkeypatch.setattr(admission, "_LOAD_POLL_SECONDS", 0.01)
    with pytest.raises(HarnessStartupError) as caught:
        await acquire_startup_slot(
            _settings(slots=1, max_load_per_cpu=1.5, queue_timeout_seconds=0.2),
            root=tmp_path,
        )
    assert str(caught.value) == (
        "m3 startup admission did not finish within 0.2s "
        "(startup_admission.queue_timeout_seconds): load per CPU 8.00 "
        "stayed above startup_admission.max_load_per_cpu 1.5"
    )
    assert _tickets(tmp_path) == []


@pytest.mark.asyncio
async def test_windows_is_rejected_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(HarnessStartupError, match="not supported on Windows"):
        await acquire_startup_slot(_settings(), root=tmp_path)
