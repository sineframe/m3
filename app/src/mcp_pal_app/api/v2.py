"""Versioned execution adapter backed exclusively by the public SDK APIs."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any, Literal, cast

from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from mcp_pal import MCPTestKit
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal import (
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionState,
    ExecutionSpec,
    ExecutionStatus,
    ExecutionReport,
    RawEvidence,
    EvidenceRef,
    TraceView,
    EvaluationQuery,
    EvaluationReport,
)
from mcp_pal_app.services.execution_service import AppExecutionError, AppExecutionService


class V2ExecutionCreate(BaseModel):
    """JSON-only submission envelope; executable registrations are not data."""

    model_config = ConfigDict(extra="forbid")
    spec: ExecutionSpec


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
    snapshot: ExecutionState
    spec: ExecutionSpec

    @classmethod
    def from_report(cls, report: ExecutionReport, spec: ExecutionSpec) -> "V2ExecutionEnvelope":
        return cls(execution_id=report.snapshot.execution_id, snapshot=report.snapshot, spec=spec)


class V2ExecutionReportEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    execution_id: ExecutionId
    spec: ExecutionSpec
    report: ExecutionReport
    trace: TraceView

    @classmethod
    def from_values(cls, execution_id: ExecutionId, spec: ExecutionSpec, report: ExecutionReport, trace: TraceView) -> "V2ExecutionReportEnvelope":
        return cls(execution_id=execution_id, spec=spec, report=report, trace=trace)


class V2ExecutionPageEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    page: ExecutionPage

    @classmethod
    def from_page(cls, page: ExecutionPage) -> "V2ExecutionPageEnvelope":
        return cls(page=page)


class V2EvidenceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: EvidenceRef
    max_bytes: int = Field(1_048_576, ge=1, le=1_048_576)


class V2EvidenceEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    evidence: RawEvidence


class V2EvaluationAggregateEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    aggregate: EvaluationReport


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


_SAFE_VALIDATION_LOCATIONS = frozenset(
    {
        "body", "query", "path", "spec", "run_id", "kind", "direct", "agent",
        "servers", "server", "profile", "alias", "required", "operation",
        "harness", "harness_profile", "message", "protocol", "timeout_seconds",
        "goal", "evaluations", "artifact_policy", "declared_artifacts", "workspace",
        "tool_policy", "permission_policy", "elicitation_policy", "sampling_policy",
        "filesystem_policy", "terminal_policy", "metadata", "validate_schemas",
        "reference", "max_bytes", "limit", "offset", "lifecycle", "outcome",
        "after_sequence", "event_limit", "artifact_limit", "execution_id", "from", "to", "group_by", "filters", "evaluator", "trial_id", "case_id", "transport", "model", "time",
    }
)


def _safe_validation_location(location: object) -> str:
    if not isinstance(location, (tuple, list)):
        return "body"
    parts = [
        str(item) if isinstance(item, int) else str(item) if str(item) in _SAFE_VALIDATION_LOCATIONS else "field"
        for item in location
    ]
    return ".".join(parts) or "body"


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
        "execution_not_terminal": 409,
        "raw_evidence_not_found": 404,
        "execution_data_unavailable": 500,
        "trace_unavailable": 500,
        "raw_evidence_integrity_error": 500,
        "invalid_report_cursor": 422,
        "execution_not_found": 404,
        "execution_terminal": 409,
        "cancellation_conflict": 409,
        "execution_active": 409,
        "execution_conflict": 409,
        "invalid_evaluation_aggregate_query": 422,
        "evaluation_data_unavailable": 500,
    }
    return V2Fault(status_by_code.get(error.code, 500), error.code, error.message)


def install_v2(
    application: Any,
    store: SQLiteExecutionStore,
    kit: MCPTestKit | None = None,
    *,
    embedded_worker: bool = True,
) -> tuple[AppExecutionService, bool]:
    owned_kit = kit is None
    execution_kit = kit or MCPTestKit(store=store, embedded_worker=embedded_worker)
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
        # Validation diagnostics are deliberately value-free.  Pydantic's
        # discriminator errors include the supplied tag (and some validators
        # include arbitrary input values) in their messages, which is not a
        # safe API response.  Keep only structural locations and stable
        # generic messages.
        fields: list[dict[str, JsonValue]] = []
        for error in exc.errors():
            loc = _safe_validation_location(error.get("loc", ()))
            error_type = str(error.get("type", ""))
            message = "field is required" if error_type == "missing" else "field is invalid"
            if error_type == "extra_forbidden":
                message = "extra fields are not permitted"
            fields.append({"loc": loc, "message": message})

        # A malformed value *inside* the required outer ``spec`` object is an
        # execution-spec error.  A missing/non-object outer value is request
        # shape validation instead.  The raw body is used solely to make
        # that distinction; it is never copied into the response.
        try:
            payload = json.loads((await request.body()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        spec_value = payload.get("spec") if isinstance(payload, Mapping) else None
        spec_is_object = isinstance(spec_value, Mapping)
        has_spec_error = any(item["loc"] == "body.spec" or str(item["loc"]).startswith("body.spec.") for item in fields)
        if request.url.path == "/api/v2/evaluations/aggregate":
            code = "invalid_evaluation_aggregate_query"
            message = "evaluation aggregate query is invalid"
        else:
            code = "invalid_execution_spec" if spec_is_object and has_spec_error else "invalid_request"
            message = "execution spec is invalid" if code == "invalid_execution_spec" else "request validation failed"
        return JSONResponse(status_code=422, content=V2ErrorEnvelope(error=V2Error(code=code, message=message, details={"fields": cast(JsonValue, fields)})).model_dump(mode="json"))

    application.add_exception_handler(RequestValidationError, validation_handler)
    router = APIRouter(prefix="/api/v2/executions", tags=["executions-v2"])

    def get_service(request: Request) -> AppExecutionService:
        return cast(AppExecutionService, request.app.state.v2_service)

    @router.post("", response_model=V2ExecutionEnvelope, status_code=status.HTTP_202_ACCEPTED)
    def create_execution(body: V2ExecutionCreate = Body(...), service: AppExecutionService = Depends(get_service)) -> V2ExecutionEnvelope:
        report = service.create(body.spec)
        return V2ExecutionEnvelope.from_report(report, service.specification(report.snapshot.execution_id))

    @router.get("", response_model=V2ExecutionPageEnvelope)
    def list_executions(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), lifecycle: ExecutionStatus | None = None, outcome: ExecutionOutcome | None = None, service: AppExecutionService = Depends(get_service)) -> V2ExecutionPageEnvelope:
        return V2ExecutionPageEnvelope.from_page(service.list(limit=limit, offset=offset, lifecycle=lifecycle, outcome=outcome))

    @router.get("/{execution_id}", response_model=V2ExecutionEnvelope)
    def get_execution(execution_id: str, service: AppExecutionService = Depends(get_service)) -> V2ExecutionEnvelope:
        report = service.get(execution_id)
        return V2ExecutionEnvelope.from_report(report, service.specification(report.snapshot.execution_id))

    @router.post("/{execution_id}/cancel", response_model=V2ExecutionEnvelope)
    def cancel_execution(execution_id: str, reason: str | None = None, service: AppExecutionService = Depends(get_service)) -> V2ExecutionEnvelope:
        report = service.cancel(execution_id, reason)
        return V2ExecutionEnvelope.from_report(report, service.specification(report.snapshot.execution_id))

    @router.delete("/{execution_id}", response_model=V2DeletedEnvelope)
    def delete_execution(execution_id: str, service: AppExecutionService = Depends(get_service)) -> V2DeletedEnvelope:
        return V2DeletedEnvelope(execution_id=service.delete(execution_id))

    @router.get("/{execution_id}/report", response_model=V2ExecutionReportEnvelope)
    def execution_report(execution_id: str, after_sequence: int = Query(-1, ge=-1), event_limit: int = Query(100, ge=1, le=1000), artifact_limit: int = Query(100, ge=1, le=1000), service: AppExecutionService = Depends(get_service)) -> V2ExecutionReportEnvelope:
        report = service.report(execution_id, after_sequence=after_sequence, event_limit=event_limit, artifact_limit=artifact_limit)
        trace = service.trace_view(execution_id)
        spec = service.specification(execution_id)
        return V2ExecutionReportEnvelope.from_values(report.snapshot.execution_id, spec, report, trace)

    application.include_router(router)
    aggregate_router = APIRouter(prefix="/api/v2/evaluations", tags=["evaluations-v2"])

    @aggregate_router.post("/aggregate", response_model=V2EvaluationAggregateEnvelope)
    def aggregate_evaluations(body: EvaluationQuery, service: AppExecutionService = Depends(get_service)) -> V2EvaluationAggregateEnvelope:
        return V2EvaluationAggregateEnvelope(aggregate=service.aggregate(body))

    application.include_router(aggregate_router)
    evidence_router = APIRouter(prefix="/api/v2/evidence", tags=["evidence-v2"])

    @evidence_router.post("/read", response_model=V2EvidenceEnvelope)
    def read_evidence(body: V2EvidenceRead, service: AppExecutionService = Depends(get_service)) -> V2EvidenceEnvelope:
        return V2EvidenceEnvelope(evidence=service.read_raw_evidence(body.reference, max_bytes=body.max_bytes))

    application.include_router(evidence_router)

    # FastAPI's field graph can lose the nested definitions of SDK models
    # that use postponed annotations (the generated component then contains
    # ``{}`` for ``TraceView.runtime``/``timeline``).  Refresh the public
    # contract components from the SDK's own JSON schema, preserving the
    # discriminator and oneOf metadata that clients need for typed parsing.
    original_openapi = application.openapi

    def openapi_with_sdk_models() -> dict[str, Any]:
        schema = original_openapi()
        components = schema.setdefault("components", {}).setdefault("schemas", {})

        def add_model(model: Any) -> None:
            model_schema = model.model_json_schema()
            definitions = model_schema.pop("$defs", {})
            for name, definition in definitions.items():
                components[name] = _component_refs(definition)
            components[model.__name__] = _component_refs(model_schema)

        for model in (TraceView, ExecutionReport, RawEvidence, EvidenceRef):
            add_model(model)
        # Use the stable public name for the evidence request reference;
        # FastAPI otherwise suffixes this input-only occurrence with
        # ``-Input`` even though it is the same SDK value model.
        evidence_request = components.get("V2EvidenceRead", {})
        if isinstance(evidence_request, dict):
            reference = evidence_request.get("properties", {}).get("reference")
            if isinstance(reference, dict) and "$ref" in reference:
                reference["$ref"] = "#/components/schemas/EvidenceRef"
        return schema

    application.openapi = openapi_with_sdk_models
    return service, owned_kit


def _component_refs(value: Any) -> Any:
    """Translate Pydantic's local ``$defs`` references to OpenAPI components."""

    if isinstance(value, dict):
        return {key: _component_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_component_refs(item) for item in value]
    if isinstance(value, str):
        return value.replace("#/$defs/", "#/components/schemas/")
    return value
