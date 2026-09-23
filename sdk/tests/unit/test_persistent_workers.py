from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from m3.elicitation import (
    ElicitationResponse,
    FormElicitationRequest,
    PendingElicitationRound,
)
from m3.errors import ManagedInputRecoveryError, ManagedInputStateError
from m3.events import EventFactory
from m3.execution_trace import ExecutionTraceRecorder
from m3.services.persistent import LeaseLost, SQLiteStoreWorker
from m3.storage import SQLiteExecutionStore, StorageConflict
from m3.types import (
    ErrorCode,
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    ExecutionState,
)


def _store(tmp_path: Path) -> SQLiteExecutionStore:
    return SQLiteExecutionStore(tmp_path / "stable.sqlite")


def _managed_pending(execution_id: str) -> PendingElicitationRound:
    return PendingElicitationRound(
        round_id="round-1",
        execution_id=execution_id,
        logical_operation_id="operation-1",
        server="example-mcp",
        operation_kind="tool",
        operation_name="book_shipment",
        request_state="opaque-state",
        requests={
            "address": FormElicitationRequest(
                request_key="address",
                message="Address",
                requested_schema={"type": "object"},
            )
        },
        created_at=datetime.now(timezone.utc),
    )


def _assert_reopenable_terminal(
    store: SQLiteExecutionStore,
    execution_id: ExecutionId,
    outcome: ExecutionOutcome,
) -> None:
    events = store.events(execution_id)
    terminals = tuple(
        event for event in events if event.kind is EventKind.EXECUTION_FINISHED
    )
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
    trace = ExecutionTraceRecorder(
        reopened,
        execution_id,
        trace_id=str(events[0].payload["trace_id"]),
    ).finalize(outcome)
    assert trace.execution_id == execution_id
    assert trace.completeness == terminal.payload["completeness"]
    assert trace.limitations == tuple(terminal.payload["limitations"])
    assert [event.model_dump(mode="json") for event in trace.events] == [
        event.model_dump(mode="json") for event in events
    ]


@pytest.mark.parametrize(
    ("action", "outcome"),
    (
        ("cancel", ExecutionOutcome.CANCELLED),
        ("interrupt", ExecutionOutcome.INTERRUPTED),
    ),
)
def test_store_terminalization_continues_positive_trace_offset(
    tmp_path: Path,
    action: str,
    outcome: ExecutionOutcome,
) -> None:
    execution_id = ExecutionId(f"execution-offset-{action}")
    store = _store(tmp_path)
    store.create(ExecutionState(execution_id=execution_id))
    factory = EventFactory(execution_id)
    store.append_events(
        (
            factory.create(
                EventKind.EXECUTION_CREATED,
                payload={
                    "lifecycle": "created",
                    "trace_id": f"trace-{execution_id.root}",
                },
            ),
        )
    )
    prior = factory.create(
        EventKind.DIAGNOSTIC,
        monotonic_offset_ms=42.0,
        payload={"source": "prior-process"},
    )
    store.append_events((prior,))
    command = store.enqueue_command(execution_id)
    if action == "cancel":
        assert store.request_cancel(execution_id)
    else:
        claimed = store.claim_next("worker-a", lease_seconds=30)
        assert claimed is not None and claimed[0].id == command.id
        assert store.mark_stale_interrupted(
            now=datetime.now(timezone.utc) + timedelta(seconds=31)
        )
    events = store.events(execution_id)
    assert events[-1].monotonic_offset_ms >= prior.monotonic_offset_ms
    _assert_reopenable_terminal(store, execution_id, outcome)


def test_sqlite_store_worker_uses_store_lease_and_command_state(tmp_path):
    store = _store(tmp_path)
    store.create(ExecutionState(execution_id=ExecutionId("execution-1")))
    store.enqueue_command("execution-1", payload={"message": "hello"})
    calls: list[str] = []

    def run(command, stable, lease):
        calls.append(command.id)

    worker = SQLiteStoreWorker(store, run, worker_id="worker-a")
    assert worker.run_once()
    assert calls
    assert store.get_command(calls[0]).status == "done"
    assert store.claim_next("worker-b") is None


def test_sqlite_store_stale_claim_is_interrupted_and_never_reclaimed(tmp_path):
    store = _store(tmp_path)
    store.create(ExecutionState(execution_id=ExecutionId("execution-1")))
    command = store.enqueue_command("execution-1")
    claimed = store.claim_next("worker-a", lease_seconds=0.01)
    assert claimed is not None
    time.sleep(0.03)
    assert store.mark_stale_interrupted()
    assert store.get_snapshot("execution-1").outcome.value == "interrupted"
    assert store.get_command(command.id).status == "interrupted"
    assert store.claim_next("worker-b") is None
    _assert_reopenable_terminal(
        store, ExecutionId("execution-1"), ExecutionOutcome.INTERRUPTED
    )


