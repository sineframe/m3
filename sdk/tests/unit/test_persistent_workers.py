from __future__ import annotations

import time

import pytest

from mcp_pal.services.persistent import LeaseLost, SQLiteStoreWorker
from mcp_pal.storage import SQLiteExecutionStore, StorageConflict
from mcp_pal.types import ExecutionId, ExecutionSnapshot


def _store(tmp_path):
    return SQLiteExecutionStore(tmp_path / "canonical.sqlite")


def test_sqlite_store_worker_uses_store_lease_and_command_state(tmp_path):
    store = _store(tmp_path)
    store.create(ExecutionSnapshot(execution_id=ExecutionId("execution-1")))
    store.enqueue_command("execution-1", payload={"message": "hello"})
    calls: list[str] = []

    def run(command, canonical, lease):
        calls.append(command.id)

    worker = SQLiteStoreWorker(store, run, worker_id="worker-a")
    assert worker.run_once()
    assert calls
    assert store.get_command(calls[0]).status == "done"
    assert store.claim_next("worker-b") is None


def test_sqlite_store_stale_claim_is_interrupted_and_never_reclaimed(tmp_path):
    store = _store(tmp_path)
    store.create(ExecutionSnapshot(execution_id=ExecutionId("execution-1")))
    command = store.enqueue_command("execution-1")
    claimed = store.claim_next("worker-a", lease_seconds=0.01)
    assert claimed is not None
    time.sleep(0.03)
    assert store.mark_stale_interrupted()
    assert store.get_snapshot("execution-1").outcome.value == "interrupted"
    assert store.get_command(command.id).status == "interrupted"
    assert store.claim_next("worker-b") is None


def test_canonical_store_worker_rejects_terminal_report_after_lease_expiry(tmp_path):
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-1")
    store.create(ExecutionSnapshot(execution_id=execution_id))
    command = store.enqueue_command(execution_id)
    claimed = store.claim_next("worker-a", lease_seconds=0.01)
    assert claimed is not None
    command, lease = claimed
    time.sleep(0.03)
    with pytest.raises(StorageConflict):
        store.complete_command(
            command.id,
            owner_id=lease.owner_id,
            lease_token=lease.lease_token,
        )


def test_canonical_store_strict_fifo_blocks_newer_command_while_oldest_is_leased(tmp_path):
    store = _store(tmp_path)
    for name in ("execution-1", "execution-2"):
        store.create(ExecutionSnapshot(execution_id=ExecutionId(name)))
    first = store.enqueue_command("execution-1", command_id="command-1")
    store.enqueue_command("execution-2", command_id="command-2")
    claimed = store.claim_next("worker-a", lease_seconds=30)
    assert claimed is not None and claimed[0].id == first.id
    assert store.claim_next("worker-b") is None


def test_canonical_command_retry_is_idempotent_and_key_reuse_conflicts(tmp_path):
    store = _store(tmp_path)
    store.create(ExecutionSnapshot(execution_id=ExecutionId("execution-1")))
    first = store.enqueue_command("execution-1", command_id="stable", payload={"message": "hello"})
    assert store.enqueue_command("execution-1", command_id="stable", payload={"message": "hello"}) == first
    with pytest.raises(StorageConflict):
        store.enqueue_command("execution-1", command_id="stable", payload={"message": "changed"})


def test_canonical_worker_marks_heartbeat_failure_interrupted(tmp_path, monkeypatch):
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-1")
    store.create(ExecutionSnapshot(execution_id=execution_id))
    command = store.enqueue_command(execution_id)
    original_heartbeat = store.heartbeat

    def fail_heartbeat(*args, **kwargs):
        return False

    monkeypatch.setattr(store, "heartbeat", fail_heartbeat)
    worker = SQLiteStoreWorker(store, lambda *_: time.sleep(0.4), lease_seconds=0.1)
    assert worker.run_once()
    assert store.get_snapshot(execution_id).outcome.value == "interrupted"
    assert store.get_command(command.id).status == "interrupted"
    monkeypatch.setattr(store, "heartbeat", original_heartbeat)


def test_canonical_cancel_before_claim_is_terminal_and_durable(tmp_path):
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-1")
    store.create(ExecutionSnapshot(execution_id=execution_id))
    command = store.enqueue_command(execution_id)
    assert store.request_cancel(execution_id, reason="user requested")
    snapshot = store.get_snapshot(execution_id)
    assert snapshot is not None and snapshot.outcome.value == "cancelled"
    assert store.get_command(command.id).status == "cancelled"
    assert store.claim_next("worker-a") is None


def test_canonical_cancel_during_worker_is_finalized(tmp_path):
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-1")
    store.create(ExecutionSnapshot(execution_id=execution_id))
    command = store.enqueue_command(execution_id)

    def run(_command, canonical, _lease):
        canonical.request_cancel(execution_id, reason="during run")

    assert SQLiteStoreWorker(store, run).run_once()
    snapshot = store.get_snapshot(execution_id)
    assert snapshot is not None and snapshot.outcome.value == "cancelled"
    assert store.get_command(command.id).status == "cancelled"


def test_runner_lease_loss_is_terminal_and_not_resumed(tmp_path):
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-1")
    store.create(ExecutionSnapshot(execution_id=execution_id))
    command = store.enqueue_command(execution_id)

    def run(_command, _store, _lease):
        raise LeaseLost("lost")

    assert SQLiteStoreWorker(store, run).run_once()
    assert store.get_snapshot(execution_id).outcome.value == "interrupted"
    assert store.get_command(command.id).status == "interrupted"
