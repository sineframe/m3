"""Process/run-owned preparation IPC router, shared across kit event loops.

The thread only routes private protocol bytes and owns the helper lifetime. It
never materializes, publishes or deletes files. Async futures stay on the
requesting event loop. Closing a kit cannot tear down another kit's helper.
"""

from __future__ import annotations

import array
import asyncio
import atexit
import json
import os
import selectors
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from ._preparation_worker import MAX_FRAME

Callback = Callable[[dict[str, Any]], None]


class PreparationWorker:
    def __init__(self, max_jobs: int) -> None:
        self.pid = os.getpid()
        self.root = Path(os.environ.get("TMPDIR") or "/tmp") / (
            "m3-preparation-worker-" + uuid4().hex
        )
        self._max_jobs = max_jobs
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._outgoing: deque[bytes] = deque()
        self._acknowledgements: deque[bytes] = deque()
        self._queued_bytes = 0
        self._private_packets: deque[tuple[bytes, socket.socket]] = deque()
        self._private_sent: dict[str, socket.socket] = {}
        self._routes: dict[str, tuple[asyncio.AbstractEventLoop | None, Callback]] = {}
        self._groups: dict[str, int] = {}
        self._abandoned: set[str] = set()
        self._closing = False
        self._failed = False
        self._wake_read, self._wake_write = socket.socketpair()
        try:
            self._wake_read.setblocking(False)
            self._wake_write.setblocking(False)
            self._thread = threading.Thread(
                target=self._route, name="m3-preparation-ipc", daemon=True
            )
            self._thread.start()
        except BaseException:
            self._closing = True
            self._failed = True
            self._wake_read.close()
            self._wake_write.close()
            raise

    @property
    def failed(self) -> bool:
        with self._lock:
            return self._failed

    @property
    def helper_pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def register(
        self, ident: str, loop: asyncio.AbstractEventLoop | None, callback: Callback
    ) -> None:
        with self._lock:
            if self._failed or self._closing:
                raise RuntimeError("preparation worker is unavailable")
            self._routes[ident] = (loop, callback)

    def bind_private(self, ident: str, channel: socket.socket) -> None:
        """Transfer a one-use private job channel; never carry its contents here."""
        duplicate = channel.dup()
        with self._lock:
            if (
                self._failed
                or self._closing
                or len(self._private_packets) + len(self._private_sent) >= 64
            ):
                duplicate.close()
                raise RuntimeError("preparation worker is unavailable")
            self._private_packets.append((ident.encode("ascii"), duplicate))
        try:
            self._wake_write.send(b"x")
        except (BlockingIOError, OSError):
            pass

    def forget(self, ident: str, *, published: bool = False) -> None:
        with self._lock:
            self._routes.pop(ident, None)
            self._abandoned.discard(ident)
            if published:
                self._groups.pop(ident, None)

    def send(self, action: str, ident: str, **values: Any) -> None:
        frame = (
            json.dumps({"version": 1, "action": action, "id": ident, **values}).encode()
            + b"\n"
        )
        if len(frame) > 4096:
            raise ValueError("preparation request exceeds limit")
        with self._lock:
            if self._failed or (self._closing and action != "begin"):
                raise RuntimeError("preparation worker is unavailable")
            if self._queued_bytes + len(frame) > MAX_FRAME:
                raise RuntimeError("preparation input queue is full")
            (self._acknowledgements if action == "begin" else self._outgoing).append(
                frame
            )
            self._queued_bytes += len(frame)
        try:
            self._wake_write.send(b"x")
        except BlockingIOError:
            pass

    def _deliver(self, frame: dict[str, Any]) -> None:
        ident = frame["id"]
        with self._lock:
            route = self._routes.get(ident)
            if frame["event"] in {"private_bound", "private_unbound", "rejected"}:
                transferred = self._private_sent.pop(ident, None)
                if transferred is not None:
                    transferred.close()
            if frame["event"] in {"started", "cleanup_started"}:
                self._groups[ident] = frame["pid"]
            elif frame["event"] in {"removed", "rejected"}:
                self._groups.pop(ident, None)
        if frame["event"] in {"started", "cleanup_started"}:
            # No job may touch staging until its owning process group is
            # recorded here. Helper death before this gate causes child EOF.
            self.send("begin", ident)
        if route is None:
            return
        loop, callback = route
        try:
            if loop is None:
                callback(frame)
            else:
                loop.call_soon_threadsafe(callback, frame)
        except RuntimeError:
            # Loop loss cannot drop ownership. Fence through the still-live
            # helper; it owns all unfinished groups even without a recipient.
            if frame["event"] in {"started", "ready", "accepted", "released"}:
                try:
                    self.send("discard", ident)
                except (RuntimeError, ValueError):
                    pass
        if frame["event"] in {"removed", "rejected"}:
            self.forget(ident)

    def _route(self) -> None:
        selector = selectors.DefaultSelector()
        pending = bytearray()
        incoming = bytearray()
        acknowledgements = bytearray()
        ownership_read: int | None = None
        ownership_write: int | None = None
        process: subprocess.Popen[bytes] | None = None
        private_parent: socket.socket | None = None
        private_child: socket.socket | None = None
        try:
            private_parent, private_child = socket.socketpair(
                socket.AF_UNIX, socket.SOCK_DGRAM
            )
            private_parent.setblocking(False)
            ownership_read, ownership_write = os.pipe()
            os.set_blocking(ownership_write, False)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "m3._preparation_worker",
                    "--max-jobs",
                    str(self._max_jobs),
                    "--root",
                    str(self.root),
                    "--ownership-fd",
                    str(ownership_read),
                    "--private-fd",
                    str(private_child.fileno()),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=(ownership_read, private_child.fileno()),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            private_child.close()
            private_child = None
            os.close(ownership_read)
            ownership_read = None
            self._process = process
            assert process.stdin is not None and process.stdout is not None
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(self._wake_read, selectors.EVENT_READ, "wake")
            selector.register(process.stdout, selectors.EVENT_READ, "output")
            while True:
                with self._lock:
                    closing = self._closing
                    while self._outgoing:
                        packet = self._outgoing.popleft()
                        pending.extend(packet)
                        self._queued_bytes -= len(packet)
                    while self._acknowledgements:
                        packet = self._acknowledgements.popleft()
                        acknowledgements.extend(packet)
                        self._queued_bytes -= len(packet)
                with self._lock:
                    abandoned = [
                        ident
                        for ident, (loop, _callback) in self._routes.items()
                        if loop is not None
                        and loop.is_closed()
                        and ident not in self._abandoned
                    ]
                    self._abandoned.update(abandoned)
                for ident in abandoned:
                    if not closing:
                        self.send("discard", ident)
                with self._lock:
                    if self._private_packets:
                        packet, channel = self._private_packets[0]
                        try:
                            private_parent.sendmsg(
                                [packet],
                                [
                                    (
                                        socket.SOL_SOCKET,
                                        socket.SCM_RIGHTS,
                                        array.array("i", [channel.fileno()]),
                                    )
                                ],
                            )
                        except BlockingIOError:
                            pass
                        else:
                            self._private_packets.popleft()
                            self._private_sent[packet.decode("ascii")] = channel
                if closing:
                    # Lifetime EOF fences every unfinished job. The helper
                    # retains blocked groups/root ownership beyond our join.
                    process.stdin.close()
                    pending.clear()
                if pending and not closing:
                    try:
                        sent = os.write(process.stdin.fileno(), pending[:4096])
                        del pending[:sent]
                    except BlockingIOError:
                        pass
                if acknowledgements:
                    try:
                        sent = os.write(ownership_write, acknowledgements[:4096])
                        del acknowledgements[:sent]
                    except BlockingIOError:
                        pass
                for key, _mask in selector.select(0.005):
                    if key.data == "wake":
                        try:
                            self._wake_read.recv(65536)
                        except BlockingIOError:
                            pass
                    else:
                        raw = os.read(key.fd, 65536)
                        if not raw:
                            return
                        incoming.extend(raw)
                        if len(incoming) > MAX_FRAME:
                            raise ValueError("preparation output exceeds limit")
                        while b"\n" in incoming:
                            line, _, rest = incoming.partition(b"\n")
                            incoming[:] = rest
                            frame = json.loads(line)
                            if frame.get("version") != 1:
                                raise ValueError("invalid preparation protocol")
                            self._deliver(frame)
                if len(pending) > MAX_FRAME:
                    raise ValueError("preparation input queue exceeds limit")
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            pass
        finally:
            # Closing pipes triggers helper EOF/BrokenPipe fencing; do not
            # kill a helper which still owns an uninterruptible child group.
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
            if private_parent is not None:
                private_parent.close()
            if private_child is not None:
                private_child.close()
            with self._lock:
                while self._private_packets:
                    _packet, channel = self._private_packets.popleft()
                    channel.close()
                for channel in self._private_sent.values():
                    channel.close()
                self._private_sent.clear()
            if ownership_read is not None:
                os.close(ownership_read)
            if ownership_write is not None:
                os.close(ownership_write)
            selector.close()
            with self._lock:
                self._failed = True
                routes = tuple(self._routes)
            for ident in routes:
                self._deliver({"id": ident, "event": "lost"})
            # Only the helper is our waitable child. Its EOF handler owns job
            # cancellation while it survives. If it dies unexpectedly, recorded
            # job PGIDs no longer prove identity: retain their private roots in
            # quarantine rather than signal, publish, reuse, or delete them.
            if process is not None:
                process.wait()

    def close(self, timeout_seconds: float = 3) -> None:
        if self.pid != os.getpid():
            return
        with self._lock:
            self._closing = True
        try:
            self._wake_write.send(b"x")
        except (OSError, BlockingIOError):
            pass
        end = time.monotonic() + timeout_seconds
        self._thread.join(max(0, end - time.monotonic()))
        process = self._process
        if process is not None and not self._thread.is_alive():
            try:
                process.wait(timeout=max(0, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        # If IO survives KILL, keep the routing thread/helper object alive and
        # roots private. atexit/OS descriptor closure still supplies EOF.
        if not self._thread.is_alive():
            self._wake_read.close()
            self._wake_write.close()


_workers: dict[tuple[int, int], PreparationWorker] = {}
_retired_workers: list[PreparationWorker] = []
_workers_lock = threading.Lock()


def worker_preparation(max_jobs: int = 64) -> PreparationWorker:
    if os.name != "posix":
        raise RuntimeError("owned preparation requires POSIX process groups")
    key = (os.getpid(), max_jobs)
    with _workers_lock:
        worker = _workers.get(key)
        if worker is None or worker.failed:
            if worker is not None:
                # A replacement serves new requests only. The old owner keeps
                # fenced roots/groups; never retry the failed generation.
                _retired_workers.append(worker)
            worker = PreparationWorker(max_jobs)
            _workers[key] = worker
        return worker


def shutdown_worker_preparation(timeout_seconds: float = 3) -> None:
    """Run/worker teardown seam; never called by individual kit close."""
    from ._bootstrap_operations import drain_closed_bootstrap_operations

    end = time.monotonic() + timeout_seconds
    drain_closed_bootstrap_operations(end)
    with _workers_lock:
        workers = tuple(
            worker for (pid, _), worker in _workers.items() if pid == os.getpid()
        ) + tuple(worker for worker in _retired_workers if worker.pid == os.getpid())
        for key in tuple(_workers):
            if key[0] == os.getpid():
                del _workers[key]
        _retired_workers[:] = [
            worker for worker in _retired_workers if worker.pid != os.getpid()
        ]
    for worker in workers:
        worker.close(max(0, end - time.monotonic()))


atexit.register(shutdown_worker_preparation)