def test_stable_store_worker_rejects_terminal_report_after_lease_expiry(tmp_path):
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-1")
    store.create(ExecutionState(execution_id=execution_id))
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


def test_stable_store_strict_fifo_blocks_newer_command_while_oldest_is_leased(tmp_path):
    store = _store(tmp_path)
    for name in ("execution-1", "execution-2"):
        store.create(ExecutionState(execution_id=ExecutionId(name)))
    first = store.enqueue_command("execution-1", command_id="command-1")
    store.enqueue_command("execution-2", command_id="command-2")
    claimed = store.claim_next("worker-a", lease_seconds=30)
    assert claimed is not None and claimed[0].id == first.id
    assert store.claim_next("worker-b") is None


def test_stable_command_retry_is_idempotent_and_key_reuse_conflicts(tmp_path):
    store = _store(tmp_path)
    store.create(ExecutionState(execution_id=ExecutionId("execution-1")))
    first = store.enqueue_command(
        "execution-1", command_id="stable", payload={"message": "hello"}
    )
    assert (
        store.enqueue_command(
            "execution-1", command_id="stable", payload={"message": "hello"}
        )
        == first
    )
    with pytest.raises(StorageConflict):
        store.enqueue_command(
            "execution-1", command_id="stable", payload={"message": "changed"}
        )


def test_stable_worker_marks_heartbeat_failure_interrupted(tmp_path, monkeypatch):
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-1")
    store.create(ExecutionState(execution_id=execution_id))
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


def test_stable_cancel_before_claim_is_terminal_and_durable(tmp_path):
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-1")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(execution_id)
    assert store.request_cancel(execution_id, reason="user requested")
    snapshot = store.get_snapshot(execution_id)
    assert snapshot is not None and snapshot.outcome.value == "cancelled"
    assert store.get_command(command.id).status == "cancelled"
    _assert_reopenable_terminal(store, execution_id, ExecutionOutcome.CANCELLED)
    assert store.claim_next("worker-a") is None


def test_stable_cancel_during_worker_is_finalized(tmp_path):
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-1")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(execution_id)

    def run(_command, stable, _lease):
        stable.request_cancel(execution_id, reason="during run")

    assert SQLiteStoreWorker(store, run).run_once()
    snapshot = store.get_snapshot(execution_id)
    assert snapshot is not None and snapshot.outcome.value == "cancelled"
    assert store.get_command(command.id).status == "cancelled"
    _assert_reopenable_terminal(store, execution_id, ExecutionOutcome.CANCELLED)


def test_runner_lease_loss_is_terminal_and_not_resumed(tmp_path):
    store = _store(tmp_path)
    execution_id = ExecutionId("execution-1")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(execution_id)

    def run(_command, _store, _lease):
        raise LeaseLost("lost")

    assert SQLiteStoreWorker(store, run).run_once()
    assert store.get_snapshot(execution_id).outcome.value == "interrupted"
    assert store.get_command(command.id).status == "interrupted"
    _assert_reopenable_terminal(store, execution_id, ExecutionOutcome.INTERRUPTED)


