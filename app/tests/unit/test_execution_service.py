from __future__ import annotations

import hashlib
import sys
from typing import Any

import pytest

from m3 import (
    RawEvidence,
    RawEvidenceIntegrityError,
    RawEvidenceUnavailable,
    TraceUnavailable,
    TraceView,
)
from m3.storage import StorageConflict, StorageError
from m3.types import (
    DirectSpec,
    EvidenceRef,
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionReport,
    ExecutionSpec,
    ExecutionState,
    ExecutionStatus,
    ListTools,
    ServerBinding,
    StdioServer,
    TraceId,
)
from m3_app.services.execution_service import (
    AppExecutionError,
    AppExecutionService,
)


def spec() -> ExecutionSpec:
    return DirectSpec(
        servers=(ServerBinding(server=StdioServer(name="x", command=sys.executable)),),
        operation=ListTools(server="x"),
    )


class FakeStore:
    def __init__(self, report: ExecutionReport | None = None) -> None:
        self.report = report
        self.deleted = False
        self.cancelled = False
        self.conflict: StorageConflict | None = None
        self.closed = 0
        self.specification: ExecutionSpec | None = spec()
        self.trace: TraceView | None = TraceView(
            trace_id=TraceId("trace-1"), execution_id=ExecutionId("execution-1")
        )
        self.raw_error: Exception | None = None
        self.spec_error: Exception | None = None
        self.trace_error: Exception | None = None
        self.raw_max_bytes: int | None = None

    def get_report(
        self,
        execution_id: ExecutionId | str,
        *,
        after_sequence: int = -1,
        event_limit: int | None = None,
        artifact_limit: int | None = None,
    ) -> ExecutionReport | None:
        if after_sequence < -1:
            raise ValueError("bad cursor")
        identifier = (
            execution_id
            if isinstance(execution_id, ExecutionId)
            else ExecutionId(execution_id)
        )
        if self.report is None or self.report.snapshot.execution_id != identifier:
            return None
        return self.report

    def get_execution_spec(
        self, execution_id: ExecutionId | str
    ) -> ExecutionSpec | None:
        if self.spec_error:
            raise self.spec_error
        identifier = (
            execution_id
            if isinstance(execution_id, ExecutionId)
            else ExecutionId(execution_id)
        )
        if self.report is None or self.report.snapshot.execution_id != identifier:
            return None
        return self.specification

    def get_trace_view(self, execution_id: ExecutionId | str) -> TraceView | None:
        if self.trace_error:
            raise self.trace_error
        identifier = (
            execution_id
            if isinstance(execution_id, ExecutionId)
            else ExecutionId(execution_id)
        )
        if self.report is None or self.report.snapshot.execution_id != identifier:
            return None
        return self.trace

    def read_raw_evidence(
        self, reference: EvidenceRef, *, max_bytes: int = 1_048_576
    ) -> RawEvidence:
        self.raw_max_bytes = max_bytes
        if self.raw_error:
            raise self.raw_error
        content = b"abc"
        actual = EvidenceRef(
            evidence_id=reference.evidence_id,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=3,
            media_type="text/plain",
        )
        return RawEvidence(
            reference=actual,
            media_type="text/plain",
            content=content[:max_bytes].decode(),
            size_bytes=3,
            returned_size_bytes=min(max_bytes, 3),
            truncated=max_bytes < 3,
        )

    def list_executions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        lifecycle: Any = None,
        outcome: Any = None,
    ) -> ExecutionPage:
        if lifecycle == "bad":
            raise ValueError("bad lifecycle")
        return ExecutionPage(
            items=(self.report.snapshot,) if self.report else (),
            limit=limit,
            offset=offset,
            total=1 if self.report else 0,
        )

    def request_cancel(
        self, execution_id: ExecutionId | str, reason: str | None = None
    ) -> bool:
        if self.conflict:
            raise self.conflict
        self.cancelled = True
        assert self.report
        self.report = ExecutionReport(
            snapshot=self.report.snapshot.transition(
                ExecutionStatus.FINISHED, ExecutionOutcome.CANCELLED
            )
        )
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


class DelayedCancelStore(FakeStore):
    """Expose the small durable-commit window after a local cancel returns."""

    def __init__(self, report: ExecutionReport) -> None:
        super().__init__(report)
        self.cancel_requested = False
        self.report_reads = 0

    def get_report(
        self, execution_id: ExecutionId | str, **kwargs: Any
    ) -> ExecutionReport | None:
        self.report_reads += 1
        if self.cancel_requested and self.report_reads >= 3 and self.report is not None:
            self.report = ExecutionReport(
                snapshot=self.report.snapshot.transition(
                    ExecutionStatus.FINISHED, ExecutionOutcome.CANCELLED
                )
            )
        return super().get_report(execution_id, **kwargs)


