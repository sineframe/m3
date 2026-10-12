"""The contention driver's failure cleanup must cover every launched CLI."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_cleanup_escalates_all_stalled_cli_processes_before_raising() -> None:
    path = Path(__file__).resolve().parents[3] / "scripts/repro_large_run_storage.py"
    spec = importlib.util.spec_from_file_location("large_run_storage_repro", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    calls = []

    class StalledCLI:
        def __init__(self, name: str) -> None:
            self.name = name
            self.killed = False

        def poll(self) -> int | None:
            return -9 if self.killed else None

        def terminate(self) -> None:
            calls.append((self.name, "terminate"))

        def kill(self) -> None:
            calls.append((self.name, "kill"))
            self.killed = True

        def wait(self, *, timeout: float) -> int:
            if not self.killed:
                raise subprocess.TimeoutExpired(self.name, timeout)
            calls.append((self.name, "reaped"))
            return -9

    with pytest.raises(RuntimeError, match="termination cleanup"):
        module.stop([StalledCLI("first"), StalledCLI("second")])
    assert calls == [
        ("first", "terminate"),
        ("second", "terminate"),
        ("first", "kill"),
        ("second", "kill"),
        ("first", "reaped"),
        ("second", "reaped"),
    ]


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
@pytest.mark.timeout(45)
@pytest.mark.parametrize("leader_state", ["suspended", "exited"])
def test_real_four_workers_die_when_cli_stalls_or_exits_at_unfinished_session(
    tmp_path: Path,
    leader_state: str,
) -> None:
    import json
    import signal
    import sys
    import time

    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "owned_storage_repro", root / "scripts/repro_large_run_storage.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    owner = module.CheckpointOwner()
    base = owner.register(0, tmp_path / "bootstrap", victim=True)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        str(root / package / "src") for package in ("sdk", "cli", "app")
    )
    database = tmp_path / "results.sqlite"
    with (tmp_path / "cli.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "m3_cli",
                "test",
                "--python",
                sys.executable,
                "--project-root",
                str(root / "scripts/fixtures/large_run_storage"),
                "--results-db",
                str(database),
                "-n",
                "4",
                "--",
                "test_writers.py",
                "-q",
                "--basetemp",
                str(base),
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        owner.bind(0, process)
        try:
            deadline = time.monotonic() + 20
            while owner.invocations[0].checkpoint is None:
                assert process.poll() is None, (tmp_path / "cli.log").read_text(
                    errors="replace"
                )
                assert time.monotonic() < deadline, (tmp_path / "cli.log").read_text(
                    errors="replace"
                )
                owner.pump(min(0.1, deadline - time.monotonic()))
            assert len(owner.live(0, role=module.PeerRole.WORKER)) == 4
            point = module.unfinished_session(database, owner.invocations[0].checkpoint)
            assert point.completed_test_results == 0
            assert len(point.checkpoint.turn_ids) == 2
            if leader_state == "suspended":
                os.kill(process.pid, signal.SIGSTOP)
                with pytest.raises(
                    RuntimeError, match="CLI did not complete termination cleanup"
                ):
                    module.stop([process], owner, grace_seconds=0.2)
            else:
                process.kill()
                process.wait(timeout=5)
                assert len(owner.live(0, role=module.PeerRole.WORKER)) == 4
                module.stop([process], owner, grace_seconds=0.2)
            assert process.poll() is not None
            assert not owner.live(0)
            assert owner.groups_dead([0])
            lifecycle = owner.invocations[0].lifecycle
            assert lifecycle == module.CleanupEvidence(
                registered_workers=4,
                peer_eof=True,
                group_dead=True,
                cli_reaped=True,
            )
            (tmp_path / "lifecycle.json").write_text(
                json.dumps(
                    {
                        "checkpoint": point.report(),
                        "lifecycle": module.asdict(lifecycle),
                        "cli_exit": process.returncode,
                    }
                )
            )
        finally:
            # Test failure cleanup uses only still-live validated leases and
            # reaps our own CLI; it never discovers or reaps grandchildren.
            owner.pump(0)
            owner.signal_groups([0], signal.SIGKILL)
            if process.poll() is None:
                process.send_signal(signal.SIGCONT)
                process.kill()
            process.wait(timeout=5)
            owner.close()
