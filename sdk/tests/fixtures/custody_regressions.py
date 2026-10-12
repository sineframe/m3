"""Run fork ownership regressions in a genuinely single-threaded process."""

from __future__ import annotations

import os
import runpy
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import m3._preparation_worker as worker
import m3._process_group_custody as custody


def wait_until(predicate: Callable[[], bool], deadline: float) -> None:
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


@contextmanager
def job(
    function: Callable[[], None], *, output: bool = False, begin: bool = True
) -> Iterator[worker._ForkJob]:
    owned = worker._ForkJob(function, output=output, gated=True)
    assert owned.ready.read(1) == b"s"
    owned.ready.close()
    owned.group_established = True
    if begin:
        owned.begin()
    deadline = time.monotonic() + 5
    try:
        yield owned
    finally:
        owned.signal(signal.SIGKILL)
        while owned.alive():
            owned.poll()
            assert time.monotonic() < deadline
            time.sleep(0.01)
        if owned.stdout is not None:
            owned.stdout.close()


def unavailable_inventory(failure: str) -> None:
    def status(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if failure == "missing":
            raise FileNotFoundError("ps")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ps", 1)
        return subprocess.CompletedProcess(
            [], 1 if failure == "exit" else 0, b"bad row"
        )

    def forbidden_fork() -> int:
        raise AssertionError("fork attempted with unavailable inventory")

    with (
        patch.object(subprocess, "run", status),
        patch.object(os, "fork", forbidden_fork),
    ):
        try:
            worker._ForkJob(lambda: None, output=False)
        except custody.ProcessStatusUnavailable:
            pass
        else:
            raise AssertionError("unavailable inventory was accepted")


def reap_race() -> None:
    with job(lambda: None) as owned:
        deadline = time.monotonic() + 5
        wait_until(lambda: custody.group_snapshot(owned.pid)[0], deadline)
        with patch.object(
            custody.GroupInventory, "snapshot", lambda self, pid: (True, True)
        ):
            owned.poll()
            assert owned.alive()
        assert owned.alive()  # alive must not make another membership query.
        with patch.object(
            custody.GroupInventory, "snapshot", lambda self, pid: (False, False)
        ):
            owned.poll()
            assert owned.alive()  # A shared pre-exit inventory is insufficient.
        while owned.alive():
            owned.poll()
            assert time.monotonic() < deadline
            time.sleep(0.01)
        try:
            os.waitpid(owned.pid, os.WNOHANG)
        except ChildProcessError:
            pass
        else:
            raise AssertionError("leader was never reaped")


def shared_inventory() -> None:
    from contextlib import ExitStack

    with ExitStack() as stack:
        jobs = [stack.enter_context(job(lambda: None, begin=False)) for _ in range(8)]
        original = custody._inventory
        reads = 0

        def inventory() -> dict[int, tuple[int, bytes]]:
            nonlocal reads
            reads += 1
            return original()

        with patch.object(custody, "_inventory", inventory):
            for _ in range(5):
                snapshot = custody.GroupInventory()
                for owned in jobs:
                    owned.poll(snapshot)
                    assert owned.alive()
            assert reads == 0
            for owned in jobs:
                owned.begin()
            deadline = time.monotonic() + 5
            while any(owned.alive() for owned in jobs):
                before = reads
                snapshot = custody.GroupInventory()
                for owned in jobs:
                    owned.poll(snapshot)
                assert reads - before <= 1
                assert time.monotonic() < deadline
                time.sleep(0.01)


def no_ps() -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("procps is not installed")

    with patch.object(subprocess, "run", unavailable):
        with job(lambda: None) as owned:
            deadline = time.monotonic() + 5
            while owned.alive():
                owned.poll()
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert owned.returncode == 0


def notification_failure(marker: Path) -> None:
    custody.validate_process_status()
    original = os.fork
    child = None

    def fork() -> int:
        nonlocal child
        pid = original()
        if pid:
            child = pid
        return pid

    def unavailable(pid: int) -> None:
        raise OSError("exit notification unavailable")

    def mutate() -> None:
        marker.write_text("started")

    before = len(os.listdir("/dev/fd"))
    with (
        patch.object(os, "waitid", None, create=True),
        patch.object(os, "fork", fork),
        patch.object(worker, "ExitNotification", unavailable),
    ):
        try:
            worker._ForkJob(mutate, output=False)
        except OSError:
            pass
        else:
            raise AssertionError("missing exit notification was accepted")
    assert not marker.exists()
    assert child is not None
    try:
        os.waitpid(child, os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise AssertionError("failed registration left a child")
    assert len(os.listdir("/dev/fd")) == before


def missed_writer(marker: Path) -> None:
    def write() -> None:
        if os.fork() == 0:
            with marker.open("ab", buffering=0) as stream:
                os.write(1, b"started\n")
                while True:
                    stream.write(b"x")
                    time.sleep(0.005)
        os._exit(0)

    with job(write, output=True) as owned:
        assert owned.stdout is not None
        assert owned.stdout.readline() == b"started\n"
        deadline = time.monotonic() + 5
        wait_until(lambda: custody.group_snapshot(owned.pid)[0], deadline)
        with patch.object(
            custody.GroupInventory, "snapshot", lambda self, pid: (True, False)
        ):
            owned.poll()
            assert owned.alive()
        while owned.alive():
            owned.poll()
            assert time.monotonic() < deadline
            time.sleep(0.01)
        writes = marker.read_bytes()
        time.sleep(0.1)
        assert marker.read_bytes() == writes


def standalone_exports() -> None:
    assert custody.__file__ is not None
    symbols = runpy.run_path(custody.__file__)
    with job(lambda: None) as owned:
        deadline = time.monotonic() + 5
        wait_until(lambda: symbols["group_snapshot"](owned.pid)[0], deadline)
        assert not symbols["members_alive"](owned.pid)


def collector_eof() -> None:
    original = subprocess.Popen

    def launch(argv, *args, **kwargs):
        return original(
            [sys.executable, "-c", "import os,time; os.close(1); time.sleep(5)"],
            *args,
            **kwargs,
        )

    collector = custody.InventoryCollector()
    deadline = time.monotonic() + 5
    try:
        with patch.object(subprocess, "Popen", launch):
            while True:
                try:
                    collector.read()
                except custody.ProcessStatusPending:
                    pass
                assert time.monotonic() < deadline
                if collector.output_descriptor is None:
                    break
                time.sleep(0.01)
        process = collector._process
        assert process is not None and process.poll() is None
        while not collector.close():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        try:
            os.waitpid(process.pid, os.WNOHANG)
        except ChildProcessError:
            pass
        else:
            raise AssertionError("status query was not reaped")
    finally:
        while not collector.close():
            assert time.monotonic() < deadline
            time.sleep(0.01)


if __name__ == "__main__":
    scenario = sys.argv[1]
    if scenario == "unavailable":
        unavailable_inventory(sys.argv[2])
    elif scenario == "reap":
        reap_race()
    elif scenario == "shared":
        shared_inventory()
    elif scenario == "no-ps":
        no_ps()
    elif scenario == "notification":
        notification_failure(Path(sys.argv[2]))
    elif scenario == "missed-writer":
        missed_writer(Path(sys.argv[2]))
    elif scenario == "exports":
        standalone_exports()
    elif scenario == "collector-eof":
        collector_eof()
    else:
        raise ValueError("unknown custody regression")
