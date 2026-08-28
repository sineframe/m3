from __future__ import annotations

import sys
from typing import Any
import pytest

from mcp_pal.types import (
    DirectExecutionSpec,
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionSnapshot,
    ExecutionSpec,
    LifecycleState,
    ListToolsOperation,
    PersistedExecutionReport,
    ServerBinding,
    StdioServer,
)
from mcp_pal.storage import StorageConflict
from mcp_pal_app.services.execution_service import AppExecutionError, AppExecutionService


def spec() -> ExecutionSpec:
    return DirectExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="x", command=sys.executable)),),
        operation=ListToolsOperation(server="x"),
    )


class FakeStore:
    def __init__(self, report: PersistedExecutionReport | None = None) -> None:
        self.report = report
        self.deleted = False
        self.cancelled = False
        self.conflict: StorageConflict | None = None
        self.closed = 0

    def get_report(self, execution_id: ExecutionId | str, *, after_sequence: int = -1, event_limit: int | None = None, artifact_limit: int | None = None) -> PersistedExecutionReport | None:
        if after_sequence < -1:
            raise ValueError("bad cursor")
        identifier = execution_id if isinstance(execution_id, ExecutionId) else ExecutionId(execution_id)
        if self.report is None or self.report.snapshot.execution_id != identifier:
            return None
        return self.report

    def list_executions(self, *, limit: int = 50, offset: int = 0, lifecycle: Any = None, outcome: Any = None) -> ExecutionPage:
        if lifecycle == "bad":
            raise ValueError("bad lifecycle")
        return ExecutionPage(items=(self.report.snapshot,) if self.report else (), limit=limit, offset=offset, total=1 if self.report else 0)

    def request_cancel(self, execution_id: ExecutionId | str, reason: str | None = None) -> bool:
        if self.conflict:
            raise self.conflict
        self.cancelled = True
        assert self.report
        self.report = PersistedExecutionReport(snapshot=self.report.snapshot.transition(LifecycleState.FINISHED, ExecutionOutcome.CANCELLED))
        return True

    def delete_execution(self, execution_id: ExecutionId | str) -> None:
        if self.conflict:
            raise self.conflict
        self.deleted = True
        self.report = None

    def close(self) -> None:
        self.closed += 1


class FakeHandle:
    def __init__(self, store: FakeStore, execution_id: ExecutionId) -> None:
        self.execution_id = execution_id
        self.store = store
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        self.store.request_cancel(self.execution_id)


class FakeKit:
    def __init__(self, store: FakeStore, execution_id: ExecutionId) -> None:
        self.store, self.execution_id = store, execution_id
        self.handle = FakeHandle(store, execution_id)
        self.closed = 0

    def submit(self, _spec: ExecutionSpec) -> FakeHandle:
        return self.handle

    def close(self) -> None:
        self.closed += 1


def active_report(identifier: ExecutionId = ExecutionId("execution-1")) -> PersistedExecutionReport:
    return PersistedExecutionReport(snapshot=ExecutionSnapshot(execution_id=identifier))


def test_service_crud_and_local_handle_cancel() -> None:
    store = FakeStore(active_report())
    kit = FakeKit(store, ExecutionId("execution-1"))
    service = AppExecutionService(store, kit)
    assert service.create(spec()).snapshot.execution_id == ExecutionId("execution-1")
    assert service.get("execution-1")
    assert service.list().total == 1
    assert service.report("execution-1")
    service.cancel("execution-1")
    assert kit.handle.cancelled and store.cancelled
    assert service.delete("execution-1") == ExecutionId("execution-1")
    assert not service._active_handles


def test_durable_cancel_without_local_handle_and_terminal_cancel() -> None:
    store = FakeStore(active_report())
    service = AppExecutionService(store, FakeKit(store, ExecutionId("other")))
    service.cancel("execution-1", reason="stop")
    assert store.cancelled
    with pytest.raises(AppExecutionError, match="terminal") as error:
        service.cancel("execution-1")
    assert error.value.code == "execution_terminal"


def test_service_validation_not_found_conflicts_and_cursor() -> None:
    store = FakeStore(active_report())
    service = AppExecutionService(store, FakeKit(store, ExecutionId("execution-1")))
    with pytest.raises(AppExecutionError) as error:
        service.get("")
    assert error.value.code == "invalid_execution_id"
    with pytest.raises(AppExecutionError) as error:
        service.get("missing")
    assert error.value.code == "execution_not_found"
    with pytest.raises(AppExecutionError) as error:
        service.list(lifecycle="bad")
    assert error.value.code == "invalid_execution_filter"
    with pytest.raises(AppExecutionError) as error:
        service.report("execution-1", after_sequence=-2)
    assert error.value.code == "invalid_report_cursor"
    store.conflict = StorageConflict("active execution")
    with pytest.raises(AppExecutionError) as error:
        service.delete("execution-1")
    assert error.value.code == "execution_active"
    with pytest.raises(AppExecutionError) as error:
        service.cancel("execution-1")
    assert error.value.code == "cancellation_conflict"


def test_close_is_idempotent_and_honors_ownership() -> None:
    store = FakeStore(active_report())
    kit = FakeKit(store, ExecutionId("execution-1"))
    service = AppExecutionService(store, kit, owns_store=True, owns_kit=True)
    service.close()
    service.close()
    assert kit.closed == 1 and store.closed == 1
