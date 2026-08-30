"""Typed, immutable observability values.

This module is deliberately a value-model boundary.  It does not know how a
trace is captured or persisted; later layers project canonical events into
these models.  Keeping the projection separate makes the public contract
usable by direct clients, harness adapters, matchers, and the UI alike.
"""

from __future__ import annotations

from collections.abc import Mapping as _Mapping
from datetime import datetime as _datetime
from datetime import timezone as _timezone
from enum import Enum as _Enum
from math import isfinite as _isfinite
from typing import Annotated as _Annotated
from typing import Any as _Any
from typing import Generic as _Generic
from typing import Literal as _Literal
from typing import TypeAlias as _TypeAlias
from typing import TypeVar as _TypeVar
from typing import cast as _cast

from pydantic import Field as _Field
from pydantic import JsonValue as _JsonValue
from pydantic import field_validator as _field_validator
from pydantic import model_validator as _model_validator

from .policy import ToolPolicyDecision as _ToolPolicyDecision
from .types import (
    ActivityHealth as _ActivityHealth,
)
from .types import (
    ArtifactRef as _ArtifactRef,
)
from .types import (
    ConnectionId as _ConnectionId,
)
from .types import (
    ContentBlock as _ContentBlock,
)
from .types import (
    DirectPrompt as _DirectPrompt,
)
from .types import (
    DirectResource as _DirectResource,
)
from .types import (
    DirectResourceTemplate as _DirectResourceTemplate,
)
from .types import (
    DirectTool as _DirectTool,
)
from .types import (
    ErrorInfo as _ErrorInfo,
)
from .types import (
    EvaluationResult as _EvaluationResult,
)
from .types import (
    EventDirection as _EventDirection,
)
from .types import (
    EventProvenance as _EventProvenance,
)
from .types import (
    ExecutionId as _ExecutionId,
)
from .types import (
    ExecutionOutcome as _ExecutionOutcome,
)
from .types import (
    FrozenModel as _FrozenModel,
)
from .types import (
    JsonRpcId as _JsonRpcId,
)
from .types import (
    RawEvidenceRef as _RawEvidenceRef,
)
from .types import (
    SessionId as _SessionId,
)
from .types import (
    TraceId as _TraceId,
)
from .types import (
    TransportKind as _TransportKind,
)
from .types import (
    TurnId as _TurnId,
)
from .types import (
    TurnResult as _TurnResult,
)
from .types import (
    TurnSnapshot as _TurnSnapshot,
)


class ObservationState(str, _Enum):
    """How completely a provider-dependent value was observed."""

    OBSERVED = "observed"
    NOT_EMITTED = "not_emitted"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    PROVIDER_HIDDEN = "provider_hidden"
    ENCRYPTED = "encrypted"
    REDACTED = "redacted"
    TRUNCATED = "truncated"


class ObservationReason(str, _Enum):
    PROVIDER_DID_NOT_EMIT = "provider_did_not_emit"
    PROVIDER_HIDDEN = "provider_hidden"
    PROVIDER_ENCRYPTED = "provider_encrypted"
    HARNESS_UNSUPPORTED = "harness_unsupported"
    TRANSPORT_NOT_APPLICABLE = "transport_not_applicable"
    CAPTURE_DISABLED = "capture_disabled"
    CAPTURE_FAILED = "capture_failed"
    EVIDENCE_TRUNCATED = "evidence_truncated"
    REDACTED_BY_POLICY = "redacted_by_policy"
    CORRELATION_UNAVAILABLE = "correlation_unavailable"
    MALFORMED_SOURCE = "malformed_source"


_T = _TypeVar("_T")


