"""Versioned execution adapter backed exclusively by the public SDK APIs."""

# FastAPI dependencies and request markers are intentionally declared in
# function signatures so OpenAPI can infer their transport shape.
# ruff: noqa: B008

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Literal, cast

from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from m3 import (
    ACPProbeDimension,
    ACPProbeKind,
    ACPProbeRequest,
    ACPProbeResult,
    EvaluationQuery,
    EvaluationReport,
    EvidenceRef,
    ExecutionId,
    ExecutionOutcome,
    ExecutionPage,
    ExecutionReport,
    ExecutionSpec,
    ExecutionState,
    ExecutionStatus,
    Feedback,
    RawEvidence,
    TraceView,
)
from m3_app.api.report_payloads import build_execution_envelope, build_report_envelope
from m3_app.api.wire import (
    internalize_request,
    neutralize_openapi,
    neutralize_response,
)
from m3_app.services.app_service import AppRuntimeService
from m3_app.services.execution_service import (
    AppExecutionError,
    AppExecutionService,
)
from m3_app.services.profile_service import (
    HarnessProfileInput,
    MCPProfileInput,
    ProfileServiceError,
    ProfileView,
)


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
    spec: ExecutionSpec | None
    project_name: str | None = None

    @classmethod
    def from_report(
        cls,
        report: ExecutionReport,
        spec: ExecutionSpec | None,
        project_name: str | None = None,
    ) -> V2ExecutionEnvelope:
        return cls(
            execution_id=report.snapshot.execution_id,
            snapshot=report.snapshot,
            spec=spec,
            project_name=project_name,
        )


class V2TestResultSummary(BaseModel):
    """Pytest attempt summary associated with an execution."""

    attempt_id: str = Field(description="Persisted pytest attempt identity.")
    node_id: str = Field(description="Pytest node ID for the attempt.")
    description: str = Field(description="Test function docstring, if present.")
    outcome: str = Field(description="Raw persisted pytest outcome.")
    verdict: str = Field(
        description="Normalized pytest verdict independent of evaluations."
    )
    effective_verdict: str = Field(
        description="Verdict after required-evaluation and execution policy."
    )
    duration_seconds: float | None = Field(
        default=None, description="Persisted test duration in seconds, if available."
    )


class V2ExecutionReportEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    execution_id: ExecutionId
    spec: ExecutionSpec | None
    report: ExecutionReport
    trace: TraceView
    test_results: tuple[V2TestResultSummary, ...] = ()

    @classmethod
    def from_values(
        cls,
        execution_id: ExecutionId,
        spec: ExecutionSpec | None,
        report: ExecutionReport,
        trace: TraceView,
        test_results: tuple[V2TestResultSummary, ...] = (),
    ) -> V2ExecutionReportEnvelope:
        return cls(
            execution_id=execution_id,
            spec=spec,
            report=report,
            trace=trace,
            test_results=test_results,
        )


class V2ExecutionPageEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    page: ExecutionPage
    project_names: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_page(cls, page: ExecutionPage) -> V2ExecutionPageEnvelope:
        return cls(page=page)


class V2SuiteExecutionPageEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    suite: dict[str, int | str]
    page: ExecutionPage


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


class V2FeedbackEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    feedback: Feedback


class V2SuiteRef(BaseModel):
    suite_id: int = Field(description="Registered suite identity.")
    suite_name: str = Field(description="Registered suite display name.")
    project_id: str | None = Field(
        default=None, description="Project owning this suite, if registered."
    )


class V2RunSummary(BaseModel):
    """Safe, compact summary of a persisted pytest run manifest."""

    run_id: str = Field(description="Persisted pytest run identity.")
    created_at: str | None = Field(
        default=None, description="Run creation timestamp, if persisted."
    )
    finished_at: str | None = Field(
        default=None, description="Run completion timestamp, if persisted."
    )
    status: str | None = Field(default=None, description="Persisted run status.")
    project_id: str | None = Field(default=None, description="Project identity.")
    project_name: str | None = Field(default=None, description="Project display name.")
    test_count: int = Field(default=0, description="Number of collected pytest nodes.")
    test_outcome_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Safe counts of raw persisted pytest outcomes.",
    )
    effective_verdict_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Safe counts after required-evaluation policy.",
    )
    suites: tuple[V2SuiteRef, ...] = Field(
        default=(),
        description="Suites of this run's saved tests; a run can span several suites.",
    )


class V2RunListEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    runs: tuple[V2RunSummary, ...]
    total: int = Field(default=0, description="Runs matching the filters.")
    limit: int | None = Field(default=None, description="Page size; null means all.")
    offset: int = Field(default=0, description="Runs skipped before this page.")


RunGroup = Literal["suite_name", "suite_id", "date", "month", "project_id", "status"]


class V2RunGroup(BaseModel):
    key: dict[str, str | int | None]
    run_count: int = Field(description="Run memberships in this group on this page.")
    runs: tuple[V2RunSummary, ...]


class V2GroupedRunListEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    group: RunGroup
    groups: tuple[V2RunGroup, ...]
    total: int = Field(
        description="Distinct runs matching the filters before pagination."
    )
    limit: int = Field(description="Maximum distinct runs on this page.")
    offset: int = Field(description="Distinct runs skipped before this page.")


def _group_run_page(
    runs: tuple[V2RunSummary, ...], group: RunGroup
) -> tuple[V2RunGroup, ...]:
    """Group an already-selected run page; a multi-suite run has multiple memberships."""
    grouped: dict[tuple[tuple[str, str | int | None], ...], list[V2RunSummary]] = {}
    keys: dict[
        tuple[tuple[str, str | int | None], ...], dict[str, str | int | None]
    ] = {}
    for run in runs:
        group_keys: list[dict[str, str | int | None]]
        if group in {"suite_name", "suite_id"}:
            group_keys = [
                (
                    {"project_id": suite.project_id, "suite_name": suite.suite_name}
                    if group == "suite_name"
                    else {
                        "suite_id": suite.suite_id,
                        "suite_name": suite.suite_name,
                        "project_id": suite.project_id,
                    }
                )
                for suite in run.suites
            ] or [
                (
                    {"project_id": run.project_id, "suite_name": None}
                    if group == "suite_name"
                    else {
                        "suite_id": None,
                        "suite_name": None,
                        "project_id": run.project_id,
                    }
                )
            ]
        elif group in {"date", "month"}:
            utc_date: str | None = None
            if run.created_at:
                try:
                    date = datetime.fromisoformat(run.created_at.replace("Z", "+00:00"))
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    utc_date = date.astimezone(timezone.utc).date().isoformat()
                except (OverflowError, ValueError):
                    pass
            group_keys = [
                {group: utc_date[:7] if utc_date and group == "month" else utc_date}
            ]
        else:
            group_keys = [{group: getattr(run, group)}]
        seen: set[tuple[tuple[str, str | int | None], ...]] = set()
        for key in group_keys:
            identity = tuple(key.items())
            if identity in seen:
                continue
            seen.add(identity)
            keys[identity] = key
            grouped.setdefault(identity, []).append(run)
    return tuple(
        V2RunGroup(key=keys[identity], run_count=len(members), runs=tuple(members))
        for identity, members in grouped.items()
    )


class V2SuiteListEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    suites: tuple[V2SuiteRef, ...]


class V2DeletedEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    execution_id: ExecutionId
    deleted: Literal[True] = True


class V2Fault(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details: dict[str, JsonValue] = dict(details or {})
        super().__init__(message)


class V2ProfileMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4096)


class V2ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4096)
    mcp_json: dict[str, JsonValue]


class V2ProfileRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mcp_json: dict[str, JsonValue]


