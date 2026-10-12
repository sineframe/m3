"""Private preparation supervisor protocol; never writes a published workspace.

The reusable supervisor performs no materialization or recursive deletion. Each
job (including cleanup) has its own process group. Submission EOF fences all
unfinished jobs; a separate ownership lease continues cleanup registration and
start acknowledgements. Ownership EOF never permits new mutation.
"""

from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal, cast

from ._process_group_custody import (
    ExitNotification,
    GroupInventory,
    ProcessStatusUnavailable,
    exited_status,
    fallback_exited,
    has_unreaped_status,
    ownership_supported,
    seal_group,
    validate_process_status,
)

MAX_FRAME = 16 * 1024 * 1024
TERM_GRACE = 0.3


def _group_alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        # A denied signal is not proof of death. Polling retains this private
        # root/group in quarantine instead of crashing the reusable owner.
        pass


class _UnsupportedGitExtensions(RuntimeError):
    pass


def _unsupported_git_setting(key: bytes, value: bytes) -> bool:
    if key.startswith(b"filter.") and key.endswith(
        (b".clean", b".smudge", b".process")
    ):
        return bool(value)
    if key == b"extensions.partialclone":
        return True
    if key == b"core.fsmonitor":
        return value.lower() not in {b"false", b"0", b"no"}
    if key.startswith(b"remote.") and key.endswith(b".promisor"):
        return value.lower() not in {b"false", b"0", b"no"}
    return False


def _bounded_git_argv(source: Path) -> list[str]:
    """Reject executable project extensions before bounded checkout.

    Git's builtin configuration/path queries do not execute checkout hooks or
    filter drivers. These queries run in the owned job, under its same clock.
    This is a capability check, not a sandbox for arbitrary custom Git code.
    """

    configured = subprocess.run(
        ["git", "config", "--null", "--list"],
        cwd=source,
        check=True,
        capture_output=True,
    ).stdout
    for entry in configured.split(b"\0"):
        key, _separator, value = entry.partition(b"\n")
        key = key.lower()
        if not key:
            continue
        if _unsupported_git_setting(key, value):
            raise _UnsupportedGitExtensions
    hooks = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
        cwd=source,
        check=True,
        capture_output=True,
    ).stdout.rstrip(b"\n")
    hooks_path = Path(os.fsdecode(hooks))
    if hooks_path.is_dir() and any(
        candidate.is_file() and os.access(candidate, os.X_OK)
        for candidate in hooks_path.iterdir()
        if not candidate.name.endswith(".sample")
    ):
        raise _UnsupportedGitExtensions
    # Guard hooks/fsmonitor even if a config file changes after the capability
    # check. Detected executable behavior is rejected, never silently skipped.
    return ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false"]


