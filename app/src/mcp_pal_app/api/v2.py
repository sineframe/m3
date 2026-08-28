"""Versioned execution adapter backed exclusively by the public SDK APIs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from mcp_pal import MCPTestKit
from mcp_pal.storage import SQLiteExecutionStore
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
from mcp_pal_app.services.execution_service import AppExecutionError, AppExecutionService


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


def _service_fault(error: AppExecutionError) -> V2Fault:
    status_by_code = {
        "invalid_execution_id": 422,
        "invalid_execution_spec": 422,
        "execution_submission_failed": 422,
        "invalid_execution_filter": 422,
        "invalid_report_cursor": 422,
        "execution_not_found": 404,
        "execution_terminal": 409,
        "cancellation_conflict": 409,
        "execution_active": 409,
        "execution_conflict": 409,
    }
    return V2Fault(status_by_code.get(error.code, 500), error.code, error.message)


def install_v2(application: Any, store: SQLiteExecutionStore, kit: MCPTestKit | None = None) -> tuple[AppExecutionService, bool]:
    owned_kit = kit is None
    execution_kit = kit or MCPTestKit(store=store, embedded_worker=True)
    service = AppExecutionService(store, execution_kit)
    application.state.v2_store = store
    application.state.v2_kit = execution_kit
    application.state.v2_service = service
    application.state.v2_kit_owned = owned_kit
    application.add_exception_handler(V2Fault, lambda _request, exc: _error_response(exc))
    application.add_exception_handler(AppExecutionError, lambda _request, exc: _error_response(_service_fault(exc)))

    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        if not request.url.path.startswith("/api/v2/"):
            return await request_validation_exception_handler(request, exc)
        fields: list[dict[str, JsonValue]] = [{"loc": ".".join(str(item) for item in error.get("loc", ())), "message": str(error.get("msg", "invalid request"))} for error in exc.errors()]
        return JSONResponse(status_code=422, content=V2ErrorEnvelope(error=V2Error(code="invalid_request", message="request validation failed", details={"fields": cast(JsonValue, fields)})).model_dump(mode="json"))

    application.add_exception_handler(RequestValidationError, validation_handler)
    router = APIRouter(prefix="/api/v2/executions", tags=["executions-v2"])

    def get_service(request: Request) -> AppExecutionService:
        return cast(AppExecutionService, request.app.state.v2_service)

    @router.post("", response_model=V2ExecutionEnvelope, status_code=status.HTTP_202_ACCEPTED)
    def create_execution(body: V2ExecutionCreate = Body(...), service: AppExecutionService = Depends(get_service)) -> V2ExecutionEnvelope:
        try:
            spec = _SPEC_ADAPTER.validate_python(body.spec)
        except ValidationError as exc:
            raise V2Fault(422, "invalid_execution_spec", "execution spec is invalid") from exc
        return V2ExecutionEnvelope.from_report(service.create(spec))

    @router.get("", response_model=V2ExecutionPageEnvelope)
    def list_executions(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), lifecycle: str | None = None, outcome: str | None = None, service: AppExecutionService = Depends(get_service)) -> V2ExecutionPageEnvelope:
        return V2ExecutionPageEnvelope.from_page(service.list(limit=limit, offset=offset, lifecycle=lifecycle, outcome=outcome))

    @router.get("/{execution_id}", response_model=V2ExecutionEnvelope)
    def get_execution(execution_id: str, service: AppExecutionService = Depends(get_service)) -> V2ExecutionEnvelope:
        return V2ExecutionEnvelope.from_report(service.get(execution_id))

    @router.post("/{execution_id}/cancel", response_model=V2ExecutionEnvelope)
    def cancel_execution(execution_id: str, reason: str | None = None, service: AppExecutionService = Depends(get_service)) -> V2ExecutionEnvelope:
        return V2ExecutionEnvelope.from_report(service.cancel(execution_id, reason))

    @router.delete("/{execution_id}", response_model=V2DeletedEnvelope)
    def delete_execution(execution_id: str, service: AppExecutionService = Depends(get_service)) -> V2DeletedEnvelope:
        return V2DeletedEnvelope(execution_id=service.delete(execution_id))

    @router.get("/{execution_id}/report", response_model=V2ExecutionReportEnvelope)
    def execution_report(execution_id: str, after_sequence: int = Query(-1, ge=-1), event_limit: int = Query(100, ge=1, le=1000), artifact_limit: int = Query(100, ge=1, le=1000), service: AppExecutionService = Depends(get_service)) -> V2ExecutionReportEnvelope:
        report = service.report(execution_id, after_sequence=after_sequence, event_limit=event_limit, artifact_limit=artifact_limit)
        if report.snapshot.lifecycle is not LifecycleState.FINISHED:
            raise V2Fault(409, "execution_not_terminal", "execution report is available only after termination")
        return V2ExecutionReportEnvelope.from_report(report, after_sequence=after_sequence, event_limit=event_limit, artifact_limit=artifact_limit)

    application.include_router(router)
    return service, owned_kit