class V2HarnessCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4096)
    manifest: dict[str, JsonValue]
    trusted_unsandboxed: bool = False


class V2HarnessRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, JsonValue]
    trusted_unsandboxed: bool = False


class V2HarnessExportOut(BaseModel):
    """Portable harness profile export accepted by the import endpoint."""

    name: str
    description: str = ""
    manifest: dict[str, JsonValue]


class V2ProbeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    probe_type: ACPProbeKind = ACPProbeKind.PROTOCOL
    transport: str = "stdio"
    agent_mode_id: str | None = None
    session_config: dict[str, JsonValue] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, gt=0, le=3600)


class V2ProfileRevisionOut(BaseModel):
    id: str
    profile_id: str
    revision_number: int
    created_at: str
    value: dict[str, JsonValue]
    # Family-specific aliases keep the control-plane wire contract explicit
    # and make profile revisions directly consumable by existing clients.
    mcp_json: dict[str, JsonValue] | None = None
    manifest: dict[str, JsonValue] | None = None
    trusted_unsandboxed: bool | None = None


class V2ProfileOut(BaseModel):
    id: str
    kind: str
    name: str
    description: str
    archived: bool
    current_revision_id: str | None
    created_at: str
    updated_at: str
    revisions: tuple[V2ProfileRevisionOut, ...]


class V2ProbeEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    probe: ACPProbeResult


class V2ProbeHistoryEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    probes: tuple[ACPProbeResult, ...]


class V2StorageHealth(BaseModel):
    """Safe durable storage projection returned by readiness endpoints."""

    available: bool
    status: Literal["connected", "degraded"]
    reason: str | None = None


class V2HarnessCapability(BaseModel):
    """Common public fields for built-in and ACP harness readiness entries."""

    selection_id: str
    kind: str
    harness: str
    name: str
    ready: bool
    models: tuple[str, ...] = ()
    tool_modes: tuple[str, ...] = ()
    limits: dict[str, JsonValue] | None = None
    executable: bool | None = None
    required_flags_ok: bool | None = None
    missing_flags: tuple[str, ...] = ()
    missing_environment: tuple[str, ...] = ()
    credential_available: bool | None = None
    providers: tuple[str, ...] = ()
    profile_id: str | None = None
    revision_id: str | None = None
    local_ready: bool | None = None
    trusted_unsandboxed: bool | None = None
    archived: bool | None = None
    verification_status: str | None = None
    warnings: tuple[str, ...] = ()
    agent_modes: tuple[dict[str, JsonValue], ...] = ()
    session_config_options: tuple[dict[str, JsonValue], ...] = ()
    protocol_verified: bool | None = None
    full_verified: bool | None = None
    current_agent_mode_id: str | None = None
    agent_identity: dict[str, JsonValue] | None = None
    protocol_verification: dict[str, JsonValue] | None = None
    full_verifications: tuple[dict[str, JsonValue], ...] = ()


class V2CapabilitiesOut(BaseModel):
    version: Literal["v2"] = "v2"
    ready: bool
    run_ready: bool
    storage: V2StorageHealth
    harnesses: tuple[V2HarnessCapability, ...]


class V2ReadinessOut(V2CapabilitiesOut):
    pass


class V2HealthOut(BaseModel):
    version: Literal["v2"] = "v2"
    status: Literal["connected", "degraded"]
    ready: bool
    checks: dict[str, bool]
    reason: str | None = None


def _profile_out(view: ProfileView) -> V2ProfileOut:
    record = view.record
    revisions = tuple(
        V2ProfileRevisionOut(
            id=str(item.id.root),
            profile_id=item.profile_id,
            revision_number=item.revision_number,
            created_at=item.created_at.isoformat(),
            value=dict(item.value),
            mcp_json=(dict(item.value) if record.kind == "server" else None),
            manifest=(
                dict(item.value.get("manifest", {}))
                if record.kind == "harness"
                and isinstance(item.value.get("manifest"), Mapping)
                else None
            ),
            trusted_unsandboxed=(
                bool(item.value.get("trusted_unsandboxed"))
                if record.kind == "harness"
                else None
            ),
        )
        for item in view.revisions
    )
    return V2ProfileOut(
        id=record.id,
        kind=record.kind,
        name=record.name,
        description=record.description,
        archived=record.archived,
        current_revision_id=(
            str(record.current_revision_id.root)
            if record.current_revision_id is not None
            else None
        ),
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
        revisions=revisions,
    )


def _readiness_value(value: Any) -> dict[str, Any]:
    """Project readiness dataclasses without exposing service internals."""
    if hasattr(value, "__dataclass_fields__"):
        output = asdict(value)
    elif isinstance(value, Mapping):
        output = dict(value)
    else:
        output = {"value": value}
    return cast(dict[str, Any], _jsonable(output))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        return _jsonable(value.value)
    return value


_SAFE_VALIDATION_LOCATIONS = frozenset(
    {
        "body",
        "query",
        "path",
        "spec",
        "run_id",
        "kind",
        "direct",
        "agent",
        "servers",
        "server",
        "profile",
        "alias",
        "required",
        "operation",
        "harness",
        "harness_profile",
        "message",
        "protocol",
        "timeout_seconds",
        "goal",
        "evaluations",
        "artifact_policy",
        "declared_artifacts",
        "workspace",
        "tool_policy",
        "permission_policy",
        "sampling_policy",
        "filesystem_policy",
        "terminal_policy",
        "metadata",
        "validate_schemas",
        "reference",
        "max_bytes",
        "limit",
        "offset",
        "lifecycle",
        "outcome",
        "after_sequence",
        "event_limit",
        "artifact_limit",
        "execution_id",
        "from",
        "to",
        "group_by",
        "filters",
        "evaluator",
        "trial_id",
        "case_id",
        "transport",
        "model",
        "time",
    }
)


def _safe_validation_location(location: object) -> str:
    if not isinstance(location, (tuple, list)):
        return "body"
    parts = [
        str(item)
        if isinstance(item, int)
        else str(item)
        if str(item) in _SAFE_VALIDATION_LOCATIONS
        else "field"
        for item in location
    ]
    return ".".join(parts) or "body"


def _error_response(fault: V2Fault) -> JSONResponse:
    return JSONResponse(
        status_code=fault.status_code,
        content=V2ErrorEnvelope(
            error=V2Error(code=fault.code, message=fault.message, details=fault.details)
        ).model_dump(mode="json"),
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
        "run_data_unavailable": 500,
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
        "feedback_not_found": 404,
        "feedback_baseline_not_found": 404,
        "feedback_data_unavailable": 500,
        "profile_resolution_failed": 422,
    }
    resolution_reason = error.details.get("reason")
    if error.code == "profile_resolution_failed":
        if resolution_reason in {"missing", "missing_revision", "wrong_kind"}:
            status_code = 404
        elif resolution_reason == "archived":
            status_code = 409
        else:
            status_code = 422
    else:
        status_code = status_by_code.get(error.code, 500)
    return V2Fault(
        status_code,
        error.code,
        error.message,
        cast(Mapping[str, JsonValue], error.details),
    )


