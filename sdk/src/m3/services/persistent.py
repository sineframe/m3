"""Embedded worker for the stable SQLAlchemy-backed SQLite store.

The execution store owns the schema, queue, leases, cancellation flags, and
durable evidence. This module intentionally contains only the worker adapter;
the removed sqlite3 queue implementation was a second persistence model that
could diverge from :class:`m3.storage.SQLiteExecutionStore`.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any


class LeaseLost(RuntimeError):
    """Raised by a runner when its durable execution ownership is gone."""


class SQLiteStoreWorker:
    """Embedded worker facade for :class:`SQLiteExecutionStore`.

    The storage implementation owns its schema and lease records; this class
    calls that public store surface instead of maintaining another execution
    engine. ``runner`` is expected to use the existing execution runtime and
    to commit final events before returning.
    """

    def __init__(
        self,
        store: Any,
        runner: Callable[[Any, Any, Any], Any],
        *,
        worker_id: str | None = None,
        lease_seconds: float = 30.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.runner = runner
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex}"
        self.lease_seconds = lease_seconds
        self._stopped = False

    def run_once(self) -> bool:
        if self._stopped:
            return False
        self.store.mark_stale_interrupted()
        claimed = self.store.claim_next(
            self.worker_id, lease_seconds=self.lease_seconds
        )
        if claimed is None:
            return False
        command, lease = claimed
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()

        def renew() -> None:
            interval = max(self.lease_seconds / 3.0, 0.1)
            while not stop_heartbeat.wait(interval):
                try:
                    if not self.store.heartbeat(
                        lease, lease_seconds=self.lease_seconds
                    ):
                        lease_lost.set()
                        return
                except Exception:
                    # Do not let the runner finish successfully after its
                    # durable ownership signal has failed.
                    lease_lost.set()
                    return

        heartbeat_thread = threading.Thread(
            target=renew,
            name=f"m3-heartbeat-{self.worker_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            if self.store.cancellation_requested(command.execution_id):
                self.store.complete_command(
                    command.id,
                    owner_id=lease.owner_id,
                    lease_token=lease.lease_token,
                    status="cancelled",
                )
                self.store.release_lease(lease)
                return True
            self.runner(command, self.store, lease)
            if lease_lost.is_set():
                self.store.mark_interrupted_if_lease_lost(
                    lease, reason="worker heartbeat failed"
                )
                return True
            status = (
                "cancelled"
                if self.store.cancellation_requested(command.execution_id)
                else "done"
            )
            if status == "cancelled":
                self.store.finalize_cancelled(command.execution_id)
            self.store.complete_command(
                command.id,
                owner_id=lease.owner_id,
                lease_token=lease.lease_token,
                status=status,
            )
            self.store.release_lease(lease)
        except LeaseLost:
            # A replacement owner is authoritative. Do not retry or resume
            # the provider conversation after a lease-loss boundary.
            self.store.mark_interrupted_if_lease_lost(lease, reason="worker lease lost")
            return True
        except BaseException:
            # Preserve the runner's failure even if ownership disappeared
            # while reporting it. In that case durable state is interruption,
            # never a retry/resume of the provider session.
            try:
                self.store.complete_command(
                    command.id,
                    owner_id=lease.owner_id,
                    lease_token=lease.lease_token,
                    status="failed",
                )
                self.store.release_lease(lease)
            except Exception:
                try:
                    self.store.mark_interrupted_if_lease_lost(
                        lease, reason="worker lease lost during failure"
                    )
                except Exception:
                    pass
            raise
        finally:
            stop_heartbeat.set()
            # The SQLite connection has a bounded busy timeout. Join fully so
            # closing a worker never leaks a heartbeat thread.
            heartbeat_thread.join()
        return True

    def stop(self) -> None:
        self._stopped = True


__all__ = ["LeaseLost", "SQLiteStoreWorker"]
