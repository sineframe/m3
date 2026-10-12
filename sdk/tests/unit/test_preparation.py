from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from m3.errors import UnsupportedFeature
from m3.preparation import PreparationSupervisor
from m3.types import WorkspaceKind, WorkspacePolicy
from m3.workspace import WorkspaceManager

pytestmark = [
    pytest.mark.process_lifecycle,
    pytest.mark.skipif(os.name != "posix", reason="POSIX process-group ownership"),
]


async def _until(predicate: Callable[[], bool], timeout: float = 5) -> None:
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError("owned process did not reach expected state")
        await asyncio.sleep(0.01)


def _exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_workspace_creation_reuses_helper_and_private_roots(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.txt").write_text("initial")
    (source / ".env").write_text("excluded")

    async def run() -> None:
        supervisor = PreparationSupervisor()
        try:
            first = WorkspaceManager(
                WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)), "one"
            )
            root = await first.create_async(supervisor)
            helper = supervisor.helper_pid
            assert (root / "sample.txt").read_text() == "initial"
            assert not (root / ".env").exists()
            assert first.root == root
            second = WorkspaceManager(WorkspacePolicy(), "two")
            other = await second.create_async(supervisor)
            assert supervisor.helper_pid == helper
            assert other != root
            staging = root.parent
            first.cleanup()
            second.cleanup()
            assert not staging.exists()
            assert source.exists()
        finally:
            await supervisor.close()

    asyncio.run(run())