class Observation(_FrozenModel, _Generic[_T]):
    """A typed value with explicit availability and provenance."""

    state: ObservationState
    value: _T | None = None
    reason: ObservationReason | None = None
    provenance: tuple[_EventProvenance, ...] = ()
    evidence_ref: _RawEvidenceRef | None = None

    @_field_validator("value", mode="before")
    @classmethod
    def _accept_frozen_json(cls, value: _Any) -> _Any:
        """Allow an observation to be reused across generic model types.

        ``FrozenModel`` stores mappings as immutable mapping views.  Pydantic's
        strict ``JsonValue`` validator quite correctly rejects those views,
        so thaw only at the validation boundary; the resulting model is
        frozen again by the base model.
        """

        if isinstance(value, _Mapping):
            return {key: cls._accept_frozen_json(item) for key, item in value.items()}
        if isinstance(value, (tuple, frozenset)):
            return [cls._accept_frozen_json(item) for item in value]
        if isinstance(value, list):
            return [cls._accept_frozen_json(item) for item in value]
        return value

    @_model_validator(mode="after")
    def _validate_state(self) -> Observation[_T]:
        allowed_reasons: dict[ObservationState, frozenset[ObservationReason]] = {
            ObservationState.NOT_EMITTED: frozenset(
                {ObservationReason.PROVIDER_DID_NOT_EMIT}
            ),
            ObservationState.PROVIDER_HIDDEN: frozenset(
                {ObservationReason.PROVIDER_HIDDEN}
            ),
            ObservationState.ENCRYPTED: frozenset(
                {ObservationReason.PROVIDER_ENCRYPTED}
            ),
            ObservationState.REDACTED: frozenset(
                {ObservationReason.REDACTED_BY_POLICY}
            ),
            ObservationState.TRUNCATED: frozenset(
                {ObservationReason.EVIDENCE_TRUNCATED}
            ),
            ObservationState.UNSUPPORTED: frozenset(
                {
                    ObservationReason.HARNESS_UNSUPPORTED,
                    ObservationReason.TRANSPORT_NOT_APPLICABLE,
                }
            ),
            ObservationState.UNAVAILABLE: frozenset(
                {
                    ObservationReason.CAPTURE_DISABLED,
                    ObservationReason.CAPTURE_FAILED,
                    ObservationReason.CORRELATION_UNAVAILABLE,
                    ObservationReason.MALFORMED_SOURCE,
                }
            ),
        }
        if (
            self.state is ObservationState.OBSERVED
            and "value" not in self.model_fields_set
        ):
            raise ValueError("observed values require value")
        if self.state is ObservationState.OBSERVED:
            if self.reason is not None:
                raise ValueError("observed values cannot carry an availability reason")
        elif self.reason not in allowed_reasons[self.state]:
            raise ValueError(
                f"reason {self.reason!r} is invalid for {self.state.value}"
            )
        if (
            self.state
            in {
                ObservationState.NOT_EMITTED,
                ObservationState.UNSUPPORTED,
                ObservationState.UNAVAILABLE,
                ObservationState.PROVIDER_HIDDEN,
                ObservationState.ENCRYPTED,
            }
            and self.value is not None
        ):
            raise ValueError(f"{self.state.value} values cannot contain value")
        return self


def _not_emitted() -> Observation[_Any]:
    return Observation(
        state=ObservationState.NOT_EMITTED,
        reason=ObservationReason.PROVIDER_DID_NOT_EMIT,
    )


def _unavailable(
    reason: ObservationReason = ObservationReason.CAPTURE_FAILED,
) -> Observation[_Any]:
    return Observation(state=ObservationState.UNAVAILABLE, reason=reason)