def _job(operation: str, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if operation == "workspace":
        from .types import WorkspaceKind, WorkspacePolicy
        from .workspace import WorkspaceManager, _safe_source

        policy = WorkspacePolicy.model_validate(payload["policy"])
        manager = WorkspaceManager(
            policy,
            "preparation",
            include_excluded=bool(payload["include_excluded"]),
            acknowledge_risky_inclusion=bool(payload["include_excluded"]),
        )
        owned = policy.kind is not WorkspaceKind.IN_PLACE
        target = root / "payload" if owned else _safe_source(str(policy.source))
        if owned:
            target.mkdir(mode=0o700)
            if policy.kind is not WorkspaceKind.TEMPORARY:
                source = _safe_source(str(policy.source))
                if policy.kind is WorkspaceKind.GIT_WORKTREE:
                    if payload.get("bounded"):
                        subprocess.run(
                            [
                                *_bounded_git_argv(source),
                                "worktree",
                                "add",
                                "--detach",
                                str(target),
                                "HEAD",
                            ],
                            cwd=source,
                            check=True,
                            capture_output=True,
                        )
                    else:
                        manager._create_worktree(source, target)
                else:
                    manager._copy_tree(source, target)
                    if policy.kind is WorkspaceKind.READ_ONLY:
                        manager._make_read_only(target)
        return {
            "root": str(target),
            "owned": owned,
            "baseline": manager._snapshot(target),
        }
    if operation == "command":
        # Internal seam for preparation tools and lifecycle fixtures. No shell
        # is added. All outputs must stay under this private staging directory.
        target = root / "payload"
        target.mkdir(mode=0o700)
        completed = subprocess.run(
            payload["argv"],
            cwd=target,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode:
            raise RuntimeError("preparation command failed")
        return {"root": str(target), "owned": True}
    raise ValueError("unsupported preparation operation")


def _remove(root: Path, source: str | None = None) -> None:
    if source is not None:
        # Failed/interrupted checkout can remove the staged .git file while
        # leaving repository registration. Consult Git's exact target ledger;
        # never prune other worktrees or depend on that private file surviving.
        command = [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "core.fsmonitor=false",
            "worktree",
        ]
        registered = subprocess.run(
            [*command, "list", "--porcelain", "-z"],
            cwd=source,
            check=True,
            capture_output=True,
        ).stdout.split(b"\0")
        target = root / "payload"
        if b"worktree " + os.fsencode(target.resolve()) in registered:
            subprocess.run(
                [*command, "remove", "--force", str(target)],
                cwd=source,
                check=True,
                capture_output=True,
            )
    try:
        info = root.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("preparation root is no longer a directory")
    # Called only in a cleanup process after the writer group has disappeared.
    # rmtree does not follow a root symlink. Make private read-only copies
    # removable without following links inside them.
    for directory, dirs, files in os.walk(root, topdown=False, followlinks=False):
        for name in (*dirs, *files):
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(0o700)
        Path(directory).chmod(0o700)
    shutil.rmtree(root)


class _ForkJob:
    """One waitable child of the single-thread helper, with a private group."""

    def __init__(
        self, function: Callable[[], None], *, output: bool, gated: bool = False
    ) -> None:
        import resource

        if not ownership_supported():
            raise RuntimeError("preparation requires unreaped child ownership")
        if threading.active_count() != 1:
            raise RuntimeError("preparation helper must remain single-threaded")
        validate_process_status()
        read_fd, write_fd = os.pipe()
        gate_read, gate_write = os.pipe()
        ready_read, ready_write = os.pipe()
        self.group_established = False
        self._gate_fd: int | None = gate_write if gated else None
        self.returncode: int | None = None
        self._reaped = False
        self.exited = False
        self.live_descendants: bool | None = None
        self._sealed_inventory: GroupInventory | None = None
        self._exit_notification: ExitNotification | None = None
        self.pid = os.fork()
        if self.pid == 0:
            try:
                os.close(ready_read)
                os.close(gate_write)
                os.dup2(write_fd, 1)
                null_fd = os.open(os.devnull, os.O_RDWR)
                os.dup2(null_fd, 0)
                os.dup2(null_fd, 2)
                # Drop controller lease, sibling output pipes and selector FD.
                # Only private job stdout and null stdin/stderr survive fork.
                limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
                end = int(limit) if limit != resource.RLIM_INFINITY else 1048576
                keep = sorted((gate_read, ready_write))
                start = 3
                for descriptor in keep:
                    os.closerange(start, descriptor)
                    start = descriptor + 1
                os.closerange(start, end)
                os.setsid()
                os.write(ready_write, b"s")
                os.close(ready_write)
                if os.read(gate_read, 1) != b"g":
                    os._exit(0)
                os.close(gate_read)
                function()
                os._exit(0)
            except BaseException:
                os._exit(1)
        try:
            if not has_unreaped_status() and sys.platform == "darwin":
                self._exit_notification = ExitNotification(self.pid)
        except BaseException:
            # Even ungated callers cannot start their function until exit
            # observation is installed. Failed registration leaves no writers.
            try:
                os.kill(self.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # No user function has run: the owned child is gated or has
                # already exited. Darwin refuses signals to zombie children.
                pass
            os.close(gate_write)
            os.waitpid(self.pid, 0)
            for descriptor in (read_fd, write_fd, gate_read, ready_read, ready_write):
                os.close(descriptor)
            raise
        os.close(write_fd)
        os.close(ready_write)
        self.ready: IO[bytes] = os.fdopen(ready_read, "rb", buffering=0)
        os.close(gate_read)
        if not gated:
            try:
                os.write(gate_write, b"g")
            except BrokenPipeError:
                pass
            os.close(gate_write)
        self.stdout: IO[bytes] | None = os.fdopen(read_fd, "rb", buffering=0)
        if not output:
            self.stdout.close()
            self.stdout = None

    def signal(self, sig: int) -> None:
        if self._reaped:
            return
        if self.group_established:
            _signal_group(self.pid, sig)
        elif self.poll() is None:
            # Before setsid acknowledgement this is our unreaped child PID,
            # so PID reuse is impossible. Never infer death from absent PGID.
            try:
                os.kill(self.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass

    def alive(self) -> bool:
        # poll() alone observes death and reaps. A second query here could
        # report death while leaving the reserved zombie permanently unreaped.
        return not self._reaped

    def begin(self) -> None:
        if self._gate_fd is not None and self.group_established:
            try:
                os.write(self._gate_fd, b"g")
            except BrokenPipeError:
                pass
            os.close(self._gate_fd)
            self._gate_fd = None

    def poll(self, inventory: GroupInventory | None = None) -> int | None:
        if self._reaped:
            return self.returncode
        if not self.exited:
            if has_unreaped_status():
                self.returncode = exited_status(self.pid)
                self.exited = self.returncode is not None
            else:
                self.exited = fallback_exited(self.pid, self._exit_notification)
        if not self.exited:
            return self.returncode
        self.live_descendants = None
        if self.group_established:
            current = inventory if inventory is not None else GroupInventory()
            zombie, descendants = current.snapshot(self.pid)
            self.live_descendants = descendants if zombie else None
            if not zombie or descendants:
                return self.returncode
            if sys.platform.startswith("linux"):
                if self._sealed_inventory is None:
                    seal_group(self.pid)
                    self._sealed_inventory = current
                    return self.returncode
                if current is self._sealed_inventory:
                    return self.returncode
        pid, status = os.waitpid(self.pid, 0)
        assert pid == self.pid
        self._reaped = True
        self.returncode = os.waitstatus_to_exitcode(status)
        if self._gate_fd is not None:
            os.close(self._gate_fd)
            self._gate_fd = None
        if self._exit_notification is not None:
            self._exit_notification.close()
            self._exit_notification = None
        self._sealed_inventory = None
        return self.returncode


def _fork_job(
    operation: str, root: Path, payload: dict[str, Any], *, create_root: bool = False
) -> _ForkJob:
    def execute() -> None:
        try:
            if create_root:
                root.mkdir(mode=0o700)
            result = _job(operation, root, payload)
            frame = json.dumps({"ok": True, "data": result}).encode() + b"\n"
            if len(frame) > MAX_FRAME:
                raise ValueError("preparation result exceeds limit")
        except _UnsupportedGitExtensions:
            frame = b'{"ok":false,"reason":"unsupported_git_extensions"}\n'
        except Exception:
            frame = b'{"ok":false}\n'
        view = memoryview(frame)
        while view:
            sent = os.write(1, view)
            view = view[sent:]

    return _ForkJob(execute, output=True, gated=True)


@dataclass
class Job:
    ident: str
    root: Path
    process: _ForkJob
    output: bytearray = field(default_factory=bytearray)
    state: Literal["running", "ready", "accepted", "released", "fenced", "removing"] = (
        "running"
    )
    stop_at: float | None = None
    killed: bool = False
    quarantine_sent: bool = False
    cleanup_source: str | None = None
    stream_open: bool = True


def _supervise(
    max_jobs: int = 64,
    owned_root: Path | None = None,
    ownership_fd: int | None = None,
) -> None:
    if os.name != "posix":
        raise RuntimeError("owned preparation requires POSIX process groups")
    try:
        validate_process_status()
        status_available = True
    except ProcessStatusUnavailable:
        status_available = False
    if owned_root is not None:
        owned_root.mkdir(mode=0o700)
    # Preload framework-owned operations once, in this isolated helper only.
    # No pytest/controller/harness process is forked.
    from .workspace import WorkspaceManager

    del WorkspaceManager
    selector = selectors.DefaultSelector()
    os.set_blocking(sys.stdin.fileno(), False)
    os.set_blocking(sys.stdout.fileno(), False)
    selector.register(sys.stdin, selectors.EVENT_READ, "controller")
    jobs: dict[str, Job] = {}
    incoming = bytearray()
    outgoing = bytearray()
    controller_open = True
    ownership_open = True
    ownership_input = bytearray()
    if ownership_fd is not None:
        os.set_blocking(ownership_fd, False)
        selector.register(ownership_fd, selectors.EVENT_READ, "ownership")

    def emit(ident: str, event: str, **data: Any) -> None:
        if ownership_open:
            outgoing.extend(
                json.dumps({"version": 1, "id": ident, "event": event, **data}).encode()
                + b"\n"
            )

    def stop(job: Job) -> None:
        if job.state == "released" and not controller_open:
            return
        job.state = "fenced"
        if job.stop_at is None:
            job.stop_at = time.monotonic()
            job.process.signal(signal.SIGTERM)

    def spawn_cleanup(job: Job) -> None:
        # Writer death precedes unregistering the exact owned worktree. Never
        # prune unrelated worktrees or delete the source repository.
        process = _ForkJob(
            lambda: _remove(job.root, job.cleanup_source),
            output=False,
            gated=True,
        )
        job.process = process
        job.state = "removing"
        job.stop_at = None
        job.killed = False
        job.stream_open = False
        os.set_blocking(process.ready.fileno(), False)
        selector.register(process.ready, selectors.EVENT_READ, ("group", job))

    def handle_request(request: dict[str, Any]) -> None:
        nonlocal controller_open, ownership_open
        if request.get("version", 1) != 1:
            raise ValueError("invalid preparation protocol")
        ident = request["id"]
        action = request["action"]
        if action == "submit":
            if not status_available:
                emit(ident, "rejected", reason="process_status_unavailable")
                return
            root = Path(request["root"])
            if (
                ident in jobs
                or len(jobs) >= max_jobs
                or (owned_root is not None and root.parent != owned_root)
            ):
                emit(ident, "rejected")
                return
            process = _fork_job(
                request["operation"],
                root,
                request["payload"],
                create_root=owned_root is not None,
            )
            assert process.stdout is not None
            os.set_blocking(process.stdout.fileno(), False)
            job = Job(ident, root, process)
            if (
                request["operation"] == "workspace"
                and request["payload"]["policy"]["kind"] == "git_worktree"
            ):
                job.cleanup_source = request["payload"]["policy"]["source"]
            jobs[ident] = job
            selector.register(process.stdout, selectors.EVENT_READ, job)
            os.set_blocking(process.ready.fileno(), False)
            selector.register(process.ready, selectors.EVENT_READ, ("group", job))
        elif action == "begin" and ownership_fd is None:
            candidate = jobs.get(ident)
            if candidate is not None and candidate.state in {
                "running",
                "removing",
            }:
                candidate.process.begin()
        elif action in {"cancel", "discard"}:
            candidate = jobs.get(ident)
            if candidate is not None:
                stop(candidate)
        elif action == "accept":
            candidate = jobs.get(ident)
            if candidate is not None and candidate.state == "ready":
                candidate.state = "accepted"
                emit(ident, "accepted")
            else:
                emit(ident, "failed")
        elif action == "release":
            candidate = jobs.get(ident)
            if candidate is not None and candidate.state == "accepted":
                candidate.state = "released"
                emit(ident, "released")
        elif action == "forget":
            candidate = jobs.get(ident)
            if candidate is not None and candidate.state == "released":
                del jobs[ident]
        elif action == "shutdown":
            controller_open = False
            if ownership_fd is None:
                ownership_open = False
                outgoing.clear()
            for job in jobs.values():
                stop(job)

    def read_ownership_acknowledgements() -> None:
        nonlocal controller_open, ownership_open
        assert ownership_fd is not None
        raw = os.read(ownership_fd, 65536)
        if not raw:
            selector.unregister(ownership_fd)
            ownership_open = False
            controller_open = False
            if sys.stdin.fileno() in selector.get_map():
                selector.unregister(sys.stdin)
            outgoing.clear()
            for job in jobs.values():
                stop(job)
            return
        ownership_input.extend(raw)
        if len(ownership_input) > MAX_FRAME:
            raise ValueError("preparation acknowledgement exceeds limit")
        while b"\n" in ownership_input:
            line, _, rest = ownership_input.partition(b"\n")
            ownership_input[:] = rest
            acknowledgement = json.loads(line)
            if (
                acknowledgement.get("version") != 1
                or acknowledgement.get("action") != "begin"
            ):
                raise ValueError("invalid preparation acknowledgement")
            candidate = jobs.get(acknowledgement["id"])
            if candidate is not None and candidate.state in {
                "running",
                "removing",
            }:
                candidate.process.begin()

    def read_controller_requests() -> None:
        nonlocal controller_open, ownership_open
        raw = os.read(sys.stdin.fileno(), 65536)
        if not raw:
            selector.unregister(sys.stdin)
            controller_open = False
            if ownership_fd is None:
                ownership_open = False
                outgoing.clear()
            for job in jobs.values():
                emit(job.ident, "failed")
                stop(job)
            return
        incoming.extend(raw)
        if len(incoming) > MAX_FRAME:
            raise ValueError("preparation request exceeds limit")
        while b"\n" in incoming:
            line, _, rest = incoming.partition(b"\n")
            incoming[:] = rest
            request = json.loads(line)
            handle_request(request)

    def handle_group_ready(key: selectors.SelectorKey) -> None:
        _phase, job = key.data
        raw = os.read(key.fd, 1)
        selector.unregister(key.fileobj)
        cast(IO[bytes], key.fileobj).close()
        if raw == b"s":
            job.process.group_established = True
            if job.state == "fenced":
                job.process.signal(signal.SIGKILL if job.killed else signal.SIGTERM)
            elif job.state in {"running", "removing"}:
                if ownership_open:
                    emit(
                        job.ident,
                        "cleanup_started" if job.state == "removing" else "started",
                        pid=job.process.pid,
                    )
                else:
                    stop(job)
        else:
            stop(job)

    def read_job_output(key: selectors.SelectorKey) -> None:
        job = key.data
        raw = os.read(key.fd, 65536)
        if raw:
            job.output.extend(raw)
            if len(job.output) > MAX_FRAME:
                stop(job)
        else:
            selector.unregister(key.fileobj)
            cast(IO[bytes], key.fileobj).close()
            job.stream_open = False

    def progress_jobs() -> None:
        now = time.monotonic()
        inventory = GroupInventory()
        for ident, job in tuple(jobs.items()):
            if job.state == "released":
                if not controller_open:
                    del jobs[ident]
                continue
            if job.state == "accepted":
                # Provisional acceptance is not publication. Only release
                # transfers ownership; EOF fences unreleased material.
                continue
            job.process.poll(inventory)
            exited = job.process.exited
            alive = job.process.alive()
            if job.state == "running" and exited and not job.stream_open:
                if job.process.live_descendants is True:
                    stop(job)
                elif not alive:
                    try:
                        result = json.loads(job.output)
                    except (ValueError, UnicodeError):
                        result = {"ok": False}
                    if result.get("ok") is True:
                        job.state = "ready"
                        emit(ident, "ready", data=result["data"])
                    else:
                        emit(
                            ident,
                            "failed",
                            reason="unsupported_git_extensions"
                            if result.get("reason") == "unsupported_git_extensions"
                            else None,
                        )
                        stop(job)
            if job.state == "fenced":
                if not exited or alive:
                    if not job.quarantine_sent:
                        emit(ident, "quarantined")
                        job.quarantine_sent = True
                    if (
                        job.stop_at is not None
                        and now - job.stop_at >= TERM_GRACE
                        and not job.killed
                    ):
                        job.process.signal(signal.SIGKILL)
                        job.killed = True
                elif not job.stream_open:
                    if ownership_open:
                        spawn_cleanup(job)
                    else:
                        # No router remains to register/ACK a new mutation
                        # group. Dead writers leave explicit quarantine.
                        del jobs[ident]
            elif job.state == "removing":
                if exited and not alive:
                    emit(
                        ident,
                        "removed" if job.process.returncode == 0 else "cleanup_failed",
                    )
                    del jobs[ident]
                elif (
                    job.stop_at is not None
                    and now - job.stop_at >= TERM_GRACE
                    and not job.killed
                ):
                    job.process.signal(signal.SIGKILL)
                    job.killed = True
            if len(outgoing) > MAX_FRAME * 2:
                raise ValueError("preparation output exceeds limit")

    def flush_events() -> None:
        nonlocal controller_open, ownership_open
        if outgoing:
            try:
                sent = os.write(sys.stdout.fileno(), outgoing[:65536])
                del outgoing[:sent]
            except BlockingIOError:
                pass
            except BrokenPipeError:
                controller_open = False
                ownership_open = False
                outgoing.clear()
                for job in jobs.values():
                    stop(job)

    try:
        while controller_open or jobs or outgoing:
            flush_events()
            for key, _mask in selector.select(timeout=0.02):
                if key.data == "ownership":
                    read_ownership_acknowledgements()
                elif key.data == "controller":
                    read_controller_requests()
                elif isinstance(key.data, tuple):
                    handle_group_ready(key)
                else:
                    read_job_output(key)
            progress_jobs()
    finally:
        # Protocol corruption cannot leave an unfenced writer. Kill owned
        # groups, retain roots (no deletion while writer death is uncertain).
        for job in jobs.values():
            job.process.signal(signal.SIGKILL)
        selector.close()
        if ownership_fd is not None:
            os.close(ownership_fd)
        if owned_root is not None:
            try:
                owned_root.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--max-jobs", type=int, default=64)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--ownership-fd", type=int)
    arguments = parser.parse_args()
    _supervise(arguments.max_jobs, arguments.root, arguments.ownership_fd)
