from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from m3._process_group_custody import ownership_supported


@pytest.mark.skipif(not ownership_supported(), reason="unreaped status unavailable")
@pytest.mark.parametrize("fallback", [False, True])
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