class TraceStatus(str, _Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TOOL_ERROR = "tool_error"
    PROTOCOL_ERROR = "protocol_error"
    TRANSPORT_ERROR = "transport_error"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"


class TraceTiming(_FrozenModel):
    started_at: _datetime = _Field(default_factory=lambda: _datetime.now(_timezone.utc))
    finished_at: _datetime | None = None
    start_offset_ms: float = _Field(default=0, ge=0)
    end_offset_ms: float = _Field(default=0, ge=0)
    duration_ms: float = _Field(default=0, ge=0)

    @_field_validator("started_at", "finished_at")
    @classmethod
    def _utc(cls, value: _datetime | None) -> _datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("trace timestamps must be timezone-aware")
            return value.astimezone(_timezone.utc)
        return value

    @_field_validator("start_offset_ms", "end_offset_ms", "duration_ms")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not _isfinite(value):
            raise ValueError("trace timing values must be finite")
        return value

    @_model_validator(mode="after")
    def _consistent(self) -> TraceTiming:
        if self.end_offset_ms < self.start_offset_ms:
            raise ValueError("trace end offset cannot precede start offset")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("trace finished_at cannot precede started_at")
        expected = self.end_offset_ms - self.start_offset_ms
        if abs(self.duration_ms - expected) > 1e-6:
            raise ValueError("trace duration must equal end offset minus start offset")
        return self


class TraceEntryBase(_FrozenModel):
    entry_id: str = _Field(min_length=1, max_length=256)
    kind: str = _Field(min_length=1, max_length=64)
    parent_id: str | None = None
    execution_id: _ExecutionId
    session_id: _SessionId | None = None
    turn_id: _TurnId | None = None
    server_binding: str | None = None
    connection_id: _ConnectionId | None = None
    sequence_start: int = _Field(ge=0)
    sequence_end: int = _Field(ge=0)
    timing: TraceTiming = _Field(default_factory=TraceTiming)
    status: TraceStatus = TraceStatus.COMPLETED
    provenance: tuple[_EventProvenance, ...] = ()
    limitations: tuple[str, ...] = ()

    @_model_validator(mode="after")
    def _sequence_range(self) -> TraceEntryBase:
        if self.sequence_end < self.sequence_start:
            raise ValueError("entry sequence_end cannot precede sequence_start")
        return self


class MessageRole(str, _Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessageEntry(TraceEntryBase):
    kind: _Literal["message"] = "message"
    message_id: Observation[str] = _Field(default_factory=_not_emitted)
    role: MessageRole = MessageRole.ASSISTANT
    content: tuple[_ContentBlock, ...] = ()
    stop_reason: Observation[str] = _Field(default_factory=_not_emitted)


class ReasoningEntry(TraceEntryBase):
    kind: _Literal["reasoning"] = "reasoning"
    block_id: Observation[str] = _Field(default_factory=_not_emitted)
    content: Observation[tuple[_ContentBlock, ...]] = _Field(
        default_factory=_not_emitted
    )


class ToolCallStatus(str, _Enum):
    SUCCESS = "success"
    TOOL_ERROR = "tool_error"
    PROTOCOL_ERROR = "protocol_error"
    TRANSPORT_ERROR = "transport_error"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INCOMPLETE = "incomplete"


class CorrelationState(str, _Enum):
    CORRELATED = "correlated"
    REPORTED_ONLY = "reported_only"
    WIRE_ONLY = "wire_only"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


class ToolResult(_FrozenModel):
    content: tuple[_ContentBlock, ...] = ()
    structured_content: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    is_error: bool = False
    error: Observation[_ErrorInfo] = _Field(default_factory=_not_emitted)


class ReportedToolCall(_FrozenModel):
    provider_call_id: Observation[str] = _Field(default_factory=_not_emitted)
    server: Observation[str] = _Field(default_factory=_not_emitted)
    tool: Observation[str] = _Field(default_factory=_not_emitted)
    arguments: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    result: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    status: Observation[str] = _Field(default_factory=_not_emitted)


class WireToolCall(_FrozenModel):
    jsonrpc_id: Observation[_JsonRpcId] = _Field(default_factory=_not_emitted)
    server: Observation[str] = _Field(default_factory=_not_emitted)
    tool: Observation[str] = _Field(default_factory=_not_emitted)
    arguments: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    result: Observation[ToolResult] = _Field(default_factory=_not_emitted)
    latency_ms: Observation[float] = _Field(default_factory=_not_emitted)


class EvidenceConflict(_FrozenModel):
    field: _Literal["server", "tool", "arguments", "result", "status"]
    reported: Observation[_JsonValue]
    wire: Observation[_JsonValue]


class ToolCallEntry(TraceEntryBase):
    kind: _Literal["tool_call"] = "tool_call"
    call_id: str = _Field(min_length=1, max_length=256)
    provider_call_id: Observation[str] = _Field(default_factory=_not_emitted)
    server: Observation[str] = _Field(default_factory=_not_emitted)
    tool: Observation[str] = _Field(default_factory=_not_emitted)
    arguments: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    result: Observation[ToolResult] = _Field(default_factory=_not_emitted)
    tool_status: ToolCallStatus = ToolCallStatus.INCOMPLETE
    correlation: CorrelationState = CorrelationState.UNAVAILABLE
    jsonrpc_id: Observation[_JsonRpcId] = _Field(default_factory=_not_emitted)
    server_latency_ms: Observation[float] = _Field(default_factory=_not_emitted)
    policy: Observation[_ToolPolicyDecision] = _Field(default_factory=_not_emitted)
    reported: Observation[ReportedToolCall] = _Field(default_factory=_not_emitted)
    wire: Observation[WireToolCall] = _Field(default_factory=_not_emitted)
    conflicts: tuple[EvidenceConflict, ...] = ()


class ProtocolKind(str, _Enum):
    MCP = "mcp"
    ACP = "acp"
    PROVIDER_HTTP = "provider_http"
    PROVIDER_STREAM = "provider_stream"


class SafeHttpHeader(_FrozenModel):
    name: _Literal[
        "content-type", "content-length", "retry-after", "request-id", "x-request-id"
    ]
    value: str


class HttpExchangeMetadata(_FrozenModel):
    method: str = _Field(min_length=1)
    status_code: int = _Field(ge=100, le=599)
    headers: tuple[SafeHttpHeader, ...] = ()


class ProtocolErrorDetails(_FrozenModel):
    code: int | str | None = None
    message: str = _Field(min_length=1, max_length=4096)
    data: Observation[_JsonValue] = _Field(default_factory=_not_emitted)


class ProtocolEntry(TraceEntryBase):
    kind: _Literal["protocol"] = "protocol"
    protocol: ProtocolKind
    method: Observation[str] = _Field(default_factory=_not_emitted)
    direction: _EventDirection = _EventDirection.INTERNAL
    jsonrpc_id: Observation[_JsonRpcId] = _Field(default_factory=_not_emitted)
    request: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    response: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    error: Observation[ProtocolErrorDetails] = _Field(default_factory=_not_emitted)
    http: Observation[HttpExchangeMetadata] = _Field(default_factory=_not_emitted)


class TransportEntry(TraceEntryBase):
    """A canonical MCP transport lifecycle observation."""

    kind: _Literal["transport"] = "transport"
    phase: _Literal["connected", "disconnected"]
    configured: Observation[_TransportKind] = _Field(default_factory=_not_emitted)
    instrumented: Observation[_TransportKind] = _Field(default_factory=_not_emitted)


class ProcessEntry(TraceEntryBase):
    kind: _Literal["process"] = "process"
    executable: Observation[str] = _Field(default_factory=_not_emitted)
    pid: Observation[int] = _Field(default_factory=_not_emitted)
    exit_code: Observation[int] = _Field(default_factory=_not_emitted)
    signal: Observation[int] = _Field(default_factory=_not_emitted)
    stderr: Observation[str] = _Field(default_factory=_not_emitted)


class RawEvidenceSource(str, _Enum):
    MCP = "mcp"
    ACP = "acp"
    OPENCODE = "opencode"
    CLAUDE_CODE = "claude_code"
    PROCESS_STDERR = "process_stderr"


class RawEvidence(_FrozenModel):
    reference: _RawEvidenceRef
    media_type: str = _Field(min_length=1, max_length=256)
    content: _JsonValue | str
    size_bytes: int = _Field(ge=0)
    returned_size_bytes: int = _Field(ge=0)
    truncated: bool = False
    redacted: _Literal[True] = True

    @_model_validator(mode="after")
    def _validate_read_bounds(self) -> RawEvidence:
        if self.returned_size_bytes > self.size_bytes:
            raise ValueError("returned evidence cannot exceed stored evidence")
        if self.truncated != (self.returned_size_bytes < self.size_bytes):
            raise ValueError("truncated must match returned and stored sizes")
        return self


class TraceCaptureConfig(_FrozenModel):
    """Boundaries for redacted provider/MCP evidence capture."""

    capture_raw_evidence: bool = True
    capture_provider_messages: bool = True
    capture_stderr: bool = True
    raw_preview_bytes: int = _Field(default=65_536, gt=0)
    raw_frame_bytes: int = _Field(default=1_048_576, gt=0)
    raw_execution_bytes: int = _Field(default=67_108_864, gt=0)


class RawEvidenceCapture(_FrozenModel):
    """Typed result of bounded, redacted raw-evidence capture."""

    reference: _RawEvidenceRef
    preview: Observation[str]
    original_size_bytes: int = _Field(ge=0)
    stored_size_bytes: int = _Field(ge=0)
    redacted: bool
    truncated: bool

    @_model_validator(mode="after")
    def _validate_capture_metadata(self) -> RawEvidenceCapture:
        if (
            self.reference.size_bytes is not None
            and self.reference.size_bytes != self.stored_size_bytes
        ):
            raise ValueError("evidence reference size must match stored size")
        if self.preview.evidence_ref != self.reference:
            raise ValueError("preview evidence reference must match capture reference")
        expected_state = (
            ObservationState.TRUNCATED
            if self.truncated
            else ObservationState.REDACTED
            if self.redacted
            else ObservationState.OBSERVED
        )
        if self.preview.state is not expected_state:
            raise ValueError("preview state does not match capture metadata")
        return self


class RawMessageEntry(TraceEntryBase):
    kind: _Literal["raw_message"] = "raw_message"
    source: RawEvidenceSource
    direction: _EventDirection = _EventDirection.INTERNAL
    media_type: str = _Field(min_length=1, max_length=256)
    preview: Observation[_JsonValue | str] = _Field(default_factory=_not_emitted)
    evidence_ref: _RawEvidenceRef | None = None
    size_bytes: int = _Field(default=0, ge=0)
    redacted: _Literal[True] = True


class UsageValue(_FrozenModel):
    """Value-only usage aggregate used by summaries and runtime metadata."""

    input_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    output_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    reasoning_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    cache_creation_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    cache_read_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    cache_write_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    total_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    cost: Observation[float] = _Field(default_factory=_not_emitted)
    currency: Observation[str] = _Field(default_factory=_not_emitted)


class UsageEntry(TraceEntryBase):
    kind: _Literal["usage"] = "usage"
    input_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    output_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    reasoning_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    cache_creation_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    cache_read_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    cache_write_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    total_tokens: Observation[int] = _Field(default_factory=_not_emitted)
    cost: Observation[float] = _Field(default_factory=_not_emitted)
    currency: Observation[str] = _Field(default_factory=_not_emitted)


class InitializationEntry(TraceEntryBase):
    kind: _Literal["initialization"] = "initialization"
    protocol_version: Observation[str] = _Field(default_factory=_not_emitted)
    server_name: Observation[str] = _Field(default_factory=_not_emitted)
    server_version: Observation[str] = _Field(default_factory=_not_emitted)
    instructions: Observation[str] = _Field(default_factory=_not_emitted)
    capabilities: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    tools: Observation[tuple[_DirectTool, ...]] = _Field(default_factory=_not_emitted)
    resources: Observation[tuple[_DirectResource, ...]] = _Field(
        default_factory=_not_emitted
    )
    resource_templates: Observation[tuple[_DirectResourceTemplate, ...]] = _Field(
        default_factory=_not_emitted
    )
    prompts: Observation[tuple[_DirectPrompt, ...]] = _Field(
        default_factory=_not_emitted
    )


class InitializationValue(_FrozenModel):
    """Value-only initialization metadata used by runtime information."""

    protocol_version: Observation[str] = _Field(default_factory=_not_emitted)
    server_name: Observation[str] = _Field(default_factory=_not_emitted)
    server_version: Observation[str] = _Field(default_factory=_not_emitted)
    instructions: Observation[str] = _Field(default_factory=_not_emitted)
    capabilities: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    tools: Observation[tuple[_DirectTool, ...]] = _Field(default_factory=_not_emitted)
    resources: Observation[tuple[_DirectResource, ...]] = _Field(
        default_factory=_not_emitted
    )
    resource_templates: Observation[tuple[_DirectResourceTemplate, ...]] = _Field(
        default_factory=_not_emitted
    )
    prompts: Observation[tuple[_DirectPrompt, ...]] = _Field(
        default_factory=_not_emitted
    )


class LifecycleEntry(TraceEntryBase):
    kind: _Literal["lifecycle"] = "lifecycle"
    phase: str = _Field(min_length=1, max_length=128)


class InteractionEntry(TraceEntryBase):
    kind: _Literal["interaction"] = "interaction"
    interaction_kind: str = _Field(min_length=1, max_length=128)
    request: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    response: Observation[_JsonValue] = _Field(default_factory=_not_emitted)


class WorkspaceEntry(TraceEntryBase):
    kind: _Literal["workspace"] = "workspace"
    change: Observation[_JsonValue] = _Field(default_factory=_not_emitted)


class ArtifactEntry(TraceEntryBase):
    kind: _Literal["artifact"] = "artifact"
    artifact: _ArtifactRef


class EvaluationEntry(TraceEntryBase):
    kind: _Literal["evaluation"] = "evaluation"
    evaluation: _EvaluationResult


class DiagnosticEntry(TraceEntryBase):
    kind: _Literal["diagnostic"] = "diagnostic"
    code: str = _Field(min_length=1, max_length=128)
    message: str = _Field(min_length=1, max_length=4096)


class ProviderEntry(TraceEntryBase):
    kind: _Literal["provider"] = "provider"
    provider: str = _Field(min_length=1, max_length=128)
    category: str = _Field(min_length=1, max_length=128)
    data: Observation[_JsonValue] = _Field(default_factory=_not_emitted)


class DirectTraceInfo(_FrozenModel):
    kind: _Literal["direct"] = "direct"
    transport: Observation[_TransportKind] = _Field(default_factory=_not_emitted)
    protocol: Observation[str] = _Field(default_factory=_not_emitted)
    initialization: Observation[InitializationValue] = _Field(
        default_factory=_not_emitted
    )


class OpenCodeTraceInfo(_FrozenModel):
    kind: _Literal["opencode"] = "opencode"
    session_id: Observation[str] = _Field(default_factory=_not_emitted)
    provider_id: Observation[str] = _Field(default_factory=_not_emitted)
    model_id: Observation[str] = _Field(default_factory=_not_emitted)
    finish_reason: Observation[str] = _Field(default_factory=_not_emitted)
    http_lifecycle: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    usage: Observation[UsageValue] = _Field(default_factory=_not_emitted)


class ClaudeCodeTraceInfo(_FrozenModel):
    kind: _Literal["claude_code"] = "claude_code"
    session_id: Observation[str] = _Field(default_factory=_not_emitted)
    model_id: Observation[str] = _Field(default_factory=_not_emitted)
    result_subtype: Observation[str] = _Field(default_factory=_not_emitted)
    stop_reason: Observation[str] = _Field(default_factory=_not_emitted)
    service_tier: Observation[str] = _Field(default_factory=_not_emitted)
    api_duration_ms: Observation[float] = _Field(default_factory=_not_emitted)
    encrypted_reasoning: Observation[bool] = _Field(default_factory=_not_emitted)
    usage: Observation[UsageValue] = _Field(default_factory=_not_emitted)


class ACPTraceInfo(_FrozenModel):
    kind: _Literal["acp"] = "acp"
    session_id: Observation[str] = _Field(default_factory=_not_emitted)
    protocol_version: Observation[str] = _Field(default_factory=_not_emitted)
    agent_identity: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    available_modes: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    current_mode: Observation[str] = _Field(default_factory=_not_emitted)
    config_options: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    selected_config: Observation[_JsonValue] = _Field(default_factory=_not_emitted)
    plan_state_available: Observation[bool] = _Field(default_factory=_not_emitted)
    usage: Observation[UsageValue] = _Field(
        default_factory=lambda: Observation(
            state=ObservationState.UNSUPPORTED,
            reason=ObservationReason.HARNESS_UNSUPPORTED,
        )
    )


RuntimeTraceInfo: _TypeAlias = _Annotated[
    DirectTraceInfo | OpenCodeTraceInfo | ClaudeCodeTraceInfo | ACPTraceInfo,
    _Field(discriminator="kind"),
]


TraceEntry: _TypeAlias = _Annotated[
    LifecycleEntry
    | MessageEntry
    | ReasoningEntry
    | ToolCallEntry
    | ProtocolEntry
    | TransportEntry
    | InitializationEntry
    | UsageEntry
    | InteractionEntry
    | ProcessEntry
    | WorkspaceEntry
    | ArtifactEntry
    | EvaluationEntry
    | DiagnosticEntry
    | RawMessageEntry
    | ProviderEntry,
    _Field(discriminator="kind"),
]


class TraceSummary(_FrozenModel):
    timing: TraceTiming = _Field(default_factory=TraceTiming)
    usage: Observation[UsageValue] = _Field(default_factory=_not_emitted)
    turn_count: int = _Field(default=0, ge=0)
    message_count: int = _Field(default=0, ge=0)
    reasoning_count: int = _Field(default=0, ge=0)
    tool_call_count: int = _Field(default=0, ge=0)
    successful_tool_call_count: int = _Field(default=0, ge=0)
    failed_tool_call_count: int = _Field(default=0, ge=0)
    protocol_error_count: int = _Field(default=0, ge=0)
    activity_health: _ActivityHealth = _ActivityHealth.NO_CALLS
    cleanup_status: TraceStatus = TraceStatus.COMPLETED


class TraceView(_FrozenModel):
    schema_id: _Literal["mcp_pal.trace_view"] = "mcp_pal.trace_view"
    schema_version: _Literal["1.1"] = "1.1"
    trace_id: _TraceId
    execution_id: _ExecutionId
    outcome: _ExecutionOutcome = _ExecutionOutcome.COMPLETED
    completeness: _Literal["complete", "partial"] = "complete"
    limitations: tuple[str, ...] = ()
    runtime: RuntimeTraceInfo = _Field(default_factory=DirectTraceInfo)
    summary: TraceSummary = _Field(default_factory=TraceSummary)
    timeline: tuple[TraceEntry, ...] = ()

    @_model_validator(mode="after")
    def _valid_completeness(self) -> TraceView:
        if self.completeness == "complete" and self.limitations:
            raise ValueError("complete traces cannot declare limitations")
        if self.completeness == "partial" and not self.limitations:
            raise ValueError("partial traces must declare limitations")
        return self

    def _entries(self, kind: str) -> tuple[TraceEntryBase, ...]:
        return tuple(
            _cast(TraceEntryBase, item) for item in self.timeline if item.kind == kind
        )

    @property
    def tool_calls(self) -> tuple[ToolCallEntry, ...]:
        return tuple(item for item in self.timeline if isinstance(item, ToolCallEntry))

    @property
    def messages(self) -> tuple[MessageEntry, ...]:
        return tuple(item for item in self.timeline if isinstance(item, MessageEntry))

    @property
    def reasoning(self) -> tuple[ReasoningEntry, ...]:
        return tuple(item for item in self.timeline if isinstance(item, ReasoningEntry))

    @property
    def protocol(self) -> tuple[ProtocolEntry, ...]:
        return tuple(item for item in self.timeline if isinstance(item, ProtocolEntry))

    @property
    def transports(self) -> tuple[TransportEntry, ...]:
        return tuple(item for item in self.timeline if isinstance(item, TransportEntry))

    @property
    def raw_messages(self) -> tuple[RawMessageEntry, ...]:
        return tuple(
            item for item in self.timeline if isinstance(item, RawMessageEntry)
        )

    @property
    def interactions(self) -> tuple[InteractionEntry, ...]:
        return tuple(
            item for item in self.timeline if isinstance(item, InteractionEntry)
        )

    @property
    def processes(self) -> tuple[ProcessEntry, ...]:
        return tuple(item for item in self.timeline if isinstance(item, ProcessEntry))

    @property
    def diagnostics(self) -> tuple[DiagnosticEntry, ...]:
        return tuple(
            item for item in self.timeline if isinstance(item, DiagnosticEntry)
        )

    def _filtered(self, entries: tuple[TraceEntry, ...]) -> TraceView:
        return self.model_copy(update={"timeline": entries})

    def for_turn(
        self,
        turn: _TurnResult | _TurnSnapshot | _TurnId | str,
    ) -> TraceView:
        """Return the finalized evidence belonging to one turn.

        ``TurnResult`` and ``TurnSnapshot`` are accepted as convenient public
        selectors; neither object owns a finalized ``TraceView`` itself.
        """
        if isinstance(turn, _TurnResult):
            value = turn.snapshot.turn_id.root
        elif isinstance(turn, _TurnSnapshot):
            value = turn.turn_id.root
        elif isinstance(turn, _TurnId):
            value = turn.root
        elif isinstance(turn, str):
            value = turn
        else:
            raise TypeError(
                "turn selector must be a TurnResult, TurnSnapshot, TurnId, or str"
            )
        return self._filtered(
            tuple(
                item
                for item in self.timeline
                if item.turn_id is not None and item.turn_id.root == value
            )
        )

    def for_session(self, session_id: _SessionId | str) -> TraceView:
        value = (
            session_id.root if isinstance(session_id, _SessionId) else str(session_id)
        )
        return self._filtered(
            tuple(
                item
                for item in self.timeline
                if item.session_id is not None and item.session_id.root == value
            )
        )

    def for_server(self, server_binding: str) -> TraceView:
        return self._filtered(
            tuple(
                item for item in self.timeline if item.server_binding == server_binding
            )
        )

    def between(self, start_offset_ms: float, end_offset_ms: float) -> TraceView:
        if start_offset_ms < 0 or end_offset_ms < start_offset_ms:
            raise ValueError("invalid trace offset range")
        return self._filtered(
            tuple(
                item
                for item in self.timeline
                if item.timing.end_offset_ms >= start_offset_ms
                and item.timing.start_offset_ms <= end_offset_ms
            )
        )


# Resolve the recursive discriminated unions once while their private helper
# aliases are present.  Afterwards remove those aliases so accidental public
# names cannot leak through the package boundary.
for _model in (
    Observation,
    ToolResult,
    ReportedToolCall,
    WireToolCall,
    EvidenceConflict,
    ToolCallEntry,
    ProtocolErrorDetails,
    ProtocolEntry,
    TransportEntry,
    RawEvidence,
    RawEvidenceCapture,
    RawMessageEntry,
    UsageValue,
    UsageEntry,
    InitializationEntry,
    InitializationValue,
    LifecycleEntry,
    InteractionEntry,
    WorkspaceEntry,
    ArtifactEntry,
    EvaluationEntry,
    DiagnosticEntry,
    ProviderEntry,
    DirectTraceInfo,
    OpenCodeTraceInfo,
    ClaudeCodeTraceInfo,
    ACPTraceInfo,
    TraceSummary,
    TraceView,
):
    _model.model_rebuild()
del _model


__all__ = [
    "ACPTraceInfo",
    "ArtifactEntry",
    "ClaudeCodeTraceInfo",
    "CorrelationState",
    "DiagnosticEntry",
    "DirectTraceInfo",
    "EvaluationEntry",
    "EvidenceConflict",
    "HttpExchangeMetadata",
    "InitializationEntry",
    "InitializationValue",
    "InteractionEntry",
    "LifecycleEntry",
    "MessageEntry",
    "MessageRole",
    "Observation",
    "ObservationReason",
    "ObservationState",
    "OpenCodeTraceInfo",
    "ProcessEntry",
    "ProtocolEntry",
    "ProtocolErrorDetails",
    "ProtocolKind",
    "ProviderEntry",
    "TransportEntry",
    "RawEvidence",
    "RawEvidenceCapture",
    "RawEvidenceSource",
    "RawMessageEntry",
    "ReasoningEntry",
    "ReportedToolCall",
    "RuntimeTraceInfo",
    "SafeHttpHeader",
    "ToolCallEntry",
    "ToolCallStatus",
    "ToolResult",
    "TraceCaptureConfig",
    "TraceEntry",
    "TraceEntryBase",
    "TraceStatus",
    "TraceSummary",
    "TraceTiming",
    "TraceView",
    "UsageEntry",
    "UsageValue",
    "WireToolCall",
    "WorkspaceEntry",
]
