from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mcp_pal.events import EventFactory
from mcp_pal.execution_trace import ExecutionTraceRecorder
from mcp_pal.services.persistent import LeaseLost, SQLiteStoreWorker
from mcp_pal.storage import SQLiteExecutionStore, StorageConflict
from mcp_pal.types import EventKind, ExecutionId, ExecutionOutcome, ExecutionSnapshot


def _store(tmp_path: Path) -> SQLiteExecutionStore:
    return SQLiteExecutionStore(tmp_path / "canonical.sqlite")


def _assert_reopenable_terminal(
    store: SQLiteExecutionStore,
    execution_id: ExecutionId,
    outcome: ExecutionOutcome,
) -> None:
    events = store.events(execution_id)
    terminals = tuple(event for event in events if event.kind is EventKind.EXECUTION_FINISHED)
    assert len(terminals) == 1
    terminal = terminals[0]
    assert terminal.payload["outcome"] == outcome.value
    assert terminal.payload["completeness"] == "partial"
    assert tuple(terminal.payload["limitations"]) == ("capture_incomplete",)
    assert set(terminal.payload) >= {"outcome", "completeness", "limitations"}
    reopened = SQLiteExecutionStore(store.database)
    reopened_events = reopened.events(execution_id)
    assert [event.model_dump(mode="json") for event in reopened_events] == [
        event.model_dump(mode="json") for event in events
    ]
    assert reopened.get_snapshot(execution_id).outcome is outcome
    trace = ExecutionTraceRecorder(reopened, execution_id, trace_id=f"trace-{execution_id.root}").finalize(outcome)
    assert trace.execution_id == execution_id
    assert trace.completeness == terminal.payload["completeness"]
    assert trace.limitations == tuple(terminal.payload["limitations"])
    assert [event.model_dump(mode="json") for event in trace.events] == [
        event.model_dump(mode="json") for event in events
    ]


@pytest.mark.parametrize(
    ("action", "outcome"),
    (("cancel", ExecutionOutcome.CANCELLED), ("interrupt", ExecutionOutcome.INTERRUPTED)),
)
def test_store_terminalization_continues_positive_trace_offset(
    tmp_path: Path,
    action: str,
    outcome: ExecutionOutcome,
) -> None:
    execution_id = ExecutionId(f"execution-offset-{action}")
    store = _store(tmp_path)
    store.create(ExecutionSnapshot(execution_id=execution_id))
    factory = EventFactory(execution_id)
    store.append_events((factory.create(EventKind.EXECUTION_CREATED, payload={"lifecycle": "created"}),))
    prior = factory.create(EventKind.DIAGNOSTIC, monotonic_offset_ms=42.0, payload={"source": "prior-process"})
    store.append_events((prior,))
    command = store.enqueue_command(execution_id)
    if action == "cancel":
        assert store.request_cancel(execution_id)
    else:
        claimed = store.claim_next("worker-a", lease_seconds=30)
        assert claimed is not None and claimed[0].id == command.id
        assert store.mark_stale_interrupted(now=datetime.now(timezone.utc) + timedelta(seconds=31))
    events = store.events(execution_id)
    assert events[-1].monotonic_offset_ms >= prior.monotonic_offset_ms
    _assert_reopenable_terminal(store, execution_id, outcome)


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
    _assert_reopenable_terminal(store, ExecutionId("execution-1"), ExecutionOutcome.INTERRUPTED)


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
    _assert_reopenable_terminal(store, execution_id, ExecutionOutcome.INTERRUPTED)
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
    _assert_reopenable_terminal(store, execution_id, ExecutionOutcome.CANCELLED)
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
    _assert_reopenable_terminal(store, execution_id, ExecutionOutcome.CANCELLED)


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
    _assert_reopenable_terminal(store, execution_id, ExecutionOutcome.INTERRUPTED)
