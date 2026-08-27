"""Versioned execution adapter backed exclusively by the public SDK APIs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from mcp_pal import ExecutionHandle, MCPTestKit
from mcp_pal.storage import SQLiteExecutionStore, StorageConflict
from mcp_pal.types import (
    ArtifactRef,
    CanonicalEvent,
    DirectOperationResult,
    ErrorInfo,
    ExecutionEvidence,
    ExecutionId,
    ExecutionPage,
    ExecutionSnapshot,
    ExecutionSpec,
    LifecycleState,
    PersistedExecutionReport,
)


_SPEC_ADAPTER: TypeAdapter[ExecutionSpec] = TypeAdapter(ExecutionSpec)


class V2ExecutionCreate(BaseModel):
    """JSON-only submission envelope; executable registrations are not data."""

    model_config = ConfigDict(extra="forbid")
    spec: dict[str, JsonValue]


class V2Error(BaseModel):
    code: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class V2ErrorEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    error: V2Error


class V2ExecutionEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    execution_id: ExecutionId
    snapshot: ExecutionSnapshot

    @classmethod
    def from_report(cls, report: PersistedExecutionReport) -> "V2ExecutionEnvelope":
        return cls(execution_id=report.snapshot.execution_id, snapshot=report.snapshot)


class V2ExecutionReportEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    execution_id: ExecutionId
    snapshot: ExecutionSnapshot
    events: tuple[CanonicalEvent, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    direct_result: DirectOperationResult | None = None
    error: ErrorInfo | None = None
    evidence: ExecutionEvidence | None = None
    after_sequence: int = -1
    event_limit: int = 100
    artifact_limit: int = 100
    artifact_count: int = 0
    event_count: int = 0
    events_truncated: bool = False
    artifacts_truncated: bool = False
    next_after_sequence: int | None = None

    @classmethod
    def from_report(cls, report: PersistedExecutionReport, *, after_sequence: int, event_limit: int, artifact_limit: int) -> "V2ExecutionReportEnvelope":
        events = report.events
        return cls(
            execution_id=report.snapshot.execution_id,
            snapshot=report.snapshot,
            events=events[:event_limit],
            artifacts=report.artifacts,
            direct_result=report.direct_result,
            error=report.error,
            evidence=report.evidence,
            after_sequence=after_sequence,
            event_limit=event_limit,
            artifact_limit=artifact_limit,
            artifact_count=report.artifact_count,
            event_count=report.event_count,
            events_truncated=report.events_truncated,
            artifacts_truncated=report.artifacts_truncated,
            next_after_sequence=report.next_after_sequence,
        )


class V2ExecutionPageEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    items: tuple[ExecutionSnapshot, ...]
    limit: int
    offset: int
    total: int

    @classmethod
    def from_page(cls, page: ExecutionPage) -> "V2ExecutionPageEnvelope":
        return cls(items=page.items, limit=page.limit, offset=page.offset, total=page.total)


class V2DeletedEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    execution_id: ExecutionId
    deleted: Literal[True] = True


class V2Fault(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Mapping[str, JsonValue] | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details: dict[str, JsonValue] = dict(details or {})
        super().__init__(message)


def _error_response(fault: V2Fault) -> JSONResponse:
    return JSONResponse(
        status_code=fault.status_code,
        content=V2ErrorEnvelope(error=V2Error(code=fault.code, message=fault.message, details=fault.details)).model_dump(mode="json"),
    )


class V2ExecutionService:
    """App adapter retaining SDK handles while SDK owns queue and persistence."""

    def __init__(self, store: SQLiteExecutionStore, kit: MCPTestKit) -> None:
        self.store = store
        self.kit = kit
        self._active_handles: dict[str, ExecutionHandle] = {}

    def _id(self, value: str) -> ExecutionId:
        try:
            return ExecutionId(value)
        except (TypeError, ValueError) as exc:
            raise V2Fault(422, "invalid_execution_id", "execution_id is invalid") from exc

    def _report(self, execution_id: ExecutionId) -> PersistedExecutionReport:
        report = self.store.get_report(execution_id, event_limit=1)
        if report is None:
            raise V2Fault(404, "execution_not_found", "execution was not found")
        if report.snapshot.lifecycle is LifecycleState.FINISHED:
            self._active_handles.pop(execution_id.root, None)
        return report

    def create(self, request: V2ExecutionCreate) -> PersistedExecutionReport:
        try:
            spec = _SPEC_ADAPTER.validate_python(request.spec)
            handle = self.kit.submit(spec)
        except ValidationError as exc:
            raise V2Fault(422, "invalid_execution_spec", "execution spec is invalid") from exc
        except (StorageConflict, ValueError, RuntimeError) as exc:
            raise V2Fault(422, "execution_submission_failed", "execution could not be submitted") from exc
        raw_execution_id = handle.execution_id
        execution_id = raw_execution_id if isinstance(raw_execution_id, ExecutionId) else ExecutionId(str(raw_execution_id))
        self._active_handles[execution_id.root] = handle
        return self._report(execution_id)

    def get(self, execution_id: str) -> PersistedExecutionReport:
        return self._report(self._id(execution_id))

    def list(self, *, limit: int, offset: int, lifecycle: str | None, outcome: str | None) -> ExecutionPage:
        try:
            return self.store.list_executions(limit=limit, offset=offset, lifecycle=lifecycle, outcome=outcome)
        except (TypeError, ValueError) as exc:
            raise V2Fault(422, "invalid_execution_filter", "execution filter is invalid") from exc

    def cancel(self, execution_id: str, reason: str | None) -> PersistedExecutionReport:
        identifier = self._id(execution_id)
        current = self._report(identifier)
        if current.snapshot.lifecycle is LifecycleState.FINISHED:
            raise V2Fault(409, "execution_terminal", "terminal executions cannot be cancelled")
        handle = self._active_handles.get(identifier.root)
        try:
            if handle is not None:
                handle.cancel()
            else:
                self.store.request_cancel(identifier, reason=reason)
        except (StorageConflict, RuntimeError) as exc:
            raise V2Fault(409, "cancellation_conflict", "execution cancellation conflicted with its current state") from exc
        return self._report(identifier)

    def report(self, execution_id: str, *, after_sequence: int, event_limit: int, artifact_limit: int) -> PersistedExecutionReport:
        identifier = self._id(execution_id)
        try:
            report = self.store.get_report(identifier, after_sequence=after_sequence, event_limit=event_limit, artifact_limit=artifact_limit)
        except ValueError as exc:
            raise V2Fault(422, "invalid_report_cursor", "report cursor or limit is invalid") from exc
        if report is None:
            raise V2Fault(404, "execution_not_found", "execution was not found")
        return report

    def delete(self, execution_id: str) -> ExecutionId:
        identifier = self._id(execution_id)
        self._report(identifier)
        try:
            self.store.delete_execution(identifier)
        except StorageConflict as exc:
            message = str(exc)
            code = "execution_active" if "active" in message else "execution_conflict"
            raise V2Fault(409, code, "active executions cannot be deleted" if code == "execution_active" else "execution could not be deleted") from exc
        return identifier


def install_v2(application: Any, store: SQLiteExecutionStore, kit: MCPTestKit | None = None) -> tuple[V2ExecutionService, bool]:
    owned_kit = kit is None
    execution_kit = kit or MCPTestKit(store=store, embedded_worker=True)
    service = V2ExecutionService(store, execution_kit)
    application.state.v2_store = store
    application.state.v2_kit = execution_kit
    application.state.v2_service = service
    application.state.v2_kit_owned = owned_kit
    application.add_exception_handler(V2Fault, lambda _request, exc: _error_response(exc))

    async def validation_handler(request: Request, exc: RequestValidationError):
        if not request.url.path.startswith("/api/v2/"):
            return await request_validation_exception_handler(request, exc)
        fields: list[dict[str, JsonValue]] = [{"loc": ".".join(str(item) for item in error.get("loc", ())), "message": str(error.get("msg", "invalid request"))} for error in exc.errors()]
        return JSONResponse(status_code=422, content=V2ErrorEnvelope(error=V2Error(code="invalid_request", message="request validation failed", details={"fields": cast(JsonValue, fields)})).model_dump(mode="json"))

    application.add_exception_handler(RequestValidationError, validation_handler)
    router = APIRouter(prefix="/api/v2/executions", tags=["executions-v2"])

    def get_service(request: Request) -> V2ExecutionService:
        return request.app.state.v2_service

    @router.post("", response_model=V2ExecutionEnvelope, status_code=status.HTTP_202_ACCEPTED)
    def create_execution(body: V2ExecutionCreate = Body(...), service: V2ExecutionService = Depends(get_service)) -> V2ExecutionEnvelope:
        return V2ExecutionEnvelope.from_report(service.create(body))

    @router.get("", response_model=V2ExecutionPageEnvelope)
    def list_executions(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), lifecycle: str | None = None, outcome: str | None = None, service: V2ExecutionService = Depends(get_service)) -> V2ExecutionPageEnvelope:
        return V2ExecutionPageEnvelope.from_page(service.list(limit=limit, offset=offset, lifecycle=lifecycle, outcome=outcome))

    @router.get("/{execution_id}", response_model=V2ExecutionEnvelope)
    def get_execution(execution_id: str, service: V2ExecutionService = Depends(get_service)) -> V2ExecutionEnvelope:
        return V2ExecutionEnvelope.from_report(service.get(execution_id))

    @router.post("/{execution_id}/cancel", response_model=V2ExecutionEnvelope)
    def cancel_execution(execution_id: str, reason: str | None = None, service: V2ExecutionService = Depends(get_service)) -> V2ExecutionEnvelope:
        return V2ExecutionEnvelope.from_report(service.cancel(execution_id, reason))

    @router.delete("/{execution_id}", response_model=V2DeletedEnvelope)
    def delete_execution(execution_id: str, service: V2ExecutionService = Depends(get_service)) -> V2DeletedEnvelope:
        return V2DeletedEnvelope(execution_id=service.delete(execution_id))

    @router.get("/{execution_id}/report", response_model=V2ExecutionReportEnvelope)
    def execution_report(execution_id: str, after_sequence: int = Query(-1, ge=-1), event_limit: int = Query(100, ge=1, le=1000), artifact_limit: int = Query(100, ge=1, le=1000), service: V2ExecutionService = Depends(get_service)) -> V2ExecutionReportEnvelope:
        report = service.report(execution_id, after_sequence=after_sequence, event_limit=event_limit, artifact_limit=artifact_limit)
        if report.snapshot.lifecycle is not LifecycleState.FINISHED:
            raise V2Fault(409, "execution_not_terminal", "execution report is available only after termination")
        return V2ExecutionReportEnvelope.from_report(report, after_sequence=after_sequence, event_limit=event_limit, artifact_limit=artifact_limit)

    application.include_router(router)
    return service, owned_kit
