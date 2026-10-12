"""Bounded, ordered batches for writes into a private capture root."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .._bootstrap_operations import (
    OwnedBootstrapOperations,
    OwnedResource,
    PrivateBytes,
)
from .._startup_deadlines import StartupBudget, earliest

_MAX_QUEUED_BYTES = 8 * 1024 * 1024
_MAX_BATCH_BYTES = 1024 * 1024
_APPEND_IO_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class _Record:
    filename: str
    data: bytes


class OwnedCaptureSink:
    def __init__(
        self,
        operations: OwnedBootstrapOperations,
        resource: OwnedResource,
        clock: StartupBudget,
        incomplete: Callable[[str], None],
    ) -> None:
        self.operations = operations
        self.resource = resource
        self.clock = clock
        self._incomplete = incomplete
        self._loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._records: deque[_Record] = deque()
        self._queued_bytes = 0
        self._inflight = False
        self._closed = False
        self._failed = False
        self._wake = asyncio.Event()
        self._changed = asyncio.Event()
        self._writer = asyncio.create_task(self._run())

    def enqueue(self, filename: str, data: str) -> None:
        raw = data.encode("utf-8")
        with self._lock:
            if self._closed or self._failed:
                return
            if (
                len(raw) > _MAX_BATCH_BYTES
                or self._queued_bytes + len(raw) > _MAX_QUEUED_BYTES
            ):
                self._loop.call_soon_threadsafe(
                    self._incomplete, "capture_queue_overflow"
                )
                return
            self._records.append(_Record(filename, raw))
            self._queued_bytes += len(raw)
        self._loop.call_soon_threadsafe(self._wake.set)

    def _batch(self) -> dict[str, PrivateBytes]:
        files: dict[str, bytearray] = {}
        total = 0
        with self._lock:
            while self._records:
                record = self._records[0]
                if total + len(record.data) > _MAX_BATCH_BYTES:
                    break
                self._records.popleft()
                self._queued_bytes -= len(record.data)
                total += len(record.data)
                files.setdefault(record.filename, bytearray()).extend(record.data)
            self._inflight = bool(files)
        return {name: PrivateBytes(bytes(data)) for name, data in files.items()}

    async def _run(self) -> None:
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                # Coalesce a burst of wire records into one owned append job.
                await asyncio.sleep(0.01)
                while files := self._batch():
                    deadline = self.clock.cleanup_deadline or self.clock.deadline
                    if deadline is None:
                        # Once startup is ready, each background append has
                        # its own bounded IO wait; flush/close add the caller's
                        # original turn or cleanup ceiling without restarting it.
                        deadline = time.monotonic() + _APPEND_IO_TIMEOUT_SECONDS
                    await asyncio.to_thread(
                        self.operations.append_capture,
                        self.resource,
                        files,
                        deadline=deadline,
                    )
                    with self._lock:
                        self._inflight = False
                    self._changed.set()
                with self._lock:
                    if self._closed:
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            with self._lock:
                self._failed = True
                self._records.clear()
                self._queued_bytes = 0
            self._incomplete("capture_write_failed")
        finally:
            with self._lock:
                self._inflight = False
            self._changed.set()

    async def flush(self, *, deadline: float | None = None) -> None:
        while True:
            self._changed.clear()
            with self._lock:
                pending = bool(self._records) or self._inflight
            if not pending:
                return
            active_deadline = earliest(
                deadline, self.clock.cleanup_deadline or self.clock.deadline
            )
            try:
                remaining = (
                    None
                    if active_deadline is None
                    else active_deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    raise asyncio.TimeoutError
                await asyncio.wait_for(self._changed.wait(), timeout=remaining)
            except BaseException:
                self.operations.fence_pending()
                raise

    async def close(self, *, deadline: float | None = None) -> None:
        with self._lock:
            self._closed = True
        self._wake.set()
        try:
            remaining = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            await asyncio.wait_for(asyncio.shield(self._writer), timeout=remaining)
        except BaseException:
            self.operations.fence_pending()
            raise