class DelayedCancelHandle(FakeHandle):
    def cancel(self) -> None:
        self.cancelled = True
        assert isinstance(self.store, DelayedCancelStore)
        self.store.cancel_requested = True


class FakeKit:
    def __init__(self, store: FakeStore, execution_id: ExecutionId) -> None:
        self.store, self.execution_id = store, execution_id
        self.handle = FakeHandle(store, execution_id)
        self.closed = 0

    def submit(self, _spec: ExecutionSpec) -> FakeHandle:
        return self.handle

    def close(self) -> None:
        self.closed += 1


class DelayedCancelKit(FakeKit):
    def __init__(self, store: DelayedCancelStore) -> None:
        super().__init__(store, ExecutionId("execution-1"))
        self.handle = DelayedCancelHandle(store, self.execution_id)


def active_report(
    identifier: ExecutionId = ExecutionId("execution-1"),  # noqa: B008 - immutable test fixture default
) -> ExecutionReport:
    return ExecutionReport(snapshot=ExecutionState(execution_id=identifier))


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


def test_local_cancel_waits_for_terminal_snapshot_commit() -> None:
    store = DelayedCancelStore(active_report())
    kit = DelayedCancelKit(store)
    service = AppExecutionService(store, kit)
    service.create(spec())

    cancelled = service.cancel("execution-1")

    assert cancelled.snapshot.lifecycle is ExecutionStatus.FINISHED
    assert cancelled.snapshot.outcome is ExecutionOutcome.CANCELLED
    assert store.report_reads >= 3


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


def test_service_specification_and_trace_availability() -> None:
    store = FakeStore(active_report())
    service = AppExecutionService(store, FakeKit(store, ExecutionId("execution-1")))
    assert service.specification("execution-1") == store.specification
    store.specification = None
    assert service.specification("execution-1") is None
    store.specification = spec()
    store.spec_error = StorageError("database canary must not escape")
    with pytest.raises(AppExecutionError) as error:
        service.specification("execution-1")
    assert error.value.code == "execution_data_unavailable"
    assert "canary" not in error.value.message
    store.spec_error = None
    store.specification = spec()
    with pytest.raises(AppExecutionError) as error:
        service.trace_view("execution-1")
    assert error.value.code == "execution_not_terminal"
    assert store.report is not None
    store.report = ExecutionReport(
        snapshot=store.report.snapshot.transition(
            ExecutionStatus.FINISHED, ExecutionOutcome.COMPLETED
        )
    )
    assert service.trace_view("execution-1") == store.trace
    store.trace = None
    with pytest.raises(AppExecutionError) as error:
        service.trace_view("execution-1")
    assert error.value.code == "trace_unavailable"
    store.trace = TraceView(
        trace_id=TraceId("trace-1"), execution_id=ExecutionId("execution-1")
    )
    store.trace_error = TraceUnavailable("trace canary must not escape")
    with pytest.raises(AppExecutionError) as error:
        service.trace_view("execution-1")
    assert error.value.code == "trace_unavailable"
    assert "canary" not in error.value.message


def test_service_specification_missing_execution_and_raw_evidence_bound() -> None:
    store = FakeStore(active_report())
    service = AppExecutionService(store, FakeKit(store, ExecutionId("execution-1")))
    with pytest.raises(AppExecutionError) as error:
        service.specification("missing")
    assert error.value.code == "execution_not_found"
    result = service.read_raw_evidence(
        EvidenceRef(evidence_id="evidence-1"), max_bytes=2
    )
    assert result.returned_size_bytes == 2 and result.truncated is True
    assert store.raw_max_bytes == 2


@pytest.mark.parametrize(
    ("failure", "code"),
    (
        (RawEvidenceUnavailable("missing"), "raw_evidence_not_found"),
        (RawEvidenceIntegrityError("bad digest"), "raw_evidence_integrity_error"),
    ),
)
def test_service_raw_evidence_maps_stable_errors(failure: Exception, code: str) -> None:
    store = FakeStore(active_report())
    store.raw_error = failure
    service = AppExecutionService(store, FakeKit(store, ExecutionId("execution-1")))
    with pytest.raises(AppExecutionError) as error:
        service.read_raw_evidence(EvidenceRef(evidence_id="evidence-1"))
    assert error.value.code == code
    assert "digest" not in error.value.message and "missing" not in error.value.message


def test_close_is_idempotent_and_honors_ownership() -> None:
    store = FakeStore(active_report())
    kit = FakeKit(store, ExecutionId("execution-1"))
    service = AppExecutionService(store, kit, owns_store=True, owns_kit=True)
    service.close()
    service.close()
    assert kit.closed == 1 and store.closed == 1