def install_v2(
    application: Any,
    runtime: AppRuntimeService,
    *,
    embedded_worker: bool = True,
) -> tuple[AppExecutionService, bool]:
    app_runtime = runtime
    store = app_runtime.store
    execution_kit = app_runtime.kit
    service = app_runtime.executions
    owned_kit = False
    application.state.v2_store = store
    application.state.v2_kit = execution_kit
    application.state.v2_service = service
    application.state.v2_kit_owned = owned_kit

    @application.middleware("http")  # type: ignore[untyped-decorator]
    async def v2_wire_boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Apply the explicit neutral API mapping around every v2 route."""

        is_v2 = request.url.path.startswith("/api/v2/")
        if is_v2 and request.method in {"POST", "PUT", "PATCH"}:
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    payload = json.loads((await request.body()).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = None
                if payload is not None:
                    request._body = json.dumps(
                        internalize_request(request.url.path, payload),
                        separators=(",", ":"),
                    ).encode("utf-8")

        response = await call_next(request)
        if not is_v2 or "application/json" not in response.headers.get(
            "content-type", ""
        ):
            return response
        body_iterator = cast(Any, response).body_iterator
        body = b"".join([chunk async for chunk in body_iterator])
        body_headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() != "content-length"
        }
        if not body:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=body_headers,
                background=response.background,
            )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Response(
                content=body,
                status_code=response.status_code,
                headers=body_headers,
                background=response.background,
            )
        headers = {
            key: value
            for key, value in body_headers.items()
            if key.lower() != "content-type"
        }
        return JSONResponse(
            status_code=response.status_code,
            content=neutralize_response(request.url.path, payload),
            headers=headers,
            background=response.background,
        )

    application.add_exception_handler(
        V2Fault, lambda _request, exc: _error_response(exc)
    )
    application.add_exception_handler(
        AppExecutionError, lambda _request, exc: _error_response(_service_fault(exc))
    )

    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
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
            message = (
                "field is required" if error_type == "missing" else "field is invalid"
            )
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
        has_spec_error = any(
            item["loc"] == "body.spec" or str(item["loc"]).startswith("body.spec.")
            for item in fields
        )
        if request.url.path == "/api/v2/evaluations/aggregate":
            code = "invalid_evaluation_aggregate_query"
            message = "evaluation aggregate query is invalid"
        else:
            code = (
                "invalid_execution_spec"
                if spec_is_object and has_spec_error
                else "invalid_request"
            )
            message = (
                "execution spec is invalid"
                if code == "invalid_execution_spec"
                else "request validation failed"
            )
        return JSONResponse(
            status_code=422,
            content=V2ErrorEnvelope(
                error=V2Error(
                    code=code,
                    message=message,
                    details={"fields": cast(JsonValue, fields)},
                )
            ).model_dump(mode="json"),
        )

    application.add_exception_handler(RequestValidationError, validation_handler)

    def get_runtime(request: Request) -> AppRuntimeService:
        value = getattr(request.app.state, "runtime", None)
        if not isinstance(value, AppRuntimeService):
            raise RuntimeError("application runtime is unavailable")
        return value

    def _profile_error(exc: ProfileServiceError) -> V2Fault:
        message = str(exc)
        lowered = message.lower()
        if "does not exist" in lowered or "missing" in lowered:
            return V2Fault(404, "profile_not_found", "profile was not found")
        if "cannot be modified" in lowered:
            return V2Fault(409, "profile_conflict", "profile cannot be modified")
        if "already exists" in lowered:
            return V2Fault(409, "profile_conflict", "profile already exists")
        return V2Fault(422, "invalid_profile", "profile request is invalid")

    # Application control plane. Each adapter maps typed service views to a
    # stable response schema; no SDK persistence rows or legacy ORM models
    # leak into the transport.
    profile_router = APIRouter(prefix="/api/v2", tags=["control-plane-v2"])

    @profile_router.get("/profiles", response_model=list[V2ProfileOut])
    def list_profiles(
        include_archived: bool = False,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> list[V2ProfileOut]:
        return [
            _profile_out(item)
            for item in runtime.list_mcp(include_archived=include_archived)
        ]

    @profile_router.post("/profiles", response_model=V2ProfileOut, status_code=201)
    def create_profile(
        body: V2ProfileCreate,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> V2ProfileOut:
        try:
            view = runtime.create_mcp(
                MCPProfileInput(
                    name=body.name, description=body.description, config=body.mcp_json
                )
            )
        except ProfileServiceError as exc:
            raise _profile_error(exc) from exc
        except ValueError as exc:
            raise V2Fault(422, "invalid_profile", "profile request is invalid") from exc
        return _profile_out(view)

    @profile_router.get("/profiles/{profile_id}", response_model=V2ProfileOut)
    def get_profile(
        profile_id: str, runtime: AppRuntimeService = Depends(get_runtime)
    ) -> V2ProfileOut:
        try:
            return _profile_out(runtime.get_mcp(profile_id))
        except ProfileServiceError as exc:
            raise _profile_error(exc) from exc

    @profile_router.patch("/profiles/{profile_id}", response_model=V2ProfileOut)
    def update_profile(
        profile_id: str,
        body: V2ProfileMetadata,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> V2ProfileOut:
        try:
            return _profile_out(
                runtime.update_mcp(
                    profile_id, name=body.name, description=body.description
                )
            )
        except ProfileServiceError as exc:
            raise _profile_error(exc) from exc

    @profile_router.post(
        "/profiles/{profile_id}/revisions", response_model=V2ProfileOut, status_code=201
    )
    def add_profile_revision(
        profile_id: str,
        body: V2ProfileRevisionCreate,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> V2ProfileOut:
        try:
            return _profile_out(runtime.add_mcp_revision(profile_id, body.mcp_json))
        except (ProfileServiceError, ValueError) as exc:
            raise _profile_error(ProfileServiceError(str(exc))) from exc

    @profile_router.post("/profiles/{profile_id}/archive", response_model=V2ProfileOut)
    def archive_profile(
        profile_id: str, runtime: AppRuntimeService = Depends(get_runtime)
    ) -> V2ProfileOut:
        try:
            return _profile_out(runtime.archive_mcp(profile_id))
        except ProfileServiceError as exc:
            raise _profile_error(exc) from exc

    @profile_router.post("/profiles/{profile_id}/restore", response_model=V2ProfileOut)
    def restore_profile(
        profile_id: str, runtime: AppRuntimeService = Depends(get_runtime)
    ) -> V2ProfileOut:
        try:
            return _profile_out(runtime.restore_mcp(profile_id))
        except ProfileServiceError as exc:
            raise _profile_error(exc) from exc

    @profile_router.get("/harness-profiles", response_model=list[V2ProfileOut])
    def list_harness_profiles(
        include_archived: bool = False,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> list[V2ProfileOut]:
        return [
            _profile_out(item)
            for item in runtime.list_harness(include_archived=include_archived)
        ]

    @profile_router.post(
        "/harness-profiles", response_model=V2ProfileOut, status_code=201
    )
    def create_harness_profile(
        body: V2HarnessCreate,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> V2ProfileOut:
        try:
            return _profile_out(
                runtime.create_harness(HarnessProfileInput(**body.model_dump()))
            )
        except (ProfileServiceError, ValueError) as exc:
            raise _profile_error(ProfileServiceError(str(exc))) from exc

    @profile_router.get("/harness-profiles/{profile_id}", response_model=V2ProfileOut)
    def get_harness_profile(
        profile_id: str, runtime: AppRuntimeService = Depends(get_runtime)
    ) -> V2ProfileOut:
        try:
            return _profile_out(runtime.get_harness(profile_id))
        except ProfileServiceError as exc:
            raise _profile_error(exc) from exc

    @profile_router.patch("/harness-profiles/{profile_id}", response_model=V2ProfileOut)
    def update_harness_profile(
        profile_id: str,
        body: V2ProfileMetadata,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> V2ProfileOut:
        try:
            return _profile_out(
                runtime.update_harness(
                    profile_id, name=body.name, description=body.description
                )
            )
        except ProfileServiceError as exc:
            raise _profile_error(exc) from exc

    @profile_router.post(
        "/harness-profiles/{profile_id}/revisions",
        response_model=V2ProfileOut,
        status_code=201,
    )
    def add_harness_revision(
        profile_id: str,
        body: V2HarnessRevisionCreate,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> V2ProfileOut:
        try:
            return _profile_out(
                runtime.add_harness_revision(
                    profile_id,
                    body.manifest,
                    trusted_unsandboxed=body.trusted_unsandboxed,
                )
            )
        except (ProfileServiceError, ValueError) as exc:
            raise _profile_error(ProfileServiceError(str(exc))) from exc

    @profile_router.post(
        "/harness-profiles/{profile_id}/archive", response_model=V2ProfileOut
    )
    def archive_harness_profile(
        profile_id: str, runtime: AppRuntimeService = Depends(get_runtime)
    ) -> V2ProfileOut:
        try:
            return _profile_out(runtime.archive_harness(profile_id))
        except ProfileServiceError as exc:
            raise _profile_error(exc) from exc

    @profile_router.post(
        "/harness-profiles/{profile_id}/restore", response_model=V2ProfileOut
    )
    def restore_harness_profile(
        profile_id: str, runtime: AppRuntimeService = Depends(get_runtime)
    ) -> V2ProfileOut:
        try:
            return _profile_out(runtime.restore_harness(profile_id))
        except ProfileServiceError as exc:
            raise _profile_error(exc) from exc

    @profile_router.get(
        "/harness-profiles/{profile_id}/export", response_model=V2HarnessExportOut
    )
    def export_harness_profile(
        profile_id: str, runtime: AppRuntimeService = Depends(get_runtime)
    ) -> V2HarnessExportOut:
        try:
            return V2HarnessExportOut.model_validate(
                json.loads(runtime.export_harness(profile_id))
            )
        except (ProfileServiceError, json.JSONDecodeError) as exc:
            if isinstance(exc, ProfileServiceError):
                raise _profile_error(exc) from exc
            raise V2Fault(
                500, "profile_export_invalid", "harness export is invalid"
            ) from exc

    @profile_router.post(
        "/harness-profiles/import", response_model=V2ProfileOut, status_code=201
    )
    def import_harness_profile(
        body: dict[str, JsonValue], runtime: AppRuntimeService = Depends(get_runtime)
    ) -> V2ProfileOut:
        try:
            return _profile_out(runtime.import_harness(body))
        except (ProfileServiceError, ValueError) as exc:
            raise _profile_error(ProfileServiceError(str(exc))) from exc

    @profile_router.get("/capabilities", response_model=dict[str, JsonValue])
    def capabilities(
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> dict[str, JsonValue]:
        snapshot = runtime.capabilities()
        harnesses = [_readiness_value(item) for item in snapshot.harnesses]
        return cast(
            dict[str, JsonValue],
            {
                "version": "v2",
                "ready": snapshot.ready,
                "run_ready": snapshot.run_ready,
                "storage": _readiness_value(snapshot.storage),
                "harnesses": harnesses,
            },
        )

    @profile_router.get("/readiness", response_model=dict[str, JsonValue])
    def readiness(
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> dict[str, JsonValue]:
        snapshot = runtime.readiness.capabilities()
        return cast(
            dict[str, JsonValue],
            {
                "version": "v2",
                "ready": snapshot.ready,
                "run_ready": snapshot.run_ready,
                "storage": _readiness_value(snapshot.storage),
                "harnesses": [_readiness_value(item) for item in snapshot.harnesses],
            },
        )

    @profile_router.get("/health", response_model=dict[str, JsonValue])
    def health(
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> dict[str, JsonValue]:
        storage = runtime.capabilities().storage
        return cast(
            dict[str, JsonValue],
            {
                "version": "v2",
                "status": storage.status,
                "ready": storage.ok,
                "checks": {"database": storage.ok},
                **({"reason": storage.reason} if storage.reason else {}),
            },
        )

    def _current_harness_revision(
        runtime: AppRuntimeService, profile_id: str, revision_id: str | None
    ) -> str:
        try:
            view = runtime.get_harness(profile_id)
        except ProfileServiceError as exc:
            raise _profile_error(exc) from exc
        selected = revision_id or (
            str(view.record.current_revision_id.root)
            if view.record.current_revision_id is not None
            else None
        )
        if not selected:
            raise V2Fault(
                422, "invalid_probe_request", "harness profile has no current revision"
            )
        if not any(str(item.id.root) == selected for item in view.revisions):
            raise V2Fault(
                422,
                "invalid_probe_request",
                "harness revision does not belong to profile",
            )
        return selected

    def _probe_request(
        runtime: AppRuntimeService,
        profile_id: str,
        body: V2ProbeCreate | None,
        revision_id: str | None,
        kind: ACPProbeKind | None,
        transport: str | None,
        mode_id: str | None,
        session_config: str | None,
    ) -> ACPProbeRequest:
        revision = _current_harness_revision(runtime, profile_id, revision_id)
        value = body or V2ProbeCreate()
        raw_config: Any = value.session_config
        if session_config is not None:
            try:
                raw_config = json.loads(session_config)
            except (TypeError, json.JSONDecodeError) as exc:
                raise V2Fault(
                    422, "invalid_probe_request", "session_config must be JSON"
                ) from exc
            if not isinstance(raw_config, dict):
                raise V2Fault(
                    422, "invalid_probe_request", "session_config must be a JSON object"
                )
        try:
            return ACPProbeRequest(
                profile_id=profile_id,
                revision_id=revision,
                probe_type=kind or value.probe_type,
                transport=transport or value.transport,
                agent_mode_id=mode_id if mode_id is not None else value.agent_mode_id,
                session_config=raw_config,
                timeout_seconds=value.timeout_seconds,
            )
        except ValueError as exc:
            raise V2Fault(
                422, "invalid_probe_request", "probe request is invalid"
            ) from exc

    @profile_router.get(
        "/harness-profiles/{profile_id}/probes",
        response_model=V2ProbeHistoryEnvelope,
    )
    def harness_probes(
        profile_id: str,
        revision_id: str | None = None,
        probe_type: ACPProbeKind = Query(ACPProbeKind.PROTOCOL, alias="kind"),
        transport: str = "stdio",
        agent_mode_id: str | None = Query(None, alias="mode_id"),
        session_config: str | None = None,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> V2ProbeHistoryEnvelope:
        revision = _current_harness_revision(runtime, profile_id, revision_id)
        raw_config: Any = {}
        if session_config is not None:
            try:
                raw_config = json.loads(session_config)
            except (TypeError, json.JSONDecodeError) as exc:
                raise V2Fault(
                    422, "invalid_probe_request", "session_config must be JSON"
                ) from exc
            if not isinstance(raw_config, dict):
                raise V2Fault(
                    422, "invalid_probe_request", "session_config must be a JSON object"
                )
        try:
            dimension = ACPProbeDimension(
                profile_id=profile_id,
                revision_id=revision,
                probe_type=probe_type,
                transport=transport,
                agent_mode_id=agent_mode_id,
                session_config=raw_config,
            )
            return V2ProbeHistoryEnvelope(
                probes=runtime.acp_probes.history(dimension).items
            )
        except ValueError as exc:
            raise V2Fault(
                422, "invalid_probe_request", "probe history query is invalid"
            ) from exc

    @profile_router.post(
        "/harness-profiles/{profile_id}/probes",
        response_model=V2ProbeEnvelope,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def request_probe(
        profile_id: str,
        body: V2ProbeCreate | None = Body(None),
        revision_id: str | None = None,
        kind: ACPProbeKind | None = Query(None),
        transport: str | None = None,
        mode_id: str | None = None,
        session_config: str | None = None,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> V2ProbeEnvelope:
        request = _probe_request(
            runtime,
            profile_id,
            body,
            revision_id,
            kind,
            transport,
            mode_id,
            session_config,
        )
        try:
            queued = runtime.acp_probes.start(request)
            return V2ProbeEnvelope(probe=queued)
        except ValueError as exc:
            raise V2Fault(
                422, "invalid_probe_request", "probe request is invalid"
            ) from exc

    @profile_router.post(
        "/harness-profiles/{profile_id}/probes/{probe_id}/cancel",
        response_model=V2ProbeEnvelope,
    )
    def cancel_probe(
        profile_id: str,
        probe_id: str,
        runtime: AppRuntimeService = Depends(get_runtime),
    ) -> V2ProbeEnvelope:
        result = runtime.acp_probes.cancel(probe_id, profile_id=profile_id)
        if result is None:
            raise V2Fault(404, "probe_not_found", "ACP probe was not found")
        return V2ProbeEnvelope(probe=result)

    application.include_router(profile_router)

    router = APIRouter(prefix="/api/v2/executions", tags=["executions-v2"])

    def get_service(request: Request) -> AppExecutionService:
        return cast(AppExecutionService, request.app.state.v2_service)

    runs_router = APIRouter(prefix="/api/v2/runs", tags=["runs-v2"])

    @runs_router.get("", response_model=V2RunListEnvelope | V2GroupedRunListEnvelope)
    def list_runs(
        limit: int | None = Query(None, ge=1, le=100),
        offset: int = Query(0, ge=0),
        suite_id: int | None = Query(None, description="filter by suite identity"),
        project_id: uuid.UUID | None = Query(
            None, description="filter by project identity"
        ),
        group: RunGroup | None = Query(
            None, description="group the selected run page by an allowed dimension"
        ),
        service: AppExecutionService = Depends(get_service),
    ) -> V2RunListEnvelope | V2GroupedRunListEnvelope:
        def optional_string(value: object) -> str | None:
            return value if isinstance(value, str) else None

        def safe_counts(value: object) -> dict[str, int]:
            if not isinstance(value, Mapping):
                return {}
            return {
                key: count
                for key, count in value.items()
                if isinstance(key, str) and key
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0
            }

        page_limit = limit if limit is not None else (50 if group else None)
        manifests, total = service.list_run_page(
            limit=page_limit,
            offset=offset,
            suite_id=suite_id,
            project_id=str(project_id) if project_id else None,
        )
        summaries: list[V2RunSummary] = []
        for manifest in manifests:
            run_id = manifest.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                continue
            collected = manifest.get("collected_node_ids")
            test_count = (
                len(collected)
                if isinstance(collected, (list, tuple))
                else manifest.get("collection_count", 0)
            )
            safe_test_count = (
                test_count
                if isinstance(test_count, int) and not isinstance(test_count, bool)
                else 0
            )
            summaries.append(
                V2RunSummary(
                    run_id=run_id,
                    created_at=optional_string(manifest.get("created_at")),
                    finished_at=optional_string(manifest.get("finished_at")),
                    status=optional_string(manifest.get("status")),
                    project_id=optional_string(manifest.get("project_id")),
                    project_name=optional_string(manifest.get("project_name")),
                    test_count=max(0, safe_test_count),
                    test_outcome_counts=safe_counts(
                        manifest.get("test_outcome_counts")
                    ),
                    effective_verdict_counts=safe_counts(
                        manifest.get("effective_verdict_counts")
                    ),
                    suites=tuple(
                        V2SuiteRef.model_validate(item)
                        for item in cast(list[object], manifest.get("suites") or [])
                    ),
                )
            )

        if group is not None:
            return V2GroupedRunListEnvelope(
                group=group,
                groups=_group_run_page(tuple(summaries), group),
                total=total,
                limit=cast(int, page_limit),
                offset=offset,
            )
        return V2RunListEnvelope(
            runs=tuple(summaries), total=total, limit=page_limit, offset=offset
        )

    application.include_router(runs_router)

    def project_name(
        service: AppExecutionService, report: ExecutionReport
    ) -> str | None:
        project = report.snapshot.project_id
        resolver = getattr(service.store, "get_project", None)
        if project is None or not callable(resolver):
            return None
        value = resolver(project.root)
        return value[1] if value is not None else None

    @router.post(
        "", response_model=V2ExecutionEnvelope, status_code=status.HTTP_202_ACCEPTED
    )
    def create_execution(
        body: V2ExecutionCreate = Body(...),
        service: AppExecutionService = Depends(get_service),
    ) -> V2ExecutionEnvelope:
        report = service.create(body.spec)
        return V2ExecutionEnvelope.model_validate(
            build_execution_envelope(
                report.snapshot,
                service.specification(report.snapshot.execution_id),
                project_name(service, report),
                public=False,
            )
        )

    @router.get("", response_model=V2ExecutionPageEnvelope)
    def list_executions(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        lifecycle: ExecutionStatus | None = None,
        outcome: ExecutionOutcome | None = None,
        project_id: uuid.UUID | None = Query(
            None, description="filter by project identity"
        ),
        service: AppExecutionService = Depends(get_service),
    ) -> V2ExecutionPageEnvelope:
        page = service.list(
            limit=limit,
            offset=offset,
            lifecycle=lifecycle,
            outcome=outcome,
            project_id=str(project_id) if project_id is not None else None,
        )
        get_project = getattr(service.store, "get_project", None)
        names: dict[str, str] = {}
        if callable(get_project):
            for snapshot in page.items:
                if snapshot.project_id is not None:
                    identifier = snapshot.project_id.root
                    if identifier not in names:
                        project = get_project(identifier)
                        if project is not None:
                            names[identifier] = project[1]
        return V2ExecutionPageEnvelope(page=page, project_names=names)

    def _list_suite_executions(
        suite_id: int,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        run_id: str | None = None,
        lifecycle: ExecutionStatus | None = None,
        outcome: ExecutionOutcome | None = None,
        service: AppExecutionService = Depends(get_service),
    ) -> V2SuiteExecutionPageEnvelope:
        suite = service.store.get_suite(suite_id)
        if suite is None:
            raise V2Fault(404, "suite_not_found", "suite was not found")
        page = service.store.list_executions(
            limit=limit,
            offset=offset,
            suite_id=suite_id,
            run_id=run_id,
            lifecycle=lifecycle,
            outcome=outcome,
        )
        return V2SuiteExecutionPageEnvelope(
            suite={
                "suite_id": suite.id.root,
                "suite_name": suite.name,
                **({"project_id": suite.project_id.root} if suite.project_id else {}),
            },
            page=page,
        )

    @router.get("/{execution_id}", response_model=V2ExecutionEnvelope)
    def get_execution(
        execution_id: str,
        service: AppExecutionService = Depends(get_service),
    ) -> V2ExecutionEnvelope:
        report = service.get(execution_id)
        return V2ExecutionEnvelope.model_validate(
            build_execution_envelope(
                report.snapshot,
                service.specification(report.snapshot.execution_id),
                project_name(service, report),
                public=False,
            )
        )

    @router.post("/{execution_id}/cancel", response_model=V2ExecutionEnvelope)
    def cancel_execution(
        execution_id: str,
        reason: str | None = None,
        service: AppExecutionService = Depends(get_service),
    ) -> V2ExecutionEnvelope:
        report = service.cancel(execution_id, reason)
        return V2ExecutionEnvelope.model_validate(
            build_execution_envelope(
                report.snapshot,
                service.specification(report.snapshot.execution_id),
                project_name(service, report),
                public=False,
            )
        )

    @router.delete("/{execution_id}", response_model=V2DeletedEnvelope)
    def delete_execution(
        execution_id: str,
        service: AppExecutionService = Depends(get_service),
    ) -> V2DeletedEnvelope:
        return V2DeletedEnvelope(execution_id=service.delete(execution_id))

    @router.get("/{execution_id}/report", response_model=V2ExecutionReportEnvelope)
    def execution_report(
        execution_id: str,
        after_sequence: int = Query(-1, ge=-1),
        event_limit: int = Query(100, ge=1, le=1000),
        artifact_limit: int = Query(100, ge=1, le=1000),
        service: AppExecutionService = Depends(get_service),
    ) -> V2ExecutionReportEnvelope:
        report = service.report(
            execution_id,
            after_sequence=after_sequence,
            event_limit=event_limit,
            artifact_limit=artifact_limit,
        )
        trace = service.trace_view(execution_id)
        spec = service.specification(execution_id)
        test_results = tuple(
            {
                "attempt_id": item.attempt_id,
                "node_id": item.node_id,
                "description": item.description,
                "outcome": item.outcome,
                "verdict": item.verdict,
                "effective_verdict": item.effective_verdict,
                "duration_seconds": item.duration_seconds,
            }
            for item in service.test_results(report)
        )
        return V2ExecutionReportEnvelope.model_validate(
            build_report_envelope(
                report.snapshot.execution_id,
                spec,
                report,
                trace,
                test_results,
                public=False,
            )
        )

    application.include_router(router)
    suite_router = APIRouter(prefix="/api/v2/suites", tags=["suites-v2"])

    @suite_router.get("", response_model=V2SuiteListEnvelope)
    def list_suites(
        service: AppExecutionService = Depends(get_service),
    ) -> V2SuiteListEnvelope:
        return V2SuiteListEnvelope(
            suites=tuple(
                V2SuiteRef(
                    suite_id=suite.id.root,
                    suite_name=suite.name,
                    project_id=suite.project_id.root if suite.project_id else None,
                )
                for suite in service.list_suites()
            )
        )

    suite_router.add_api_route(
        "/{suite_id}/executions",
        _list_suite_executions,
        methods=["GET"],
        response_model=V2SuiteExecutionPageEnvelope,
    )
    application.include_router(suite_router)
    aggregate_router = APIRouter(prefix="/api/v2/evaluations", tags=["evaluations-v2"])

    @aggregate_router.post("/aggregate", response_model=V2EvaluationAggregateEnvelope)
    def aggregate_evaluations(
        body: EvaluationQuery,
        service: AppExecutionService = Depends(get_service),
    ) -> V2EvaluationAggregateEnvelope:
        return V2EvaluationAggregateEnvelope(aggregate=service.aggregate(body))

    application.include_router(aggregate_router)
    feedback_router = APIRouter(prefix="/api/v2/feedback", tags=["feedback-v2"])

    @feedback_router.get("/{run_id}", response_model=V2FeedbackEnvelope)
    def get_feedback(
        run_id: str,
        baseline_run_id: str | None = Query(None),
        service: AppExecutionService = Depends(get_service),
    ) -> V2FeedbackEnvelope:
        return V2FeedbackEnvelope(
            feedback=service.feedback(run_id, baseline_run_id=baseline_run_id)
        )

    application.include_router(feedback_router)
    evidence_router = APIRouter(prefix="/api/v2/evidence", tags=["evidence-v2"])

    @evidence_router.post("/read", response_model=V2EvidenceEnvelope)
    def read_evidence(
        body: V2EvidenceRead,
        service: AppExecutionService = Depends(get_service),
    ) -> V2EvidenceEnvelope:
        return V2EvidenceEnvelope(
            evidence=service.read_raw_evidence(body.reference, max_bytes=body.max_bytes)
        )

    application.include_router(evidence_router)

    # FastAPI's field graph can lose the nested definitions of SDK models
    # that use postponed annotations (the generated component then contains
    # ``{}`` for ``TraceView.runtime``/``timeline``).  Refresh the public
    # contract components from the SDK's own JSON schema, preserving the
    # discriminator and oneOf metadata that clients need for typed parsing.
    original_openapi = application.openapi

    def openapi_with_sdk_models() -> dict[str, Any]:
        schema: dict[str, Any] = cast(dict[str, Any], original_openapi())
        components = schema.setdefault("components", {}).setdefault("schemas", {})

        def add_model(model: Any) -> None:
            model_schema = model.model_json_schema()
            definitions = model_schema.pop("$defs", {})
            for name, definition in definitions.items():
                components[name] = _component_refs(definition)
            components[model.__name__] = _component_refs(model_schema)

        for model in (TraceView, ExecutionReport, RawEvidence, EvidenceRef):
            add_model(model)
        for extra_model in (
            V2StorageHealth,
            V2HarnessCapability,
            V2CapabilitiesOut,
            V2ReadinessOut,
            V2HealthOut,
        ):
            add_model(extra_model)
        # Use the stable public name for the evidence request reference;
        # FastAPI otherwise suffixes this input-only occurrence with
        # ``-Input`` even though it is the same SDK value model.
        evidence_request = components.get("V2EvidenceRead", {})
        if isinstance(evidence_request, dict):
            reference = evidence_request.get("properties", {}).get("reference")
            if isinstance(reference, dict) and "$ref" in reference:
                reference["$ref"] = "#/components/schemas/EvidenceRef"

        # FastAPI can only infer the success model.  The v2 middleware and
        # handlers deliberately return a stable error envelope, so make that
        # contract explicit for generated clients and Swagger UI.
        error_ref = {"$ref": "#/components/schemas/V2ErrorEnvelope"}
        error_schema = V2ErrorEnvelope.model_json_schema()
        error_defs = error_schema.pop("$defs", {})
        for name, definition in error_defs.items():
            components.setdefault(name, _component_refs(definition))
        components.setdefault("V2ErrorEnvelope", _component_refs(error_schema))
        descriptions = {
            "/api/v2/profiles": "List or create MCP server profiles. List is read-only; creation stores an immutable revision.",
            "/api/v2/profiles/{profile_id}": "Read a profile or update only its display metadata. Profile revisions remain immutable.",
            "/api/v2/profiles/{profile_id}/revisions": "Create an immutable MCP JSON revision for an existing profile.",
            "/api/v2/profiles/{profile_id}/archive": "Archive a profile so it cannot be selected for new work.",
            "/api/v2/profiles/{profile_id}/restore": "Restore an archived MCP profile.",
            "/api/v2/harness-profiles": "List or create agent harness profiles and their immutable manifest revisions.",
            "/api/v2/harness-profiles/{profile_id}": "Read or update harness profile metadata.",
            "/api/v2/harness-profiles/{profile_id}/revisions": "Create an immutable harness manifest revision.",
            "/api/v2/harness-profiles/{profile_id}/archive": "Archive a harness profile.",
            "/api/v2/harness-profiles/{profile_id}/restore": "Restore an archived harness profile.",
            "/api/v2/harness-profiles/{profile_id}/export": "Export the stored harness profile payload for SDK or CLI import.",
            "/api/v2/harness-profiles/import": "Import a previously exported harness profile and create it as a new profile.",
            "/api/v2/capabilities": "Report storage and configured harness readiness. Credentials are represented only as availability flags.",
            "/api/v2/readiness": "Report whether storage and at least one configured harness are ready to run work.",
            "/api/v2/health": "Return the durable storage health status used by local liveness checks.",
            "/api/v2/harness-profiles/{profile_id}/probes": "Read exact-dimension probe history or submit a probe request. Submission is asynchronous and returns 202.",
            "/api/v2/harness-profiles/{profile_id}/probes/{probe_id}/cancel": "Cancel a queued or active harness probe.",
            "/api/v2/executions": "Submit an asynchronous direct or agent execution, or page through saved executions.",
            "/api/v2/executions/{execution_id}": "Read an execution snapshot or delete it after it reaches a terminal state.",
            "/api/v2/executions/{execution_id}/cancel": "Request cancellation of an active execution.",
            "/api/v2/executions/{execution_id}/report": "Read a terminal execution report, trace, test summaries, and bounded event or artifact pages.",
            "/api/v2/runs": "List safe, newest-first pytest run summaries with their suites, including runs with no executions or evaluations. Optional limit, offset, suite_id, project_id, and page-scoped group.",
            "/api/v2/suites": "List registered suites for run filters.",
            "/api/v2/suites/{suite_id}/executions": "Page through saved executions belonging to an integer suite ID.",
            "/api/v2/evaluations/aggregate": "Calculate a read-only aggregate from evaluation results already saved by the SDK or CLI.",
            "/api/v2/feedback/{run_id}": "Read saved feedback for a run and optionally compare it with a saved baseline run.",
            "/api/v2/evidence/read": "Read bounded evidence by reference; content may be redacted or truncated and integrity is checked.",
        }
        method_descriptions = {
            (
                "get",
                "/api/v2/profiles",
            ): "List MCP profiles; include_archived defaults to false.",
            (
                "post",
                "/api/v2/profiles",
            ): "Create an MCP profile and its first immutable revision. Duplicate names return profile_conflict.",
            (
                "get",
                "/api/v2/harness-profiles",
            ): "List harness profiles; include_archived defaults to false.",
            (
                "post",
                "/api/v2/harness-profiles",
            ): "Create a harness profile. trusted_unsandboxed must be true to acknowledge local execution.",
            (
                "post",
                "/api/v2/harness-profiles/import",
            ): "Import either an exported {name, description, manifest} wrapper or a bare manifest. Imports are stored untrusted.",
            (
                "get",
                "/api/v2/harness-profiles/{profile_id}/probes",
            ): "Read exact-dimension probe history using revision_id, kind, transport, mode_id, and URL-encoded session_config.",
            (
                "post",
                "/api/v2/harness-profiles/{profile_id}/probes",
            ): "Queue a probe and return 202. Query kind, transport, and mode_id override matching body values; revision_id selects the current or explicit revision.",
            (
                "post",
                "/api/v2/executions",
            ): "Submit a JSON direct or agent ExecutionSpec and return 202; poll the returned execution_id before reading its report.",
            (
                "get",
                "/api/v2/executions",
            ): "Page saved executions with limit 1-100, offset >=0, and lifecycle, outcome, or project_id filters.",
            (
                "get",
                "/api/v2/executions/{execution_id}",
            ): "Read the current asynchronous execution snapshot.",
            (
                "delete",
                "/api/v2/executions/{execution_id}",
            ): "Delete an execution only after it is terminal; active executions return execution_active.",
        }
        examples = {
            "/api/v2/evaluations/aggregate": {
                "summary": "Group saved evaluations by run and case",
                "value": {
                    "group_by": ["run_id", "case_id"],
                    "filters": {"evaluator": ["quality.v1"]},
                    "limit": 100,
                    "offset": 0,
                },
            },
            "/api/v2/evidence/read": {
                "summary": "Read a bounded evidence reference",
                "value": {
                    "reference": {
                        "evidence_id": "evidence-example-1",
                        "sha256": "a" * 64,
                    },
                    "max_bytes": 65536,
                },
            },
            "/api/v2/executions": {
                "summary": "Submit a direct execution",
                "value": {
                    "spec": {
                        "kind": "direct",
                        "servers": [
                            {
                                "server": {
                                    "kind": "streamable_http",
                                    "name": "example",
                                    "url": "https://example.invalid/mcp",
                                }
                            }
                        ],
                        "operation": {
                            "kind": "call_tool",
                            "server": "example",
                            "name": "echo",
                            "arguments": {"message": "hello"},
                        },
                    }
                },
            },
            "/api/v2/profiles": {
                "summary": "Create an MCP profile",
                "value": {
                    "name": "example-server",
                    "description": "Safe example",
                    "mcp_json": {
                        "mcpServers": {
                            "example": {
                                "type": "http",
                                "url": "https://example.invalid/mcp",
                            }
                        }
                    },
                },
            },
            "/api/v2/profiles/{profile_id}/revisions": {
                "summary": "Create an MCP profile revision",
                "value": {
                    "mcp_json": {
                        "mcpServers": {
                            "example": {
                                "type": "http",
                                "url": "https://example.invalid/mcp",
                            }
                        }
                    }
                },
            },
            "/api/v2/harness-profiles": {
                "summary": "Create a harness profile",
                "value": {
                    "name": "example-acp",
                    "description": "Safe example; trusted_unsandboxed acknowledges local execution",
                    "manifest": {
                        "schema_version": "m3.harness.v1",
                        "protocol": "acp",
                        "protocol_version": 1,
                        "command": "example-agent",
                        "args": [],
                        "env": {"EXAMPLE_TOKEN": "${EXAMPLE_TOKEN}"},
                    },
                    "trusted_unsandboxed": True,
                },
            },
            "/api/v2/harness-profiles/{profile_id}/revisions": {
                "summary": "Create a harness revision",
                "value": {
                    "manifest": {
                        "schema_version": "m3.harness.v1",
                        "protocol": "acp",
                        "protocol_version": 1,
                        "command": "example-agent",
                        "args": [],
                        "env": {"EXAMPLE_TOKEN": "${EXAMPLE_TOKEN}"},
                    },
                    "trusted_unsandboxed": True,
                },
            },
            "/api/v2/harness-profiles/{profile_id}/probes": {
                "summary": "Submit a protocol probe",
                "value": {
                    "probe_type": "protocol",
                    "transport": "stdio",
                    "session_config": {},
                    "timeout_seconds": 30,
                },
            },
            "/api/v2/harness-profiles/import": {
                "summary": "Import an exported harness profile",
                "value": {
                    "name": "imported-acp",
                    "description": "Safe example",
                    "manifest": {
                        "schema_version": "m3.harness.v1",
                        "protocol": "acp",
                        "protocol_version": 1,
                        "command": "example-agent",
                        "args": [],
                        "env": {},
                    },
                },
            },
        }
        not_found_operations = {
            ("get", "/api/v2/profiles/{profile_id}"),
            ("patch", "/api/v2/profiles/{profile_id}"),
            ("post", "/api/v2/profiles/{profile_id}/revisions"),
            ("post", "/api/v2/profiles/{profile_id}/archive"),
            ("post", "/api/v2/profiles/{profile_id}/restore"),
            ("get", "/api/v2/harness-profiles/{profile_id}"),
            ("patch", "/api/v2/harness-profiles/{profile_id}"),
            ("post", "/api/v2/harness-profiles/{profile_id}/revisions"),
            ("post", "/api/v2/harness-profiles/{profile_id}/archive"),
            ("post", "/api/v2/harness-profiles/{profile_id}/restore"),
            ("get", "/api/v2/harness-profiles/{profile_id}/export"),
            ("get", "/api/v2/harness-profiles/{profile_id}/probes"),
            ("post", "/api/v2/harness-profiles/{profile_id}/probes"),
            ("post", "/api/v2/harness-profiles/{profile_id}/probes/{probe_id}/cancel"),
            ("post", "/api/v2/executions"),
            ("get", "/api/v2/executions/{execution_id}"),
            ("delete", "/api/v2/executions/{execution_id}"),
            ("post", "/api/v2/executions/{execution_id}/cancel"),
            ("get", "/api/v2/executions/{execution_id}/report"),
            ("get", "/api/v2/runs"),
            ("get", "/api/v2/suites/{suite_id}/executions"),
            ("get", "/api/v2/feedback/{run_id}"),
            ("post", "/api/v2/evidence/read"),
        }
        conflict_operations = {
            ("post", "/api/v2/profiles"),
            ("patch", "/api/v2/profiles/{profile_id}"),
            ("post", "/api/v2/profiles/{profile_id}/revisions"),
            ("post", "/api/v2/harness-profiles"),
            ("patch", "/api/v2/harness-profiles/{profile_id}"),
            ("post", "/api/v2/harness-profiles/{profile_id}/revisions"),
            ("post", "/api/v2/harness-profiles/import"),
            ("post", "/api/v2/executions"),
            ("delete", "/api/v2/executions/{execution_id}"),
            ("post", "/api/v2/executions/{execution_id}/cancel"),
            ("get", "/api/v2/executions/{execution_id}/report"),
        }
        internal_error_operations = {
            ("post", "/api/v2/executions"),
            ("get", "/api/v2/executions/{execution_id}"),
            ("get", "/api/v2/executions/{execution_id}/report"),
            ("get", "/api/v2/runs"),
            ("get", "/api/v2/suites"),
            ("post", "/api/v2/evaluations/aggregate"),
            ("get", "/api/v2/feedback/{run_id}"),
            ("post", "/api/v2/evidence/read"),
            ("get", "/api/v2/harness-profiles/{profile_id}/export"),
        }
        validation_operations = {
            ("get", "/api/v2/profiles"),
            ("post", "/api/v2/profiles"),
            ("patch", "/api/v2/profiles/{profile_id}"),
            ("post", "/api/v2/profiles/{profile_id}/revisions"),
            ("post", "/api/v2/harness-profiles"),
            ("patch", "/api/v2/harness-profiles/{profile_id}"),
            ("post", "/api/v2/harness-profiles/{profile_id}/revisions"),
            ("post", "/api/v2/harness-profiles/import"),
            ("get", "/api/v2/harness-profiles/{profile_id}/probes"),
            ("post", "/api/v2/harness-profiles/{profile_id}/probes"),
            ("post", "/api/v2/executions"),
            ("get", "/api/v2/executions"),
            ("get", "/api/v2/executions/{execution_id}"),
            ("delete", "/api/v2/executions/{execution_id}"),
            ("post", "/api/v2/executions/{execution_id}/cancel"),
            ("get", "/api/v2/executions/{execution_id}/report"),
            ("get", "/api/v2/runs"),
            ("get", "/api/v2/suites/{suite_id}/executions"),
            ("post", "/api/v2/evaluations/aggregate"),
            ("post", "/api/v2/evidence/read"),
        }
        for path, path_item in list(schema.get("paths", {}).items()):
            if not isinstance(path_item, dict):
                continue
            if getattr(application.state, "viewer_read_only", False):
                allowed = {"get", "head", "options"}
                if path in {"/api/v2/evidence/read", "/api/v2/evaluations/aggregate"}:
                    allowed.add("post")
                for method in list(path_item):
                    if method.lower() not in allowed:
                        del path_item[method]
                if not any(
                    str(key).lower() in {"get", "post", "head", "options"}
                    for key in path_item
                ):
                    del schema["paths"][path]
                    continue
            for method, operation in path_item.items():
                if method.lower() not in {
                    "get",
                    "post",
                    "patch",
                    "delete",
                    "put",
                } or not isinstance(operation, dict):
                    continue
                operation.setdefault("summary", f"{method.upper()} {path}")
                operation["description"] = (
                    method_descriptions.get((method.lower(), path))
                    or descriptions.get(path)
                    or operation.get("description")
                    or ""
                ).strip()
                responses = operation.setdefault("responses", {})
                if path == "/api/v2/capabilities" and method.lower() == "get":
                    responses.setdefault("200", {}).setdefault(
                        "content", {}
                    ).setdefault("application/json", {})["schema"] = {
                        "$ref": "#/components/schemas/V2CapabilitiesOut"
                    }
                elif path == "/api/v2/readiness" and method.lower() == "get":
                    responses.setdefault("200", {}).setdefault(
                        "content", {}
                    ).setdefault("application/json", {})["schema"] = {
                        "$ref": "#/components/schemas/V2ReadinessOut"
                    }
                elif path == "/api/v2/health" and method.lower() == "get":
                    responses.setdefault("200", {}).setdefault(
                        "content", {}
                    ).setdefault("application/json", {})["schema"] = {
                        "$ref": "#/components/schemas/V2HealthOut"
                    }
                # Route validation is normalized by the v2 exception handler.
                if (method.lower(), path) in validation_operations:
                    responses["422"] = {
                        "description": "Invalid request or domain input.",
                        "content": {"application/json": {"schema": error_ref}},
                    }
                else:
                    responses.pop("422", None)
                if (method.lower(), path) in not_found_operations:
                    responses.setdefault(
                        "404",
                        {
                            "description": "Resource not found (for example profile_not_found, execution_not_found, suite_not_found, probe_not_found, or feedback_not_found).",
                            "content": {"application/json": {"schema": error_ref}},
                        },
                    )
                if (method.lower(), path) in conflict_operations:
                    responses.setdefault(
                        "409",
                        {
                            "description": "State conflict (for example profile_conflict, execution_active, execution_terminal, execution_not_terminal, or cancellation_conflict).",
                            "content": {"application/json": {"schema": error_ref}},
                        },
                    )
                if (method.lower(), path) in internal_error_operations:
                    responses.setdefault(
                        "500",
                        {
                            "description": "Mapped durable-data failure such as execution_data_unavailable, trace_unavailable, evaluation_data_unavailable, feedback_data_unavailable, or raw_evidence_integrity_error.",
                            "content": {"application/json": {"schema": error_ref}},
                        },
                    )
                if path in examples and "requestBody" in operation:
                    request_examples = (
                        operation["requestBody"]
                        .setdefault("content", {})
                        .setdefault("application/json", {})
                        .setdefault("examples", {})
                    )
                    request_examples["safe"] = examples[path]
                    if path == "/api/v2/executions" and method.lower() == "post":
                        request_examples["agent"] = {
                            "summary": "Submit an ACP agent execution",
                            "value": {
                                "spec": {
                                    "kind": "agent",
                                    "servers": [
                                        {
                                            "server": {
                                                "kind": "streamable_http",
                                                "name": "example",
                                                "url": "https://example.invalid/mcp",
                                            }
                                        }
                                    ],
                                    "harness": {
                                        "kind": "acp",
                                        "model": "example-model",
                                        "manifest": {
                                            "schema_version": "m3.harness.v1",
                                            "protocol": "acp",
                                            "protocol_version": 1,
                                            "command": "example-agent",
                                            "args": [],
                                            "env": {},
                                        },
                                    },
                                    "message": {
                                        "content": [
                                            {
                                                "kind": "text",
                                                "text": "Run the safe example task.",
                                            }
                                        ]
                                    },
                                }
                            },
                        }
                if (
                    path == "/api/v2/executions/{execution_id}/report"
                    and method.lower() == "get"
                ):
                    for parameter in operation.get("parameters", []):
                        if parameter.get("name") == "after_sequence":
                            parameter["example"] = 120
                        elif parameter.get("name") in {"event_limit", "artifact_limit"}:
                            parameter["example"] = 100
                if path == "/api/v2/feedback/{run_id}" and method.lower() == "get":
                    for parameter in operation.get("parameters", []):
                        if parameter.get("name") == "baseline_run_id":
                            parameter["example"] = "run-baseline"
        return neutralize_openapi(schema)

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
