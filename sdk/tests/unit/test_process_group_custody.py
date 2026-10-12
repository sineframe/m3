from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from m3._process_group_custody import has_unreaped_status, ownership_supported


@pytest.mark.skipif(not ownership_supported(), reason="unreaped status unavailable")
@pytest.mark.parametrize(
    "fallback",
    [
        pytest.param(
            False,
            marks=pytest.mark.skipif(
                not has_unreaped_status(), reason="waitid unavailable on this Python"
            ),
        ),
        True,
    ],
)
def test_fork_job_reserves_exited_leader_until_term_ignoring_descendant_dies(
    fallback: bool,
) -> None:
    program = textwrap.dedent(
        """
        import os, select, signal, sys, time
        from m3._preparation_worker import _ForkJob
        from m3._process_group_custody import exited_status, group_snapshot
        fallback = sys.argv[1] == 'True'
        if fallback:
            os.waitid = None

        def run():
            child = os.fork()
            if child == 0:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                os.write(1, b'ready\\n')
                while True:
                    signal.pause()
            os._exit(0)

        job = _ForkJob(run, output=True, gated=True)
        assert job.ready.read(1) == b's'
        job.ready.close()
        job.group_established = True
        job.begin()
        try:
            assert job.stdout.readline() == b'ready\\n'
            end = time.monotonic() + 5
            while not group_snapshot(job.pid)[0]:
                assert time.monotonic() < end
                select.select([], [], [], .01)
            assert job.poll() == (None if fallback else 0)
            assert job.exited
            assert not job._reaped
            assert job.alive()
            if not fallback:
                assert exited_status(job.pid) == 0
            job.signal(signal.SIGTERM)
            assert job.alive()
            job.signal(signal.SIGKILL)
            while not job._reaped:
                job.poll()
                assert time.monotonic() < end
                select.select([], [], [], .01)
            assert not job.alive()
            try:
                os.waitpid(job.pid, os.WNOHANG)
            except ChildProcessError:
                pass
            else:
                raise AssertionError('leader was not finally reaped')
            os.killpg = lambda *args: (_ for _ in ()).throw(
                AssertionError('signal after identity retirement'))
            job.signal(signal.SIGKILL)
            print('reservation-and-retirement-verified')
        finally:
            job.signal(signal.SIGKILL)
            job.stdout.close()
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(fallback)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "reservation-and-retirement-verified" in result.stdout


def test_unsupported_ownership_is_detected_before_fork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m3._preparation_worker import _ForkJob

    called = False

    def forbidden_fork() -> int:
        nonlocal called
        called = True
        raise AssertionError("fork attempted")

    monkeypatch.setattr(os, "fork", forbidden_fork)
    with monkeypatch.context() as context:
        context.setattr(os, "name", "nt")
        with pytest.raises(RuntimeError, match="unreaped child ownership"):
            _ForkJob(lambda: None, output=False)
    assert not called


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin status backend")
@pytest.mark.parametrize("failure", ["missing", "timeout", "exit", "malformed"])
def test_unavailable_inventory_rejects_jobs_before_fork(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import m3._process_group_custody as custody
    from m3._preparation_worker import _ForkJob

    def failed_status(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        if failure == "missing":
            raise FileNotFoundError("ps")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ps", 1)
        return subprocess.CompletedProcess(
            [], 1 if failure == "exit" else 0, b"bad row"
        )

    def forbidden_fork() -> int:
        raise AssertionError("fork attempted with unavailable inventory")

    custody.validate_process_status.cache_clear()
    try:
        monkeypatch.setattr(subprocess, "run", failed_status)
        monkeypatch.setattr(os, "fork", forbidden_fork)
        with pytest.raises(custody.ProcessStatusUnavailable, match="inventory"):
            _ForkJob(lambda: None, output=False)
    finally:
        custody.validate_process_status.cache_clear()


@pytest.mark.skipif(os.name != "posix", reason="POSIX job ownership")
def test_alive_cannot_skip_reaping_after_last_descendant_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from m3._preparation_worker import _ForkJob
    from m3._process_group_custody import GroupInventory, group_snapshot

    job = _ForkJob(lambda: None, output=False, gated=True)
    assert job.ready.read(1) == b"s"
    job.ready.close()
    job.group_established = True
    job.begin()
    deadline = time.monotonic() + 5
    try:
        while not group_snapshot(job.pid)[0]:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        with monkeypatch.context() as context:
            context.setattr(GroupInventory, "snapshot", lambda self, pid: (True, True))
            job.poll()
            assert job.alive()
        # alive() must retain ownership until poll() itself confirms death and
        # reaps. A separate false membership result used to leak the leader.
        assert job.alive()
        with monkeypatch.context() as context:
            # A shared inventory collected before this leader exited cannot
            # prove its group has no writers, even if no members were listed.
            context.setattr(
                GroupInventory, "snapshot", lambda self, pid: (False, False)
            )
            job.poll()
            assert job.alive()
        while job.alive():
            job.poll()
            assert time.monotonic() < deadline
            time.sleep(0.01)
        with pytest.raises(ChildProcessError):
            os.waitpid(job.pid, os.WNOHANG)
    finally:
        job.signal(signal.SIGKILL)
        while job.alive():
            job.poll()
            assert time.monotonic() < deadline
            time.sleep(0.01)


@pytest.mark.skipif(os.name != "posix", reason="POSIX job ownership")
def test_running_jobs_never_read_full_inventory_and_exited_jobs_share_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    import m3._process_group_custody as custody
    from m3._preparation_worker import _ForkJob

    # Gated leaders remain alive without any filesystem or external program.
    jobs = [_ForkJob(lambda: None, output=False, gated=True) for _ in range(8)]
    for job in jobs:
        assert job.ready.read(1) == b"s"
        job.ready.close()
        job.group_established = True
    reads = 0
    original = custody._inventory

    def inventory() -> dict[int, tuple[int, bytes]]:
        nonlocal reads
        reads += 1
        return original()

    deadline = time.monotonic() + 5
    try:
        with monkeypatch.context() as context:
            context.setattr(custody, "_inventory", inventory)
            for _ in range(5):
                snapshot = custody.GroupInventory()
                for job in jobs:
                    job.poll(snapshot)
                    assert job.alive()
            assert reads == 0
            for job in jobs:
                job.begin()
            while any(job.alive() for job in jobs):
                previous = reads
                snapshot = custody.GroupInventory()
                for job in jobs:
                    job.poll(snapshot)
                assert reads - previous <= 1
                assert time.monotonic() < deadline
                time.sleep(0.01)
    finally:
        for job in jobs:
            job.signal(signal.SIGKILL)
        for job in jobs:
            while job.alive():
                job.poll()
                assert time.monotonic() < deadline
                time.sleep(0.01)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin ps semantics")
def test_darwin_status_overrides_legacy_command_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m3._process_group_custody import _inventory

    monkeypatch.setenv("COMMAND_MODE", "legacy")
    assert os.getpid() in _inventory()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux proc backend")
def test_linux_jobs_complete_without_ps(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    import m3._process_group_custody as custody
    from m3._preparation_worker import _ForkJob

    def forbidden_ps(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("procps is not installed")

    monkeypatch.setattr(subprocess, "run", forbidden_ps)
    custody.validate_process_status.cache_clear()
    job = _ForkJob(lambda: None, output=False, gated=True)
    assert job.ready.read(1) == b"s"
    job.ready.close()
    job.group_established = True
    job.begin()
    deadline = time.monotonic() + 5
    try:
        while job.alive():
            job.poll()
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert job.returncode == 0
        with pytest.raises(ChildProcessError):
            os.waitpid(job.pid, os.WNOHANG)
    finally:
        job.signal(signal.SIGKILL)
        while job.alive():
            job.poll()
            assert time.monotonic() < deadline
            time.sleep(0.01)
        custody.validate_process_status.cache_clear()


@pytest.mark.skipif(os.name != "posix", reason="POSIX preparation supervisor")
def test_supervisor_reports_unavailable_status_without_creating_jobs() -> None:
    import json

    program = textwrap.dedent(
        """
        import m3._process_group_custody as custody
        from m3._preparation_worker import _supervise
        def unavailable():
            raise custody.ProcessStatusUnavailable('inventory is unavailable')
        custody._inventory = unavailable
        custody.validate_process_status.cache_clear()
        _supervise()
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        process.stdin.write(
            json.dumps({"version": 1, "action": "submit", "id": "job"}).encode() + b"\n"
        )
        process.stdin.flush()
        import select

        assert select.select([process.stdout], [], [], 5)[0]
        assert json.loads(process.stdout.readline()) == {
            "version": 1,
            "id": "job",
            "event": "rejected",
            "reason": "process_status_unavailable",
        }
        process.stdin.close()
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        assert process.stderr is not None
        process.stderr.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin exit notification")
def test_exit_notification_failure_reaps_child_before_its_function_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import m3._preparation_worker as worker
    import m3._process_group_custody as custody

    marker = tmp_path / "writer-started"
    custody.validate_process_status.cache_clear()
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

    monkeypatch.setattr(os, "waitid", None, raising=False)
    monkeypatch.setattr(os, "fork", fork)
    monkeypatch.setattr(worker, "ExitNotification", unavailable)

    def mark_started() -> None:
        marker.write_text("started")

    before = len(os.listdir("/dev/fd"))
    with pytest.raises(OSError, match="exit notification unavailable"):
        worker._ForkJob(mark_started, output=False)
    assert not marker.exists()
    assert child is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(child, os.WNOHANG)
    assert len(os.listdir("/dev/fd")) == before
    custody.validate_process_status.cache_clear()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux group sealing")
def test_linux_empty_inventory_cannot_publish_a_missed_live_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import time

    from m3._preparation_worker import _ForkJob
    from m3._process_group_custody import GroupInventory, group_snapshot

    marker = tmp_path / "writes"

    def write_in_descendant() -> None:
        if os.fork() == 0:
            with marker.open("ab", buffering=0) as stream:
                os.write(1, b"started\n")
                while True:
                    stream.write(b"x")
                    time.sleep(0.005)
        os._exit(0)

    job = _ForkJob(write_in_descendant, output=True, gated=True)
    assert job.ready.read(1) == b"s"
    job.ready.close()
    job.group_established = True
    job.begin()
    assert job.stdout is not None
    deadline = time.monotonic() + 5
    try:
        assert job.stdout.readline() == b"started\n"
        while not group_snapshot(job.pid)[0]:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        with monkeypatch.context() as context:
            # Simulate /proc's non-atomic enumeration missing a concurrent fork.
            context.setattr(GroupInventory, "snapshot", lambda self, pid: (True, False))
            job.poll()
            assert job.alive()
        while job.alive():
            job.poll()
            assert time.monotonic() < deadline
            time.sleep(0.01)
        writes = marker.read_bytes()
        time.sleep(0.1)
        assert marker.read_bytes() == writes
        with pytest.raises(ChildProcessError):
            os.waitpid(job.pid, os.WNOHANG)
    finally:
        job.signal(signal.SIGKILL)
        while job.alive():
            job.poll()
            assert time.monotonic() < deadline
            time.sleep(0.01)
        job.stdout.close()