def test_managed_runner_lease_loss_terminalizes_round_and_reopened_error(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("managed-execution-1")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(
        execution_id,
        payload={"human_input": "managed", "spec": {"kind": "agent"}},
    )
    managed = store.resolve_managed_input_store()
    round_record = managed.create_round(
        _managed_pending(execution_id.root),
        round_index=0,
        round_limit=10,
        owner_id="managed-runtime:managed-execution-1",
        lease_seconds=30,
        native_resume_token="provider-resume-token",
        delivery_idempotency_key="delivery-key",
    )

    def run(_command, _store, _lease):
        raise LeaseLost("worker disappeared")

    assert SQLiteStoreWorker(store, run, worker_id="worker-a").run_once()

    snapshot = store.get_snapshot(execution_id)
    assert snapshot is not None
    assert snapshot.outcome is ExecutionOutcome.FAILED
    assert store.get_command(command.id).status == "interrupted"
    failed_round = managed.get_round(execution_id.root, round_record.round_id)
    assert failed_round is not None
    assert failed_round.status == "failed"
    assert failed_round.failure_code == "recovery_unavailable"
    terminal = store.events(execution_id)[-1]
    assert (
        terminal.payload["error"]["code"]
        == ErrorCode.MANAGED_INPUT_RECOVERY_UNAVAILABLE
    )

    reopened = SQLiteExecutionStore(store.database)
    assert reopened.get_snapshot(execution_id).outcome is ExecutionOutcome.FAILED
    reopened_terminal = reopened.events(execution_id)[-1]
    assert (
        reopened_terminal.payload["error"]["code"]
        == ErrorCode.MANAGED_INPUT_RECOVERY_UNAVAILABLE
    )
    assert reopened.claim_next("worker-b") is None
    with pytest.raises(ManagedInputStateError):
        managed.submit_responses(
            execution_id.root,
            round_record.round_id,
            {"address": ElicitationResponse(action="accept", content={})},
            owner_id=round_record.owner_id,
            lease_token=round_record.lease_token,
            response_idempotency_key="late-response",
        )
    with pytest.raises(ManagedInputRecoveryError):
        managed.claim_round(
            execution_id.root,
            round_record.round_id,
            owner_id="replacement",
            lease_seconds=30,
            expected_lease_token=round_record.lease_token,
        )


def test_managed_heartbeat_loss_terminalizes_round_without_replacement_claim(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("managed-heartbeat")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(execution_id, payload={"human_input": "managed"})
    managed = store.resolve_managed_input_store()
    record = managed.create_round(
        _managed_pending(execution_id.root),
        round_index=0,
        round_limit=10,
        owner_id="managed-runtime:managed-heartbeat",
        lease_seconds=30,
    )
    monkeypatch.setattr(store, "heartbeat", lambda *_args, **_kwargs: False)

    def run(_command, _store, _lease):
        time.sleep(0.4)

    assert SQLiteStoreWorker(
        store, run, worker_id="worker-a", lease_seconds=0.1
    ).run_once()
    assert store.get_snapshot(execution_id).outcome is ExecutionOutcome.FAILED
    assert store.get_command(command.id).status == "interrupted"
    failed = managed.get_round(execution_id.root, record.round_id)
    assert failed is not None and failed.failure_code == "recovery_unavailable"


def test_managed_heartbeat_loss_releases_a_blocked_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("managed-blocked-runner")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(execution_id, payload={"human_input": "managed"})
    managed = store.resolve_managed_input_store()
    record = managed.create_round(
        _managed_pending(execution_id.root),
        round_index=0,
        round_limit=10,
        owner_id="managed-runtime:managed-blocked-runner",
        lease_seconds=30,
    )
    entered = threading.Event()
    released = threading.Event()
    monkeypatch.setattr(store, "heartbeat", lambda *_args, **_kwargs: False)

    def run(_command, _store, _lease):
        entered.set()
        assert released.wait(2)

    def cancel(_command):
        released.set()

    worker = SQLiteStoreWorker(
        store,
        run,
        worker_id="worker-a",
        lease_seconds=0.1,
        cancel_runner=cancel,
    )
    result = threading.Thread(target=worker.run_once)
    result.start()
    assert entered.wait(2)
    result.join(2)
    assert not result.is_alive()
    assert store.get_snapshot(execution_id).outcome is ExecutionOutcome.FAILED
    assert store.get_command(command.id).status == "interrupted"
    with pytest.raises(ManagedInputStateError):
        managed.submit_responses(
            execution_id.root,
            record.round_id,
            {"address": ElicitationResponse(action="accept", content={})},
            owner_id=record.owner_id,
            lease_token=record.lease_token,
            response_idempotency_key="late-response",
        )


def test_generic_worker_rejects_managed_runner_without_cancel_boundary(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("managed-no-cancel-boundary")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(execution_id, payload={"human_input": "managed"})
    called = threading.Event()

    def run(_command, _store, _lease):
        called.set()
        raise AssertionError(
            "a managed runner without cancellation must not be started"
        )

    assert SQLiteStoreWorker(store, run, worker_id="worker-a").run_once()
    assert not called.is_set()
    assert store.get_snapshot(execution_id).outcome is ExecutionOutcome.FAILED
    assert store.get_command(command.id).status == "interrupted"


def test_non_managed_worker_does_not_enable_managed_wal_schema(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "delete-journal.sqlite", wal=False)
    execution_id = ExecutionId("ordinary-delete-journal")
    store.create(ExecutionState(execution_id=execution_id))
    store.enqueue_command(execution_id, payload={"human_input": "fail"})

    assert store.claim_next("worker-a") is not None
    assert store.journal_mode == "delete"


def test_managed_stale_claim_is_terminalized_before_replacement_can_claim(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("managed-stale-claim")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(
        execution_id,
        payload={"human_input": "managed"},
    )
    managed = store.resolve_managed_input_store()
    record = managed.create_round(
        _managed_pending(execution_id.root),
        round_index=0,
        round_limit=10,
        owner_id="managed-runtime:managed-stale-claim",
        lease_seconds=30,
        native_resume_token="resume-token-is-not-proof",
        delivery_idempotency_key="idempotency-is-not-proof",
    )
    claimed = store.claim_next("worker-a", lease_seconds=0.01)
    assert claimed is not None and claimed[0].id == command.id
    time.sleep(0.03)

    assert store.claim_next("worker-b") is None
    assert store.get_snapshot(execution_id).outcome is ExecutionOutcome.FAILED
    assert store.get_command(command.id).status == "interrupted"
    failed = managed.get_round(execution_id.root, record.round_id)
    assert failed is not None and failed.failure_code == "recovery_unavailable"


def test_mark_stale_interrupted_uses_managed_recovery_boundary(tmp_path) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("managed-mark-stale")
    store.create(ExecutionState(execution_id=execution_id))
    store.enqueue_command(execution_id, payload={"human_input": "managed"})
    managed = store.resolve_managed_input_store()
    record = managed.create_round(
        _managed_pending(execution_id.root),
        round_index=0,
        round_limit=10,
        owner_id="managed-runtime:managed-mark-stale",
        lease_seconds=30,
    )
    assert store.claim_next("worker-a", lease_seconds=0.01) is not None
    time.sleep(0.03)

    changed = store.mark_stale_interrupted()
    assert changed == (execution_id,)
    assert store.get_snapshot(execution_id).outcome is ExecutionOutcome.FAILED
    failed = managed.get_round(execution_id.root, record.round_id)
    assert failed is not None and failed.failure_code == "recovery_unavailable"


def test_finished_managed_snapshot_still_cleans_claimed_command_and_lease(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("managed-finished-before-cleanup")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(execution_id, payload={"human_input": "managed"})
    claimed = store.claim_next("worker-a", lease_seconds=30)
    assert claimed is not None and claimed[0].id == command.id
    _, lease = claimed
    assert store.request_cancel(execution_id)
    assert store.finalize_cancelled(execution_id)

    assert not store.mark_managed_recovery_unavailable_if_lease_lost(lease)
    assert store.get_command(command.id).status == "interrupted"
    with sqlite3.connect(store.database) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM v2_leases WHERE execution_id=?", (execution_id.root,)
            ).fetchone()
            is None
        )


def test_managed_stale_worker_initializes_schema_before_transaction(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("managed-schema-not-opened")
    store.create(ExecutionState(execution_id=execution_id))
    command = store.enqueue_command(execution_id, payload={"human_input": "managed"})
    claimed = store.claim_next("worker-a", lease_seconds=0.01)
    assert claimed is not None and claimed[0].id == command.id
    time.sleep(0.03)

    assert store.mark_stale_interrupted() == (execution_id,)
    assert store.get_snapshot(execution_id).outcome is ExecutionOutcome.FAILED
    reopened = SQLiteExecutionStore(store.database)
    assert reopened.get_command(command.id).status == "interrupted"


@pytest.mark.parametrize(
    "status", ["pending", "response_validated", "delivery_started"]
)
def test_managed_lease_loss_terminalizes_each_unresolved_round_state(
    tmp_path, status: str
) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId(f"managed-{status}")
    store.create(ExecutionState(execution_id=execution_id))
    store.enqueue_command(
        execution_id,
        payload={"human_input": "managed"},
    )
    managed = store.resolve_managed_input_store()
    record = managed.create_round(
        _managed_pending(execution_id.root),
        round_index=0,
        round_limit=10,
        owner_id=f"managed-runtime:{execution_id.root}",
        lease_seconds=30,
    )
    if status in {"response_validated", "delivery_started"}:
        record = managed.submit_responses(
            execution_id.root,
            record.round_id,
            {"address": ElicitationResponse(action="accept", content={})},
            owner_id=record.owner_id,
            lease_token=record.lease_token,
            response_idempotency_key="response-1",
        )
    if status == "delivery_started":
        managed.start_delivery(
            execution_id.root,
            record.round_id,
            owner_id=record.owner_id,
            lease_token=record.lease_token,
        )

    def run(_command, _store, _lease):
        raise LeaseLost("worker disappeared")

    assert SQLiteStoreWorker(store, run, worker_id="worker-a").run_once()
    failed = managed.get_round(execution_id.root, record.round_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.failure_code == "recovery_unavailable"


def test_non_managed_lease_loss_keeps_interrupted_behavior(tmp_path) -> None:
    store = _store(tmp_path)
    execution_id = ExecutionId("ordinary-execution")
    store.create(ExecutionState(execution_id=execution_id))
    store.enqueue_command(execution_id, payload={"human_input": "fail"})

    def run(_command, _store, _lease):
        raise LeaseLost("worker disappeared")

    assert SQLiteStoreWorker(store, run, worker_id="worker-a").run_once()
    assert store.get_snapshot(execution_id).outcome is ExecutionOutcome.INTERRUPTED