def test_fenced_generation_cannot_publish_late_result(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    script = f"""
import pathlib
import signal
import time

signal.signal(signal.SIGTERM, lambda *_: None)
pathlib.Path({str(ready)!r}).write_text("ready")
pathlib.Path("partial").write_text("first chunk")
time.sleep(0.15)
pathlib.Path("late").write_text("late chunk")
"""

    async def run() -> None:
        supervisor = PreparationSupervisor(stop_seconds=0)
        task = asyncio.create_task(
            supervisor.prepare("command", {"argv": [sys.executable, "-c", script]})
        )
        await _until(ready.exists)
        roots = tuple(job.root for job in supervisor._jobs.values())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert supervisor.quarantined_roots == roots
        # A real writer is still alive after bounded return, with its private
        # partial copy intact; no manager/published path has been handed out.
        assert roots[0].exists()
        await _until(lambda: (roots[0] / "payload" / "late").exists())
        assert roots[0] in supervisor.quarantined_roots
        await _until(lambda: not roots[0].exists())
        # The cancelled result cannot accept or overwrite a later generation.
        material = await supervisor.prepare(
            "command", {"argv": [sys.executable, "-c", "pass"]}
        )
        assert material.staging_root != roots[0]
        import shutil

        shutil.rmtree(material.staging_root)
        await supervisor.close()

    asyncio.run(run())


def test_stalled_copy_returns_bounded_and_deletes_only_after_writer_death(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "ready"
    script = f"""
import os
import pathlib
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path({str(ready)!r}).write_text(str(os.getpid()))
pathlib.Path("copy").write_text("partial")
time.sleep(60)
"""

    async def run() -> None:
        supervisor = PreparationSupervisor(stop_seconds=0.02)
        task = asyncio.create_task(
            supervisor.prepare("command", {"argv": [sys.executable, "-c", script]})
        )
        await _until(ready.exists)
        writer = int(ready.read_text())
        root = next(iter(supervisor._jobs.values())).root
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started < 0.3
        assert _exists(writer)
        assert root.exists()
        assert root in supervisor.quarantined_roots
        await _until(lambda: not root.exists())
        assert not _exists(writer)
        await supervisor.close()

    asyncio.run(run())


def test_absolute_deadline_includes_bootstrap_and_does_not_publish(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        supervisor = PreparationSupervisor(stop_seconds=0.1)
        manager = WorkspaceManager(WorkspacePolicy(), "deadline")
        with pytest.raises(asyncio.TimeoutError):
            await manager.create_async(supervisor, deadline=time.monotonic() - 1)
        assert supervisor.helper_pid is None
        with pytest.raises(Exception, match="not been created"):
            _ = manager.root
        await supervisor.close()

    asyncio.run(run())


def test_controller_eof_kills_ignored_term_and_descendants(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    ready = tmp_path / "pids"
    descendant_script = """
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(60)
"""
    script = f"""
import os
import pathlib
import signal
import subprocess
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, "-c", {descendant_script!r}])
pathlib.Path({str(ready)!r}).write_text(str(os.getpid()) + " " + str(child.pid))
time.sleep(60)
"""
    helper, ownership = _owned_helper()
    assert helper.stdin is not None and helper.stdout is not None
    helper.stdin.write(
        json.dumps(
            {
                "action": "submit",
                "id": "eof",
                "root": str(root),
                "operation": "command",
                "payload": {"argv": [sys.executable, "-c", script]},
            }
        ).encode()
        + b"\n"
    )
    helper.stdin.flush()
    started = json.loads(helper.stdout.readline())
    assert started["event"] == "started"
    _acknowledge(ownership, "eof")
    end = time.monotonic() + 5
    while not ready.exists():
        assert time.monotonic() < end
        time.sleep(0.01)
    pids = [int(item) for item in ready.read_text().split()]
    helper.stdin.close()
    try:
        _finish_helper(helper, ownership)
        assert all(not _exists(pid) for pid in pids)
        assert not root.exists()
    finally:
        if helper.poll() is None:
            os.killpg(started["pid"], signal.SIGKILL)
            helper.kill()
            helper.wait()
        if not helper.stdout.closed:
            helper.stdout.close()


def test_descendant_group_must_die_before_material_can_be_accepted(
    tmp_path: Path,
) -> None:
    child_pid = tmp_path / "child"
    descendant_script = """
import time

time.sleep(60)
"""
    script = f"""
import pathlib
import subprocess
import sys

child = subprocess.Popen([sys.executable, "-c", {descendant_script!r}])
pathlib.Path({str(child_pid)!r}).write_text(str(child.pid))
"""

    async def run() -> None:
        supervisor = PreparationSupervisor()
        task = asyncio.create_task(
            supervisor.prepare("command", {"argv": [sys.executable, "-c", script]})
        )
        await _until(child_pid.exists)
        # Job leader exits but descendant remains; a successful command alone
        # cannot authorize publication. Supervisor fences the generation.
        await _until(lambda: bool(supervisor.quarantined_roots))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _until(lambda: not _exists(int(child_pid.read_text())))
        await supervisor.close()

    asyncio.run(run())


def _owned_helper() -> tuple[subprocess.Popen[bytes], int]:
    read_fd, write_fd = os.pipe()
    try:
        helper = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "m3._preparation_worker",
                "--ownership-fd",
                str(read_fd),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(read_fd,),
        )
    except BaseException:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    return helper, write_fd


def _acknowledge(ownership: int, ident: str) -> None:
    os.write(
        ownership,
        json.dumps({"version": 1, "action": "begin", "id": ident}).encode() + b"\n",
    )


def _finish_helper(helper: subprocess.Popen[bytes], ownership: int) -> None:
    import select

    assert helper.stdout is not None
    try:
        while True:
            assert select.select([helper.stdout], [], [], 5)[0]
            line = helper.stdout.readline()
            if not line:
                break
            frame = json.loads(line)
            if frame["event"] == "cleanup_started":
                assert os.getpgid(frame["pid"]) == frame["pid"]
                _acknowledge(ownership, frame["id"])
        helper.wait(timeout=5)
    finally:
        os.close(ownership)
        helper.stdout.close()


def _ready_helper(
    root: Path, operation: str, payload: dict[str, object]
) -> tuple[subprocess.Popen[bytes], int]:
    helper, ownership = _owned_helper()
    assert helper.stdin is not None and helper.stdout is not None
    helper.stdin.write(
        json.dumps(
            {
                "action": "submit",
                "id": "boundary",
                "root": str(root),
                "operation": operation,
                "payload": payload,
            }
        ).encode()
        + b"\n"
    )
    helper.stdin.flush()
    assert json.loads(helper.stdout.readline())["event"] == "started"
    _acknowledge(ownership, "boundary")
    assert json.loads(helper.stdout.readline())["event"] == "ready"
    return helper, ownership


def test_eof_after_accept_before_release_reclaims_staging(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    helper, ownership = _ready_helper(
        root, "command", {"argv": [sys.executable, "-c", "pass"]}
    )
    assert helper.stdin is not None and helper.stdout is not None
    helper.stdin.write(b'{"action":"accept","id":"boundary"}\n')
    helper.stdin.flush()
    assert json.loads(helper.stdout.readline())["event"] == "accepted"
    helper.stdin.close()
    _finish_helper(helper, ownership)
    assert not root.exists()


def test_cancelled_worktree_unregisters_exact_owned_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "sample").write_text("preserved")
    subprocess.run(["git", "add", "sample"], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=source,
        check=True,
    )
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    helper, ownership = _ready_helper(
        root,
        "workspace",
        {
            "policy": WorkspacePolicy(
                kind=WorkspaceKind.GIT_WORKTREE, source=str(source)
            ).model_dump(mode="json"),
            "include_excluded": False,
        },
    )
    assert (root / "payload" / ".git").is_file()
    before = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=source,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert str(root / "payload") in before
    assert helper.stdin is not None and helper.stdout is not None
    helper.stdin.close()
    _finish_helper(helper, ownership)
    assert not root.exists()
    after = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=source,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert str(root / "payload") not in after
    assert (source / "sample").read_text() == "preserved"


def test_rejected_submission_never_leaves_unowned_staging(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    script = f"""
import pathlib
import time

pathlib.Path({str(ready)!r}).touch()
time.sleep(60)
"""

    async def run() -> None:
        supervisor = PreparationSupervisor(max_jobs=1)
        task = asyncio.create_task(
            supervisor.prepare("command", {"argv": [sys.executable, "-c", script]})
        )
        await _until(ready.exists)
        first = next(iter(supervisor._jobs.values())).root
        with pytest.raises(Exception, match="preparation job failed"):
            await supervisor.prepare(
                "command", {"argv": [sys.executable, "-c", "pass"]}
            )
        assert tuple(job.root for job in supervisor._jobs.values()) == (first,)
        with pytest.raises(Exception, match="request exceeds limit"):
            await supervisor.prepare("command", {"argv": ["x" * 4096]})
        assert tuple(job.root for job in supervisor._jobs.values()) == (first,)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await supervisor.close()

    asyncio.run(run())


def test_temporary_default_and_bounded_routes() -> None:
    async def run() -> None:
        supervisor = PreparationSupervisor()
        plain = WorkspaceManager(WorkspacePolicy(), "plain")
        plain_root = await plain.create_async(supervisor)
        assert supervisor.helper_pid is None
        bounded = WorkspaceManager(WorkspacePolicy(), "bounded")
        bounded_root = await bounded.create_async(
            supervisor, deadline=time.monotonic() + 5
        )
        assert supervisor.helper_pid is not None
        assert bounded_root.parent != plain_root.parent
        bounded.cleanup()
        plain.cleanup()
        await supervisor.close()

    asyncio.run(run())


def test_fork_job_closes_controller_and_sibling_descriptors(tmp_path: Path) -> None:
    marker = tmp_path / "controller-private"
    marker.write_text("private")
    script = """
import json
import os
import time

from m3._preparation_worker import _ForkJob

extra = os.open(MARKER, os.O_RDONLY)
sibling_read, sibling_write = os.pipe()


def execute():
    leaked = []
    for fd in (extra, sibling_read, sibling_write):
        try:
            os.fstat(fd)
            leaked.append(fd)
        except OSError:
            pass
    report = {
        "leaked": leaked,
        "stdin_eof": os.read(0, 1) == b"",
        "group_owned": os.getpgrp() == os.getpid(),
    }
    os.write(1, json.dumps(report).encode())


job = _ForkJob(execute, output=True)
print(job.stdout.read().decode(), flush=True)
while job.poll() is None:
    time.sleep(0.01)
job.stdout.close()
os.close(extra)
os.close(sibling_read)
os.close(sibling_write)
""".replace("MARKER", repr(str(marker)))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        input=b"controller lease",
        capture_output=True,
        timeout=5,
        check=True,
    )
    report = json.loads(completed.stdout)
    assert report == {"leaked": [], "stdin_eof": True, "group_owned": True}


def test_two_real_kits_on_distinct_loop_threads_reuse_worker(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from m3._types.specs import AgentSpec
    from m3.async_api import AsyncMCPTestKit
    from m3.harness import DeterministicHarnessAdapter
    from m3.types import ACPAgent, ServerBinding, StdioServer

    source = tmp_path / "source"
    source.mkdir()
    (source / "sample").write_text("private")

    def one() -> int:
        async def run() -> int:
            async with AsyncMCPTestKit(env={}, cwd=str(tmp_path)) as kit:
                spec = AgentSpec(
                    harness=ACPAgent(model="fixture"),
                    servers=(
                        ServerBinding(
                            server=StdioServer(name="fixture", command="not-spawned")
                        ),
                    ),
                    workspace=WorkspacePolicy(
                        kind=WorkspaceKind.COPY, source=str(source)
                    ),
                )
                async with kit.agent_session(
                    spec, adapter=DeterministicHarnessAdapter()
                ) as session:
                    assert (session._workspace.root / "sample").read_text() == "private"
                    pid = kit._preparation_supervisor.helper_pid
                    assert pid is not None
                return pid

        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(lambda _: one(), range(2)))
    assert first == second
    assert one() == first  # Both prior kits and their event loops have closed.


def test_closed_loop_delivery_fences_real_writer(tmp_path: Path) -> None:
    from m3._preparation_owner import worker_preparation

    ready = tmp_path / "ready"
    script = f"""
import os
import pathlib
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path({str(ready)!r}).write_text(str(os.getpid()))
time.sleep(60)
"""
    loop = asyncio.new_event_loop()
    worker = worker_preparation()
    ident = "closed-loop-" + str(time.monotonic_ns())
    root = worker.root / ident
    frames: list[dict[str, object]] = []
    worker.register(ident, loop, frames.append)
    worker.send(
        "submit",
        ident,
        root=str(root),
        operation="command",
        payload={"argv": [sys.executable, "-c", script]},
    )
    end = time.monotonic() + 5
    while not ready.exists():
        assert time.monotonic() < end
        loop.run_until_complete(asyncio.sleep(0.01))
    writer = int(ready.read_text())
    loop.close()
    # The router detects a dead loop even when no more result packets arrive.
    while root.exists() or _exists(writer) or ident in worker._routes:
        assert time.monotonic() < end
        time.sleep(0.01)
    assert ident not in worker._routes


def test_helper_loss_fences_known_writer_without_signaling_unowned_groups(
    tmp_path: Path,
) -> None:
    # An isolated controller process avoids deliberately killing the helper
    # shared with unrelated tests. Its stdout reports only ownership evidence.
    ready = tmp_path / "ready"
    stop = tmp_path / "stop"
    script = """
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

from m3.preparation import PreparationError, PreparationSupervisor


async def main():
    supervisor = PreparationSupervisor(stop_seconds=0.1)
    ready_path = Path(READY)
    command = f'''
import os
import pathlib
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path({str(ready_path)!r}).write_text(str(os.getpid()))
end = time.monotonic() + 5
while not pathlib.Path({str(Path(STOP))!r}).exists() and time.monotonic() < end:
    pathlib.Path('unpublished-write').write_text('private')
    time.sleep(0.01)
'''
    task = asyncio.create_task(
        supervisor.prepare("command", {"argv": [sys.executable, "-c", command]})
    )
    while not Path(READY).exists():
        await asyncio.sleep(0.01)
    root = next(iter(supervisor._jobs.values())).root
    writer = int(Path(READY).read_text())
    group_signals = []
    os.killpg = lambda pid, sig: group_signals.append((pid, sig))
    os.kill(supervisor.helper_pid, signal.SIGKILL)
    try:
        await task
    except PreparationError:
        pass
    assert root in supervisor.quarantined_roots
    assert root.exists()
    os.kill(writer, 0)
    before = time.monotonic()
    await supervisor.close()
    assert time.monotonic() - before < 0.2
    assert not group_signals
    Path(STOP).touch()
    end = time.monotonic() + 3
    while True:
        try:
            os.kill(writer, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < end
        await asyncio.sleep(0.01)
    print(
        json.dumps(
            {
                "root": str(root),
                "quarantine": root in supervisor.quarantined_roots,
                "writer_dead": True,
                "no_group_signals": not group_signals,
            }
        ),
        flush=True,
    )
    before = time.monotonic()
    await supervisor.close()
    assert time.monotonic() - before < 0.2


asyncio.run(main())
""".replace("READY", repr(str(ready))).replace("STOP", repr(str(stop)))
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, timeout=10, check=True
    )
    evidence = json.loads(completed.stdout)
    assert evidence["quarantine"] and evidence["writer_dead"]
    assert evidence["no_group_signals"]
    root = Path(evidence["root"])
    assert root.exists()  # Helper loss retains ownership evidence, no deletion.
    import shutil

    shutil.rmtree(root)
    root.parent.rmdir()


def test_stalled_helper_bootstrap_does_not_allocate_on_caller_loop(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        supervisor = PreparationSupervisor(stop_seconds=0)
        warm = await supervisor.prepare(
            "command", {"argv": [sys.executable, "-c", "pass"]}
        )
        import shutil

        shutil.rmtree(warm.staging_root)
        helper = supervisor.helper_pid
        assert helper is not None
        os.kill(helper, signal.SIGSTOP)
        try:
            before = time.monotonic()
            with pytest.raises(asyncio.TimeoutError):
                await supervisor.prepare(
                    "workspace",
                    {
                        "policy": WorkspacePolicy().model_dump(mode="json"),
                        "include_excluded": False,
                    },
                    deadline=before + 0.05,
                )
            assert time.monotonic() - before < 0.2
            roots = supervisor.quarantined_roots
            assert len(roots) == 1
            assert not roots[0].exists()  # Allocation belongs to the gated job.
        finally:
            os.kill(helper, signal.SIGCONT)
        await _until(lambda: not supervisor._jobs)
        await supervisor.close()

    asyncio.run(run())


def test_helper_death_before_start_gate_creates_no_unknown_writer(
    tmp_path: Path,
) -> None:
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    helper = subprocess.Popen(
        [sys.executable, "-m", "m3._preparation_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    assert helper.stdin is not None and helper.stdout is not None
    helper.stdin.write(
        json.dumps(
            {
                "action": "submit",
                "id": "gate",
                "root": str(root),
                "operation": "command",
                "payload": {
                    "argv": [
                        sys.executable,
                        "-c",
                        "open('unexpected','w').write('late')",
                    ]
                },
            }
        ).encode()
        + b"\n"
    )
    helper.stdin.flush()
    started = json.loads(helper.stdout.readline())
    assert started["event"] == "started"
    os.kill(helper.pid, signal.SIGKILL)
    helper.wait(timeout=5)
    helper.stdin.close()
    helper.stdout.close()
    end = time.monotonic() + 5
    while _exists(started["pid"]):
        assert time.monotonic() < end
        time.sleep(0.01)
    assert list(root.iterdir()) == []


def test_kit_close_fences_only_its_jobs(tmp_path: Path) -> None:
    from m3.async_api import AsyncMCPTestKit
    from m3.preparation import PreparationError

    ready = tmp_path / "ready"
    script = f"""
import pathlib
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path({str(ready)!r}).touch()
time.sleep(60)
"""

    async def run() -> None:
        async with (
            AsyncMCPTestKit(env={}, cwd=str(tmp_path)) as first,
            AsyncMCPTestKit(env={}, cwd=str(tmp_path)) as second,
        ):
            pending = asyncio.create_task(
                first._preparation_supervisor.prepare(
                    "command", {"argv": [sys.executable, "-c", script]}
                )
            )
            await _until(ready.exists)
            root = next(iter(first._preparation_supervisor._jobs.values())).root
            material = await second._preparation_supervisor.prepare(
                "command",
                {"argv": [sys.executable, "-c", "open('stable','w').write('second')"]},
            )
            helper = second._preparation_supervisor.helper_pid
            await first.aclose()
            with pytest.raises(PreparationError, match="facade is closed"):
                await pending
            assert not root.exists()
            assert (
                material.staging_root / "payload" / "stable"
            ).read_text() == "second"
            assert second._preparation_supervisor.helper_pid == helper
            import shutil

            shutil.rmtree(material.staging_root)

    asyncio.run(run())


def test_concurrent_workspace_create_accepts_only_one_generation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "sample").write_text("content")

    async def run() -> None:
        supervisor = PreparationSupervisor()
        manager = WorkspaceManager(
            WorkspacePolicy(kind=WorkspaceKind.COPY, source=str(source)), "shared"
        )
        roots = await asyncio.gather(
            manager.create_async(supervisor), manager.create_async(supervisor)
        )
        assert roots[0] == roots[1]
        assert not supervisor._jobs
        manager.cleanup()
        await supervisor.close()

    asyncio.run(run())


def test_worker_thread_start_failure_closes_real_wakeup_sockets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket
    import threading

    from m3._preparation_owner import PreparationWorker

    pair = socket.socketpair()
    monkeypatch.setattr(socket, "socketpair", lambda: pair)

    def fail_start(_thread: threading.Thread) -> None:
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="thread unavailable"):
        PreparationWorker(64)
    assert pair[0].fileno() == -1
    assert pair[1].fileno() == -1


def test_router_runtime_error_fences_and_new_kit_recreates_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m3._preparation_owner import worker_preparation
    from m3.preparation import PreparationError

    async def run() -> None:
        old = worker_preparation()
        original_send = old.send

        def fail_begin(action: str, ident: str, **values: object) -> None:
            if action == "begin":
                raise RuntimeError("private detail must not escape")
            original_send(action, ident, **values)

        monkeypatch.setattr(old, "send", fail_begin)
        first = PreparationSupervisor(stop_seconds=0.1)
        with pytest.raises(PreparationError, match="preparation job failed") as failure:
            await first.prepare(
                "command",
                {
                    "argv": [
                        sys.executable,
                        "-c",
                        "open('unpublished','w').write('late')",
                    ]
                },
            )
        assert "private detail" not in str(failure.value)
        assert old.failed
        assert first.quarantined_roots
        assert all(not root.exists() for root in first.quarantined_roots)
        await first.close()
        later = PreparationSupervisor()
        material = await later.prepare(
            "command", {"argv": [sys.executable, "-c", "pass"]}
        )
        assert later._worker is not old
        assert later.helper_pid != old.helper_pid
        import shutil

        shutil.rmtree(material.staging_root)
        await later.close()

    asyncio.run(run())


@pytest.mark.parametrize("loss", ["deadline", "controller_eof", "helper_loss"])
def test_pre_group_child_is_owned_until_acknowledged(tmp_path: Path, loss: str) -> None:
    # Inject the boundary stall inside a real single-thread helper process.
    # A live helper owns escalation. After helper loss, the closed start gate
    # prevents mutation when the test peer releases its pre-group stall.
    helper_script = (
        """
import os
import pathlib
import signal
import sys
import time

from m3._preparation_worker import _supervise

marker = pathlib.Path(MARKER)
original = os.setsid


def stalled_setsid():
    if pathlib.Path(ARMED).exists() and not marker.exists():
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        marker.write_text(str(os.getpid()))
        end = time.monotonic() + 5
        while not pathlib.Path(RELEASE).exists() and time.monotonic() < end:
            time.sleep(0.01)
    return original()


os.setsid = stalled_setsid
_supervise(int(sys.argv[1]), pathlib.Path(sys.argv[2]), int(sys.argv[3]))
""".replace("MARKER", repr(str(tmp_path / "pre-group")))
        .replace("ARMED", repr(str(tmp_path / "armed")))
        .replace("RELEASE", repr(str(tmp_path / "release")))
    )
    controller_script = """
import asyncio
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

from m3.preparation import PreparationError, PreparationSupervisor

original = subprocess.Popen


def launch(argv, *args, **kwargs):
    if isinstance(argv, list) and "m3._preparation_worker" in argv:
        argv = [
            argv[0],
            "-c",
            HELPER,
            argv[argv.index("--max-jobs") + 1],
            argv[argv.index("--root") + 1],
            argv[argv.index("--ownership-fd") + 1],
        ]
    return original(argv, *args, **kwargs)


subprocess.Popen = launch


async def main():
    supervisor = PreparationSupervisor(stop_seconds=0.1)
    marker = pathlib.Path(MARKER)
    escaped = pathlib.Path(ESCAPED)
    # Reach a warm real helper before targeting the pre-group boundary. Cold
    # bootstrap deadline behavior has separate ownership tests.
    warm = await supervisor.prepare("command", {"argv": [sys.executable, "-c", "pass"]})
    shutil.rmtree(warm.staging_root)
    pathlib.Path(ARMED).touch()
    deadline = time.monotonic() + 1.5 if LOSS == "deadline" else None
    command = f'''
import pathlib

pathlib.Path({str(escaped)!r}).touch()
'''
    task = asyncio.create_task(
        supervisor.prepare(
            "command", {"argv": [sys.executable, "-c", command]}, deadline=deadline
        )
    )
    end = time.monotonic() + 3
    while not marker.exists():
        assert time.monotonic() < end
        await asyncio.sleep(0.01)
    child = int(marker.read_text())
    job = next(iter(supervisor._jobs.values()))
    root = job.root
    assert not root.exists()
    assert not supervisor._worker._groups  # No unacknowledged PGID recorded.
    group_signals = []
    os.killpg = lambda pid, sig: group_signals.append((pid, sig))
    if LOSS == "controller_eof":
        supervisor._worker.close(0.02)
    elif LOSS == "helper_loss":
        os.kill(supervisor.helper_pid, signal.SIGKILL)
    try:
        await task
    except (PreparationError, asyncio.TimeoutError):
        pass
    else:
        raise AssertionError("unacknowledged child was published")
    if LOSS == "helper_loss":
        assert root in supervisor.quarantined_roots
        assert not group_signals
        pathlib.Path(RELEASE).touch()
    end = time.monotonic() + 3
    while True:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < end
        await asyncio.sleep(0.01)
    assert not escaped.exists()
    assert not root.exists()
    await supervisor.close()
    print(json.dumps({"child_dead": True, "unpublished": True}), flush=True)


asyncio.run(main())
"""
    for name, value in (
        ("HELPER", helper_script),
        ("MARKER", str(tmp_path / "pre-group")),
        ("ARMED", str(tmp_path / "armed")),
        ("ESCAPED", str(tmp_path / "escaped")),
        ("LOSS", loss),
        ("RELEASE", str(tmp_path / "release")),
    ):
        controller_script = controller_script.replace(name, repr(value))
    completed = subprocess.run(
        [sys.executable, "-c", controller_script],
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert json.loads(completed.stdout) == {"child_dead": True, "unpublished": True}


def test_close_during_deadline_bootstrap_fences_before_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        supervisor = PreparationSupervisor()
        started = asyncio.Event()
        resume = asyncio.Event()
        original_start = supervisor._start

        async def controlled_start() -> None:
            await original_start()  # Real reusable owner/bootstrap allocation.
            started.set()
            await resume.wait()

        monkeypatch.setattr(supervisor, "_start", controlled_start)
        task = asyncio.create_task(
            supervisor.prepare(
                "command",
                {"argv": [sys.executable, "-c", "pass"]},
                deadline=time.monotonic() + 5,
            )
        )
        await started.wait()
        await supervisor.close()
        resume.set()
        with pytest.raises(Exception, match="supervisor is closed"):
            await task
        assert not supervisor._jobs
        assert supervisor._worker is not None
        assert not supervisor._worker._routes

    asyncio.run(run())


def test_ownership_eof_never_opens_unacknowledged_cleanup(tmp_path: Path) -> None:
    import select

    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    helper, ownership = _ready_helper(
        root, "command", {"argv": [sys.executable, "-c", "pass"]}
    )
    assert helper.stdin is not None and helper.stdout is not None
    helper.stdin.close()
    cleanup = None
    while cleanup is None:
        assert select.select([helper.stdout], [], [], 5)[0]
        frame = json.loads(helper.stdout.readline())
        if frame["event"] == "cleanup_started":
            cleanup = frame["pid"]
    assert root.exists()
    assert os.getpgid(cleanup) == cleanup
    os.close(ownership)  # Router lease lost before registering/releasing gate.
    helper.wait(timeout=5)
    helper.stdout.close()
    assert not _exists(cleanup)
    assert root.exists()  # Mutation never started; retain explicit quarantine.
    import shutil

    shutil.rmtree(root)  # Safe fixture cleanup after proven writer death.


def test_submission_eof_cleanup_is_quarantined_when_helper_custody_is_lost(
    tmp_path: Path,
) -> None:
    helper_script = """
import os
import pathlib
import signal
import sys
import time

from m3 import _preparation_worker as worker

def stalled_remove(root, source=None):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (root / "cleanup-mutation").touch()
    pathlib.Path(MARKER).write_text(str(os.getpid()))
    end = time.monotonic() + 5
    while not pathlib.Path(RELEASE).exists() and time.monotonic() < end:
        time.sleep(0.01)


worker._remove = stalled_remove
worker._supervise(int(sys.argv[1]), pathlib.Path(sys.argv[2]), int(sys.argv[3]))
""".replace("MARKER", repr(str(tmp_path / "cleanup-ready"))).replace(
        "RELEASE", repr(str(tmp_path / "release"))
    )
    controller_script = """
import asyncio
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

from m3.preparation import PreparationError, PreparationSupervisor

original = subprocess.Popen


def launch(argv, *args, **kwargs):
    if isinstance(argv, list) and "m3._preparation_worker" in argv:
        argv = [
            argv[0],
            "-c",
            HELPER,
            argv[argv.index("--max-jobs") + 1],
            argv[argv.index("--root") + 1],
            argv[argv.index("--ownership-fd") + 1],
        ]
    return original(argv, *args, **kwargs)


subprocess.Popen = launch


async def wait(predicate):
    end = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < end
        await asyncio.sleep(0.01)


def exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def main():
    ready = pathlib.Path(READY)
    cleanup = pathlib.Path(CLEANUP)
    supervisor = PreparationSupervisor(stop_seconds=0.1)
    command = f'''
import pathlib
import time

pathlib.Path({str(ready)!r}).touch()
time.sleep(60)
'''
    task = asyncio.create_task(
        supervisor.prepare("command", {"argv": [sys.executable, "-c", command]})
    )
    await wait(ready.exists)
    root = next(iter(supervisor._jobs.values())).root
    owner = supervisor._worker
    start = time.monotonic()
    owner.close(0.02)
    assert time.monotonic() - start < 0.2
    await wait(cleanup.exists)
    child = int(cleanup.read_text())
    assert child in owner._groups.values()  # Registered before cleanup mutation.
    group_signals = []
    os.killpg = lambda pid, sig: group_signals.append((pid, sig))
    os.kill(owner.helper_pid, signal.SIGKILL)
    await wait(lambda: not owner._thread.is_alive())
    assert exists(child)
    assert not group_signals
    pathlib.Path(RELEASE).touch()
    await wait(lambda: not exists(child))
    try:
        await task
    except PreparationError:
        pass
    else:
        raise AssertionError("EOF job was published")
    assert root in supervisor.quarantined_roots
    assert (
        root / "cleanup-mutation"
    ).exists()  # Dead-writer quarantine, no unsafe deletion.
    shutil.rmtree(root)
    root.parent.rmdir()
    await supervisor.close()
    print(
        json.dumps({"cleanup_dead": True, "registered_before_mutation": True}),
        flush=True,
    )


asyncio.run(main())
"""
    for name, value in (
        ("HELPER", helper_script),
        ("READY", str(tmp_path / "writer-ready")),
        ("CLEANUP", str(tmp_path / "cleanup-ready")),
        ("RELEASE", str(tmp_path / "release")),
    ):
        controller_script = controller_script.replace(name, repr(value))
    completed = subprocess.run(
        [sys.executable, "-c", controller_script],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert json.loads(completed.stdout) == {
        "cleanup_dead": True,
        "registered_before_mutation": True,
    }


@pytest.mark.parametrize(
    "extension",
    [
        "hook",
        "filter",
        "fsmonitor",
        "fsmonitor_implicit",
        "promisor",
        "promisor_implicit",
        "plain",
    ],
)
def test_bounded_worktree_rejects_executable_extensions_before_checkout(
    tmp_path: Path, extension: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "sample").write_text("preserved")
    subprocess.run(["git", "add", "sample"], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=source,
        check=True,
    )
    escaped = tmp_path / "escaped.pid"
    if extension == "hook":
        # A genuine checkout hook would escape the job's process group.
        hook = source / ".git" / "hooks" / "post-checkout"
        child = """
import pathlib
import time

time.sleep(0.1)
pathlib.Path("late-writer").touch()
time.sleep(60)
"""
        hook.write_text(
            "#!" + sys.executable + "\nimport pathlib,subprocess,sys\n"
            "child=subprocess.Popen([sys.executable,'-c',"
            + repr(child)
            + "],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            "pathlib.Path(" + repr(str(escaped)) + ").write_text(str(child.pid))\n"
        )
        hook.chmod(0o700)
    elif extension == "filter":
        (source / ".gitattributes").write_text("sample filter=fixture\n")
        subprocess.run(["git", "add", ".gitattributes"], cwd=source, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=" + os.devnull,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "attributes",
            ],
            cwd=source,
            check=True,
        )
        subprocess.run(
            ["git", "config", "filter.fixture.smudge", "cat"], cwd=source, check=True
        )
    elif extension == "fsmonitor":
        subprocess.run(
            ["git", "config", "core.fsmonitor", "fixture-external-command"],
            cwd=source,
            check=True,
        )
    elif extension.endswith("_implicit"):
        with (source / ".git" / "config").open("a") as configuration:
            configuration.write(
                "\n[core]\n fsmonitor\n"
                if extension == "fsmonitor_implicit"
                else '\n[remote "origin"]\n promisor\n'
            )
    elif extension == "promisor":
        subprocess.run(
            ["git", "config", "remote.origin.promisor", "true"], cwd=source, check=True
        )

    async def run() -> None:
        supervisor = PreparationSupervisor()
        manager = WorkspaceManager(
            WorkspacePolicy(kind=WorkspaceKind.GIT_WORKTREE, source=str(source)),
            "bounded-git",
        )
        try:
            if extension == "plain":
                root = await manager.create_async(
                    supervisor, deadline=time.monotonic() + 10
                )
                assert (root / "sample").read_text() == "preserved"
                manager.cleanup()
            else:
                with pytest.raises(
                    UnsupportedFeature,
                    match="cannot execute project hooks, filters or external helpers",
                ):
                    await manager.create_async(
                        supervisor, deadline=time.monotonic() + 10
                    )
                with pytest.raises(Exception, match="not been created"):
                    _ = manager.root
            assert not escaped.exists()  # Unsupported executable never ran.
            assert (source / "sample").read_text() == "preserved"
            worktrees = subprocess.run(
                [
                    "git",
                    "-c",
                    "core.fsmonitor=false",
                    "worktree",
                    "list",
                    "--porcelain",
                ],
                cwd=source,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            assert worktrees.count("worktree ") == 1
        finally:
            if escaped.exists():
                # Regression fixture cleanup only, never delete while escaped
                # writer survives. The passing path cannot reach this branch.
                os.killpg(int(escaped.read_text()), signal.SIGKILL)
            await supervisor.close()

    asyncio.run(run())


def test_failed_git_checkout_unregisters_even_after_staged_git_file_is_removed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "sample").write_text("preserved")
    subprocess.run(["git", "add", "sample"], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=source,
        check=True,
    )
    # Real Git keeps a registered worktree when post-checkout fails; the
    # compatibility creation path then removes payload before owned cleanup.
    hook = source / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o700)

    async def run() -> None:
        supervisor = PreparationSupervisor()
        manager = WorkspaceManager(
            WorkspacePolicy(kind=WorkspaceKind.GIT_WORKTREE, source=str(source)),
            "failed-git",
        )
        with pytest.raises(Exception, match="preparation job failed"):
            await manager.create_async(supervisor)
        assert not supervisor.quarantined_roots
        worktrees = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=source,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert worktrees.count("worktree ") == 1
        assert (source / "sample").read_text() == "preserved"
        await supervisor.close()

    asyncio.run(run())
