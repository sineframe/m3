"""Transport-neutral application execution service.

The service owns the small amount of application orchestration around the
public SDK execution toolkit and store.  HTTP adapters translate these typed
errors and values into their wire representation; this module has no FastAPI
or response-envelope dependencies.
"""

from __future__ import annotations

import time
from typing import Protocol

from mcp_pal.storage import StorageConflict, StorageError
from mcp_pal import (
    AgentExecutionSpec,
    DirectExecutionSpec,
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionSpec,
    LifecycleState,
    PersistedExecutionReport,
    RawEvidence,
    RawEvidenceRef,
    TraceView,
    EvaluationAggregateQuery,
    EvaluationAggregateReport,
)
from mcp_pal import RawEvidenceIntegrityError, RawEvidenceUnavailable, TraceUnavailable


_CANCEL_SETTLE_TIMEOUT_SECONDS = 2.0
_CANCEL_SETTLE_POLL_SECONDS = 0.01


class AppExecutionError(RuntimeError):
    """Typed service failure independent of an HTTP transport."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class AppExecutionStore(Protocol):
    """Minimal public SDK store surface required by the app."""

    def get_report(
        self,
        execution_id: ExecutionId | str,
        *,
        after_sequence: int = -1,
        event_limit: int | None = None,
        artifact_limit: int | None = None,
    ) -> PersistedExecutionReport | None: ...

    def get_execution_spec(self, execution_id: ExecutionId | str) -> ExecutionSpec | None: ...
    def get_trace_view(self, execution_id: ExecutionId | str) -> TraceView | None: ...
    def read_raw_evidence(self, reference: RawEvidenceRef, *, max_bytes: int = 1_048_576) -> RawEvidence: ...

    def list_executions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        lifecycle: LifecycleState | str | None = None,
        outcome: ExecutionOutcome | str | None = None,
    ) -> ExecutionPage: ...

    def request_cancel(self, execution_id: ExecutionId | str, reason: str | None = None) -> bool: ...

    def delete_execution(self, execution_id: ExecutionId | str) -> None: ...
    def aggregate_evaluations(self, query: EvaluationAggregateQuery) -> EvaluationAggregateReport: ...


class AppExecutionKit(Protocol):
    """Minimal public synchronous SDK toolkit surface required by the app."""

    def submit(self, spec: ExecutionSpec) -> "AppExecutionHandle": ...

    def close(self) -> None: ...


class AppExecutionHandle(Protocol):
    """Minimal handle surface needed for app cancellation and retention."""

    @property
    def execution_id(self) -> ExecutionId | str: ...

    def cancel(self) -> None: ...


class AppExecutionService:
    """Application execution facade over an injected SDK toolkit and store."""

    def __init__(
        self,
        store: AppExecutionStore,
        kit: AppExecutionKit,
        *,
        owns_store: bool = False,
        owns_kit: bool = False,
    ) -> None:
        """Create a service over injected SDK resources.

        By default the caller owns ``store`` and ``kit`` (the HTTP app does
        this during its lifespan).  Set an ownership flag when constructing a
        standalone service/context manager so :meth:`close` shuts that
        resource down exactly once.
        """
        self.store = store
        self.kit = kit
        self._owns_store = owns_store
        self._owns_kit = owns_kit
        self._closed = False
        self._active_handles: dict[str, AppExecutionHandle] = {}

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("execution service is closed")

    def _id(self, value: ExecutionId | str) -> ExecutionId:
        try:
            return value if isinstance(value, ExecutionId) else ExecutionId(value)
        except (TypeError, ValueError) as exc:
            raise AppExecutionError("invalid_execution_id", "execution_id is invalid") from exc

    def _report(self, execution_id: ExecutionId) -> PersistedExecutionReport:
        report = self.store.get_report(execution_id, event_limit=1)
        if report is None:
            raise AppExecutionError("execution_not_found", "execution was not found")
        if report.snapshot.lifecycle is LifecycleState.FINISHED:
            self._active_handles.pop(execution_id.root, None)
        return report

    def create(self, spec: ExecutionSpec) -> PersistedExecutionReport:
        """Submit an already-validated SDK execution specification."""

        self._ensure_open()
        if not isinstance(spec, (DirectExecutionSpec, AgentExecutionSpec)):
            raise AppExecutionError("invalid_execution_spec", "execution spec is invalid")
        try:
            handle = self.kit.submit(spec)
        except (StorageConflict, ValueError, RuntimeError) as exc:
            raise AppExecutionError("execution_submission_failed", "execution could not be submitted") from exc
        raw_execution_id = handle.execution_id
        execution_id = raw_execution_id if isinstance(raw_execution_id, ExecutionId) else ExecutionId(str(raw_execution_id))
        self._active_handles[execution_id.root] = handle
        return self._report(execution_id)

    def get(self, execution_id: ExecutionId | str) -> PersistedExecutionReport:
        self._ensure_open()
        return self._report(self._id(execution_id))

    def specification(self, execution_id: ExecutionId | str) -> ExecutionSpec:
        self._ensure_open()
        identifier = self._id(execution_id)
        self._report(identifier)
        try:
            spec = self.store.get_execution_spec(identifier)
        except (StorageError, TypeError, ValueError) as exc:
            raise AppExecutionError("execution_data_unavailable", "execution data is unavailable") from exc
        if spec is None:
            raise AppExecutionError("execution_data_unavailable", "execution data is unavailable")
        return spec

    def trace_view(self, execution_id: ExecutionId | str) -> TraceView:
        self._ensure_open()
        identifier = self._id(execution_id)
        report = self._report(identifier)
        if report.snapshot.lifecycle is not LifecycleState.FINISHED:
            raise AppExecutionError("execution_not_terminal", "execution report is available only after termination")
        try:
            view = self.store.get_trace_view(identifier)
        except (TraceUnavailable, StorageError, TypeError, ValueError) as exc:
            raise AppExecutionError("trace_unavailable", "execution trace is unavailable") from exc
        if view is None:
            raise AppExecutionError("trace_unavailable", "execution trace is unavailable")
        return view

    def read_raw_evidence(self, reference: RawEvidenceRef, *, max_bytes: int = 1_048_576) -> RawEvidence:
        self._ensure_open()
        try:
            return self.store.read_raw_evidence(reference, max_bytes=max_bytes)
        except RawEvidenceUnavailable as exc:
            raise AppExecutionError("raw_evidence_not_found", "raw evidence was not found") from exc
        except RawEvidenceIntegrityError as exc:
            raise AppExecutionError("raw_evidence_integrity_error", "raw evidence integrity could not be verified") from exc

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        lifecycle: LifecycleState | str | None = None,
        outcome: ExecutionOutcome | str | None = None,
    ) -> ExecutionPage:
        self._ensure_open()
        try:
            return self.store.list_executions(
                limit=limit,
                offset=offset,
                lifecycle=lifecycle,
                outcome=outcome,
            )
        except (TypeError, ValueError) as exc:
            raise AppExecutionError("invalid_execution_filter", "execution filter is invalid") from exc

    def report(
        self,
        execution_id: ExecutionId | str,
        *,
        after_sequence: int = -1,
        event_limit: int = 100,
        artifact_limit: int = 100,
    ) -> PersistedExecutionReport:
        self._ensure_open()
        identifier = self._id(execution_id)
        try:
            report = self.store.get_report(
                identifier,
                after_sequence=after_sequence,
                event_limit=event_limit,
                artifact_limit=artifact_limit,
            )
        except ValueError as exc:
            raise AppExecutionError("invalid_report_cursor", "report cursor or limit is invalid") from exc
        if report is None:
            raise AppExecutionError("execution_not_found", "execution was not found")
        return report

    def cancel(self, execution_id: ExecutionId | str, reason: str | None = None) -> PersistedExecutionReport:
        self._ensure_open()
        identifier = self._id(execution_id)
        current = self._report(identifier)
        if current.snapshot.lifecycle is LifecycleState.FINISHED:
            raise AppExecutionError("execution_terminal", "terminal executions cannot be cancelled")
        handle = self._active_handles.get(identifier.root)
        try:
            if handle is not None:
                handle.cancel()
                # A local handle normally waits for the runtime's terminal
                # event, but the durable snapshot and trace are committed by
                # separate storage operations.  Give that final commit a
                # small settling window before returning a successful cancel
                # response.  Otherwise the next report request can observe
                # the still-active snapshot and incorrectly receive 409.
                deadline = time.monotonic() + _CANCEL_SETTLE_TIMEOUT_SECONDS
                while True:
                    settled = self._report(identifier)
                    if settled.snapshot.lifecycle is LifecycleState.FINISHED:
                        return settled
                    if time.monotonic() >= deadline:
                        raise AppExecutionError(
                            "cancellation_conflict",
                            "execution cancellation did not finish",
                        )
                    time.sleep(_CANCEL_SETTLE_POLL_SECONDS)
            else:
                self.store.request_cancel(identifier, reason=reason)
        except (StorageConflict, RuntimeError) as exc:
            raise AppExecutionError(
                "cancellation_conflict",
                "execution cancellation conflicted with its current state",
            ) from exc
        return self._report(identifier)

    def delete(self, execution_id: ExecutionId | str) -> ExecutionId:
        self._ensure_open()
        identifier = self._id(execution_id)
        self._report(identifier)
        try:
            self.store.delete_execution(identifier)
        except StorageConflict as exc:
            code = "execution_active" if "active" in str(exc) else "execution_conflict"
            message = (
                "active executions cannot be deleted"
                if code == "execution_active"
                else "execution could not be deleted"
            )
            raise AppExecutionError(code, message) from exc
        self._active_handles.pop(identifier.root, None)
        return identifier

    def aggregate(self, query: EvaluationAggregateQuery) -> EvaluationAggregateReport:
        self._ensure_open()
        try:
            return self.store.aggregate_evaluations(query)
        except (TypeError, ValueError) as exc:
            raise AppExecutionError("invalid_evaluation_aggregate_query", "evaluation aggregate query is invalid") from exc
        except StorageError as exc:
            raise AppExecutionError("evaluation_data_unavailable", "evaluation data is unavailable") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failure: BaseException | None = None
        if self._owns_kit:
            try:
                self.kit.close()
            except BaseException as exc:  # pragma: no cover - defensive cleanup
                failure = exc
        if self._owns_store:
            close = getattr(self.store, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException as exc:  # pragma: no cover - defensive cleanup
                    if failure is None:
                        failure = exc
        self._active_handles.clear()
        if failure is not None:
            raise failure

    def __enter__(self) -> "AppExecutionService":
        self._ensure_open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "AppExecutionError",
    "AppExecutionHandle",
    "AppExecutionKit",
    "AppExecutionService",
    "AppExecutionStore",
]
