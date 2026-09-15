"""Versioned execution adapter backed exclusively by the public SDK APIs."""

# FastAPI dependencies and request markers are intentionally declared in
# function signatures so OpenAPI can infer their transport shape.
# ruff: noqa: B008

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, Literal, cast

from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from mcp_pal import (
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
from mcp_pal_app.services.app_service import AppRuntimeService
from mcp_pal_app.services.execution_service import (
    AppExecutionError,
    AppExecutionService,
)
from mcp_pal_app.services.profile_service import (
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
    spec: ExecutionSpec

    @classmethod
    def from_report(
        cls, report: ExecutionReport, spec: ExecutionSpec
    ) -> V2ExecutionEnvelope:
        return cls(
            execution_id=report.snapshot.execution_id,
            snapshot=report.snapshot,
            spec=spec,
        )


class V2ExecutionReportEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    execution_id: ExecutionId
    spec: ExecutionSpec
    report: ExecutionReport
    trace: TraceView

    @classmethod
    def from_values(
        cls,
        execution_id: ExecutionId,
        spec: ExecutionSpec,
        report: ExecutionReport,
        trace: TraceView,
    ) -> V2ExecutionReportEnvelope:
        return cls(execution_id=execution_id, spec=spec, report=report, trace=trace)


class V2ExecutionPageEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    page: ExecutionPage

    @classmethod
    def from_page(cls, page: ExecutionPage) -> V2ExecutionPageEnvelope:
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


class V2FeedbackEnvelope(BaseModel):
    version: Literal["v2"] = "v2"
    feedback: Feedback


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
        "elicitation_policy",
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
        "/harness-profiles/{profile_id}/export", response_model=dict[str, JsonValue]
    )
    def export_harness_profile(
        profile_id: str, runtime: AppRuntimeService = Depends(get_runtime)
    ) -> dict[str, JsonValue]:
        try:
            return cast(
                dict[str, JsonValue], json.loads(runtime.export_harness(profile_id))
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

    @router.post(
        "", response_model=V2ExecutionEnvelope, status_code=status.HTTP_202_ACCEPTED
    )
    def create_execution(
        body: V2ExecutionCreate = Body(...),
        service: AppExecutionService = Depends(get_service),
    ) -> V2ExecutionEnvelope:
        report = service.create(body.spec)
        return V2ExecutionEnvelope.from_report(
            report, service.specification(report.snapshot.execution_id)
        )

    @router.get("", response_model=V2ExecutionPageEnvelope)
    def list_executions(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        lifecycle: ExecutionStatus | None = None,
        outcome: ExecutionOutcome | None = None,
        service: AppExecutionService = Depends(get_service),
    ) -> V2ExecutionPageEnvelope:
        return V2ExecutionPageEnvelope.from_page(
            service.list(
                limit=limit, offset=offset, lifecycle=lifecycle, outcome=outcome
            )
        )

    @router.get("/{execution_id}", response_model=V2ExecutionEnvelope)
    def get_execution(
        execution_id: str,
        service: AppExecutionService = Depends(get_service),
    ) -> V2ExecutionEnvelope:
        report = service.get(execution_id)
        return V2ExecutionEnvelope.from_report(
            report, service.specification(report.snapshot.execution_id)
        )

    @router.post("/{execution_id}/cancel", response_model=V2ExecutionEnvelope)
    def cancel_execution(
        execution_id: str,
        reason: str | None = None,
        service: AppExecutionService = Depends(get_service),
    ) -> V2ExecutionEnvelope:
        report = service.cancel(execution_id, reason)
        return V2ExecutionEnvelope.from_report(
            report, service.specification(report.snapshot.execution_id)
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
        return V2ExecutionReportEnvelope.from_values(
            report.snapshot.execution_id, spec, report, trace
        )

    application.include_router(router)
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
