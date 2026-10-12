from __future__ import annotations

import os
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
    failure: str,
) -> None:
    _custody_regression("unavailable", failure)


@pytest.mark.skipif(os.name != "posix", reason="POSIX job ownership")
def test_alive_cannot_skip_reaping_after_last_descendant_exits() -> None:
    _custody_regression("reap")


@pytest.mark.skipif(os.name != "posix", reason="POSIX job ownership")
def test_running_jobs_never_read_full_inventory_and_exited_jobs_share_one() -> None:
    _custody_regression("shared")


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin ps semantics")
def test_darwin_status_overrides_legacy_command_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m3._process_group_custody import _inventory

    monkeypatch.setenv("COMMAND_MODE", "legacy")
    assert os.getpid() in _inventory()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux proc backend")
def test_linux_jobs_complete_without_ps() -> None:
    _custody_regression("no-ps")


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
    tmp_path: Path,
) -> None:
    _custody_regression("notification", str(tmp_path / "writer-started"))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux group sealing")
def test_linux_empty_inventory_cannot_publish_a_missed_live_writer(
    tmp_path: Path,
) -> None:
    _custody_regression("missed-writer", str(tmp_path / "writes"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX custody module")
def test_standalone_guardian_can_load_custody_callables() -> None:
    _custody_regression("exports")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux proc inventory")
@pytest.mark.parametrize("error", [FileNotFoundError, ProcessLookupError])
def test_disappearing_proc_entry_does_not_reject_the_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: type[OSError]
) -> None:
    import m3._process_group_custody as custody

    live = tmp_path / "101"
    live.mkdir()
    (live / "stat").write_bytes(b"101 (process name) S 1 101 101")
    vanished = tmp_path / "102"
    vanished.mkdir()
    original_iterdir = Path.iterdir
    original_read = Path.read_bytes

    def entries(path: Path):
        return (
            iter((live, vanished)) if str(path) == "/proc" else original_iterdir(path)
        )

    def read(path: Path) -> bytes:
        if path == vanished / "stat":
            raise error("process disappeared")
        return original_read(path)

    monkeypatch.setattr(Path, "iterdir", entries)
    monkeypatch.setattr(Path, "read_bytes", read)
    assert custody._inventory() == {101: (101, b"S")}


def _custody_regression(scenario: str, *arguments: str) -> None:
    driver = Path(__file__).parents[1] / "fixtures" / "custody_regressions.py"
    result = subprocess.run(
        [sys.executable, str(driver), scenario, *arguments],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_unavailable_inventory_preserves_guardian_ownership_and_caches_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import m3._process_group_custody as custody

    attempts = 0

    def unavailable() -> dict[int, tuple[int, bytes]]:
        nonlocal attempts
        attempts += 1
        raise custody.ProcessStatusUnavailable("inventory unavailable")

    monkeypatch.setattr(custody, "_inventory", unavailable)
    assert custody.group_snapshot(123) == (False, True)
    assert custody.members_alive(123)
    shared = custody.GroupInventory()
    before = attempts
    for pid in range(64):
        with pytest.raises(custody.ProcessStatusUnavailable):
            shared.snapshot(pid)
    assert attempts - before == 1
