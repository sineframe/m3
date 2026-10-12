"""Owned, cancellable preparation jobs shared by sessions across kits in one worker.

Only allowlisted operations run in helpers. They receive a unique private
staging directory and cannot publish into session state. A result becomes
caller-owned only after writer/group death, an accepted-generation handshake,
and a final absolute deadline check. Cancellation always fences first; an
unfinished writer remains owned by the supervisor in quarantine.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ._preparation_owner import PreparationWorker, worker_preparation
from .errors import UnsupportedFeature


class PreparationError(RuntimeError):
    """Preparation failed without exposing paths or child output."""


@dataclass(frozen=True)
class PreparedMaterial:
    """Private material with dead writers, transferred to the caller."""

    staging_root: Path
    data: dict[str, Any] = field(repr=False)


@dataclass
class _PendingJob:
    root: Path
    ready: asyncio.Future[dict[str, Any]] = field(repr=False)
    accepted: asyncio.Future[None]
    removed: asyncio.Future[None]
    released: asyncio.Future[None]
    fenced: bool = False
    pid: int | None = None
    quarantined: bool = False
    rejected: bool = False


class PreparationSupervisor:
    """A lazily started reusable helper; close after all owning sessions close.

    ``deadline`` is absolute monotonic time and is never reset on a frame,
    retry, or acknowledgment. ``stop_seconds`` bounds controller cancellation
    waiting, not ownership: the helper retains quarantined jobs beyond that
    wait until the complete writer group disappears.
    """

    def __init__(self, *, stop_seconds: float = 3.0, max_jobs: int = 64) -> None:
        if stop_seconds < 0 or not math.isfinite(stop_seconds):
            raise ValueError("preparation stop timeout must be finite and nonnegative")
        if max_jobs < 1 or max_jobs > 64:
            raise ValueError("preparation job limit must be between 1 and 64")
        self._max_jobs = max_jobs
        self.stop_seconds = stop_seconds
        self._worker: PreparationWorker | None = None
        self._jobs: dict[str, _PendingJob] = {}
        self._closed = False

    @property
    def quarantined_roots(self) -> tuple[Path, ...]:
        return tuple(job.root for job in self._jobs.values() if job.quarantined)

    @property
    def helper_pid(self) -> int | None:
        return self._worker.helper_pid if self._worker is not None else None

    async def _start(self) -> None:
        if self._closed:
            raise PreparationError("preparation supervisor is closed")
        if self._worker is None:
            try:
                self._worker = worker_preparation(self._max_jobs)
            except RuntimeError:
                raise PreparationError("owned preparation is unavailable") from None

    def _send(self, action: str, ident: str, **values: Any) -> None:
        if self._worker is None:
            raise PreparationError("preparation worker is unavailable")
        try:
            self._worker.send(action, ident, **values)
        except ValueError:
            raise PreparationError("preparation request exceeds limit") from None
        except RuntimeError:
            raise PreparationError("preparation worker is unavailable") from None

    def _deliver(self, frame: dict[str, Any]) -> None:
        # Always invoked on this facade's loop by the process-owned router.
        job = self._jobs.get(frame["id"])
        if job is None:
            return
        event = frame["event"]
        if event in {"started", "cleanup_started"}:
            job.pid = frame["pid"]
        elif event == "ready" and not job.fenced:
            if not job.ready.done():
                job.ready.set_result(frame["data"])
        elif event == "accepted" and not job.fenced:
            if not job.accepted.done():
                job.accepted.set_result(None)
        elif event == "released" and not job.fenced:
            if not job.released.done():
                job.released.set_result(None)
        elif event == "quarantined":
            job.quarantined = True
        elif event in {"failed", "rejected", "lost"}:
            job.rejected = event == "rejected"
            if event == "lost":
                job.fenced = True
                job.quarantined = True
            if frame.get("reason") == "unsupported_git_extensions":
                error: Exception = UnsupportedFeature(
                    "bounded git worktree preparation cannot execute project hooks, filters or external helpers"
                )
            elif frame.get("reason") == "process_status_unavailable":
                error = PreparationError(
                    "owned preparation requires an available process status backend"
                )
            else:
                error = PreparationError("preparation job failed")
            for future in (job.ready, job.accepted, job.released):
                if not future.done():
                    future.set_exception(error)
        elif event == "cleanup_failed":
            job.quarantined = True
            if not job.removed.done():
                job.removed.set_exception(
                    PreparationError("preparation cleanup failed")
                )
        elif event == "removed":
            if not job.removed.done():
                job.removed.set_result(None)
            self._jobs.pop(frame["id"], None)

    @staticmethod
    async def _wait(future: asyncio.Future[Any], deadline: float | None) -> Any:
        if deadline is None:
            return await asyncio.shield(future)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(asyncio.shield(future), remaining)

    async def prepare(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        deadline: float | None = None,
    ) -> PreparedMaterial:
        if operation not in {"workspace", "command"}:
            raise PreparationError("unsupported preparation operation")
        if deadline is not None and deadline <= time.monotonic():
            raise asyncio.TimeoutError
        # The helper bootstrap is part of the same total clock.
        if deadline is None:
            await self._start()
        else:
            await asyncio.wait_for(self._start(), max(0, deadline - time.monotonic()))
        # close() may run while wait_for schedules the bootstrap coroutine.
        # A closed facade must never register or adopt a later generation.
        if self._closed:
            raise PreparationError("preparation supervisor is closed")
        loop = asyncio.get_running_loop()
        ident = uuid4().hex
        assert self._worker is not None
        root = self._worker.root / ident
        job = _PendingJob(
            root,
            loop.create_future(),
            loop.create_future(),
            loop.create_future(),
            loop.create_future(),
        )
        # Retrieve errors even if cancellation stops the sole waiter.
        for future in (job.ready, job.accepted, job.removed, job.released):
            future.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )
        self._jobs[ident] = job
        submitted = False
        try:
            try:
                self._worker.register(ident, loop, self._deliver)
            except RuntimeError:
                raise PreparationError("preparation worker is unavailable") from None
            self._send(
                "submit", ident, root=str(root), operation=operation, payload=payload
            )
            submitted = True
            data = await self._wait(job.ready, deadline)
            if job.fenced:
                raise PreparationError("preparation generation is fenced")
            if deadline is not None and time.monotonic() >= deadline:
                raise asyncio.TimeoutError
            self._send("accept", ident)
            await self._wait(job.accepted, deadline)
            if job.fenced:
                raise PreparationError("preparation generation is fenced")
            if deadline is not None and time.monotonic() >= deadline:
                raise asyncio.TimeoutError
            self._send("release", ident)
            await self._wait(job.released, deadline)
            if job.fenced:
                raise PreparationError("preparation generation is fenced")
            if deadline is not None and time.monotonic() >= deadline:
                raise asyncio.TimeoutError
            self._send("forget", ident)
            self._worker.forget(ident)
            self._jobs.pop(ident, None)
            # No suspension after transfer: caller must publish/adopt without
            # an intervening await. The helper no longer owns this directory.
            return PreparedMaterial(root, data)
        except BaseException:
            if not submitted or job.rejected:
                # No helper accepted ownership and no writer could exist.
                self._worker.forget(ident)
                self._jobs.pop(ident, None)
                raise
            job.fenced = True
            job.quarantined = True
            try:
                self._send("discard", ident)
                await asyncio.wait_for(asyncio.shield(job.removed), self.stop_seconds)
            except (PreparationError, asyncio.TimeoutError, asyncio.CancelledError):
                # The reusable helper remains the owner. Closing its controller
                # pipe also fences/kills jobs, and never deletes live roots.
                pass
            raise

    async def close(self, *, deadline: float | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        pending = tuple(self._jobs.items())
        for ident, job in pending:
            job.fenced = True
            job.quarantined = True
            for future in (job.ready, job.accepted, job.released):
                if not future.done():
                    future.set_exception(
                        PreparationError("preparation facade is closed")
                    )
            try:
                self._send("discard", ident)
            except PreparationError:
                pass
        if self._worker is not None and self._worker.failed:
            # A lost helper cannot prove writer death. Roots stay quarantined;
            # waiting here cannot establish safe deletion, and the worker
            # owner no longer reserves those process group identities.
            return
        end = time.monotonic() + 3.0
        if deadline is not None:
            end = min(end, deadline)
        for _ident, job in pending:
            try:
                await self._wait(job.removed, end)
            except (PreparationError, asyncio.TimeoutError, asyncio.CancelledError):
                # Closing this facade never kills the worker-owned helper.
                # Late cleanup is routed or retained after this loop closes.
                pass
