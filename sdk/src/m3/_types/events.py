from __future__ import annotations

from collections.abc import Mapping as _Mapping
from datetime import datetime as _datetime
from datetime import timezone as _timezone
from enum import Enum as _Enum
from math import isfinite as _isfinite
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Any as _Any
from typing import Literal as _Literal

from pydantic import AliasChoices as _AliasChoices
from pydantic import Field as _Field
from pydantic import StrictInt as _StrictInt
from pydantic import StrictStr as _StrictStr
from pydantic import field_validator as _field_validator
from pydantic import model_validator as _model_validator

from .base import (
    EVENT_SCHEMA_ID,
    EVENT_SCHEMA_VERSION,
    ArtifactId,
    ConnectionId,
    EventId,
    ExecutionId,
    FrozenModel,
    SessionId,
    TraceId,
    TurnId,
    _utc_now,
)

if _TYPE_CHECKING:
    from ..observability import TraceView as _TraceView


class EventKind(str, _Enum):
    """Closed taxonomy for stable harness-neutral events."""

    EXECUTION_CREATED = "execution.created"
    EXECUTION_STATE_CHANGED = "execution.state_changed"
    EXECUTION_FINISHED = "execution.finished"
    HARNESS_SELECTION = "harness.selection"
    HARNESS_RUNTIME_RESOLVED = "harness.runtime_resolved"
    SESSION_CREATED = "session.created"
    SESSION_STATE_CHANGED = "session.state_changed"
    TURN_CREATED = "turn.created"
    TURN_STATE_CHANGED = "turn.state_changed"
    PROCESS_STARTED = "process.started"
    PROCESS_EXITED = "process.exited"
    TRANSPORT_CONNECTED = "transport.connected"
    TRANSPORT_DISCONNECTED = "transport.disconnected"
    MCP_INITIALIZED = "mcp.initialized"
    MCP_REQUEST = "mcp.request"
    MCP_RESPONSE = "mcp.response"
    MCP_ERROR = "mcp.error"
    MCP_NOTIFICATION = "mcp.notification"
    MCP_PROGRESS = "mcp.progress"
    MCP_CANCELLATION_REQUESTED = "mcp.cancellation_requested"
    MCP_CANCELLATION_COMPLETED = "mcp.cancellation_completed"
    AGENT_MESSAGE = "agent.message"
    ASSISTANT_CONTENT = "assistant.content"
    TOOL_CALL_REQUESTED = "tool.call_requested"
    TOOL_RESULT_RECEIVED = "tool.result_received"
    PERMISSION_REQUEST = "permission.request"
    PERMISSION_RESPONSE = "permission.response"
    SAMPLING_REQUEST = "sampling.request"
    SAMPLING_RESPONSE = "sampling.response"
    ELICITATION_REQUEST = "elicitation.request"
    ELICITATION_RESPONSE = "elicitation.response"
    FILESYSTEM_READ_REQUEST = "filesystem.read.request"
    FILESYSTEM_READ_RESPONSE = "filesystem.read.response"
    FILESYSTEM_WRITE_REQUEST = "filesystem.write.request"
    FILESYSTEM_WRITE_RESPONSE = "filesystem.write.response"
    TERMINAL_CREATE_REQUEST = "terminal.create.request"
    TERMINAL_CREATE_RESPONSE = "terminal.create.response"
    TERMINAL_OUTPUT_REQUEST = "terminal.output.request"
    TERMINAL_OUTPUT_RESPONSE = "terminal.output.response"
    TERMINAL_WAIT_REQUEST = "terminal.wait.request"
    TERMINAL_WAIT_RESPONSE = "terminal.wait.response"
    TERMINAL_RELEASE_REQUEST = "terminal.release.request"
    TERMINAL_RELEASE_RESPONSE = "terminal.release.response"
    TERMINAL_KILL_REQUEST = "terminal.kill.request"
    TERMINAL_KILL_RESPONSE = "terminal.kill.response"
    REASONING = "reasoning"
    EVALUATION_RECORDED = "evaluation.recorded"
    ARTIFACT_RECORDED = "artifact.recorded"
    WORKSPACE_CHANGED = "workspace.changed"
    CLEANUP_STARTED = "cleanup.started"
    CLEANUP_FINISHED = "cleanup.finished"
    DIAGNOSTIC = "diagnostic"
    PROVIDER_EVENT = "provider.event"


class EventDirection(str, _Enum):
    CLIENT_TO_SERVER = "client_to_server"
    SERVER_TO_CLIENT = "server_to_client"
    SDK_TO_HARNESS = "sdk_to_harness"
    HARNESS_TO_SDK = "harness_to_sdk"
    INTERNAL = "internal"


class LifecyclePhase(str, _Enum):
    PREFLIGHT = "preflight"
    STARTUP = "startup"
    INITIALIZATION = "initialization"
    IDLE = "idle"
    TURN = "turn"
    MCP_CALL = "mcp_call"
    CLEANUP = "cleanup"
    TERMINAL = "terminal"
    UNKNOWN = "unknown"


class EventOrigin(str, _Enum):
    NORMALIZED = "normalized"
    WIRE_OBSERVED = "wire_observed"
    HARNESS_REPORTED = "harness_reported"
    DERIVED = "derived"


class ReasoningVisibility(str, _Enum):
    VISIBLE = "visible"
    UNAVAILABLE = "unavailable"
    ENCRYPTED = "encrypted"
    PROVIDER_HIDDEN = "provider_hidden"


JsonRpcId = _StrictInt | _StrictStr


class EvidenceRef(FrozenModel):
    evidence_id: str = _Field(min_length=1, max_length=256)
    sha256: str | None = _Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = _Field(default=None, ge=0)
    media_type: str | None = _Field(default=None, max_length=256)
    storage_key: str | None = _Field(default=None, min_length=1, max_length=1024)


class PayloadRef(FrozenModel):
    blob_id: str = _Field(min_length=1, max_length=256)
    sha256: str = _Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = _Field(ge=0)
    media_type: str = _Field(default="application/json", max_length=256)
    compression: str | None = _Field(default=None, max_length=64)


class EventSource(FrozenModel):
    origin: EventOrigin
    source: str = _Field(min_length=1, max_length=256)
    provider_kind: str | None = _Field(default=None, max_length=256)
    derived_from_sequence: int | None = _Field(default=None, ge=0)


class ReasoningState(FrozenModel):
    visibility: ReasoningVisibility
    explicit: bool = False
    payload_ref: PayloadRef | None = None

    @_model_validator(mode="after")
    def _validate_visibility(self) -> ReasoningState:
        if self.visibility is ReasoningVisibility.VISIBLE and not self.explicit:
            raise ValueError("visible reasoning must be explicitly emitted")
        if self.visibility is not ReasoningVisibility.VISIBLE and self.explicit:
            raise ValueError(
                "unavailable or hidden reasoning cannot be marked explicit"
            )
        if (
            self.visibility
            in {ReasoningVisibility.UNAVAILABLE, ReasoningVisibility.PROVIDER_HIDDEN}
            and self.payload_ref is not None
        ):
            raise ValueError(
                "unavailable or provider-hidden reasoning cannot carry a payload reference"
            )
        return self


class RequestLink(FrozenModel):
    jsonrpc_id: JsonRpcId | None = None
    direction: EventDirection
    request_sequence: int | None = _Field(default=None, ge=1)

    @_field_validator("jsonrpc_id", mode="before")
    @classmethod
    def _reject_bool_ids(cls, value: _Any) -> _Any:
        if isinstance(value, bool):
            raise ValueError("JSON-RPC IDs cannot be boolean")
        return value


class Event(FrozenModel):
    schema_id: _Literal["m3.event"] = _Field(default="m3.event", alias="schema")
    schema_version: _Literal["0.2"] = "0.2"
    event_id: EventId
    execution_id: ExecutionId
    sequence: int = _Field(ge=0)
    kind: EventKind
    timestamp: _datetime = _Field(default_factory=_utc_now)
    monotonic_offset_ms: float = _Field(ge=0)
    session_id: SessionId | None = None
    turn_id: TurnId | None = None
    server_binding: str | None = _Field(default=None, max_length=256)
    connection_id: ConnectionId | None = None
    correlation: RequestLink | None = None
    lifecycle_phase: LifecyclePhase = LifecyclePhase.UNKNOWN
    payload: _Mapping[str, _Any] = _Field(default_factory=dict)
    payload_ref: PayloadRef | None = None
    provenance: EventSource = _Field(
        default_factory=lambda: EventSource(origin=EventOrigin.NORMALIZED, source="m3")
    )
    raw_evidence_ref: EvidenceRef | None = _Field(
        default=None,
        validation_alias=_AliasChoices("raw_evidence_ref", "raw_evidence"),
    )
    reasoning: ReasoningState | None = None

    @_field_validator("schema_id")
    @classmethod
    def _validate_schema_id(cls, value: str) -> str:
        if value != EVENT_SCHEMA_ID:
            raise ValueError("schema must be 'm3.event'")
        return value

    @_field_validator("schema_version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        if value != EVENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported event schema version {value!r}")
        return value

    @_field_validator("timestamp")
    @classmethod
    def _utc_timestamp(cls, value: _datetime) -> _datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must be timezone-aware UTC")
        return value.astimezone(_timezone.utc)

    @_field_validator("monotonic_offset_ms")
    @classmethod
    def _finite_offset(cls, value: float) -> float:
        if not _isfinite(value):
            raise ValueError("monotonic offset must be finite")
        return value

    @_model_validator(mode="after")
    def _correlation_requires_connection(self) -> Event:
        if (
            self.correlation is not None
            and self.correlation.request_sequence is not None
            and self.connection_id is None
        ):
            raise ValueError("request sequence requires a connection identity")
        if self.kind is EventKind.REASONING and self.reasoning is None:
            raise ValueError("reasoning events require an explicit visibility state")
        return self


class TraceResult(FrozenModel):
    trace_id: TraceId
    execution_id: ExecutionId
    completeness: _Literal["complete", "partial"] = "complete"
    highest_sequence: int = _Field(default=0, ge=0)
    events: tuple[Event, ...] = ()
    limitations: tuple[str, ...] = ()

    def view(self) -> _TraceView:
        """Project this finalized stable trace into the typed view."""

        from ..trace.projector import TraceProjector

        return TraceProjector.project(self)

    @_model_validator(mode="after")
    def _validate_trace_invariants(self) -> TraceResult:
        if self.completeness == "complete" and self.limitations:
            raise ValueError("complete traces cannot declare limitations")
        if self.completeness == "partial" and not self.limitations:
            raise ValueError("partial traces must declare at least one limitation")
        if any(not limitation.strip() for limitation in self.limitations):
            raise ValueError("trace limitations must be non-empty")
        if not self.events:
            if self.highest_sequence != 0:
                raise ValueError("empty traces must have highest_sequence 0")
            return self
        if any(event.execution_id != self.execution_id for event in self.events):
            raise ValueError("all trace events must belong to the trace execution")
        if self.events[0].sequence != 0:
            raise ValueError("trace event sequence must start at 0")
        expected_sequences = tuple(range(len(self.events)))
        actual_sequences = tuple(event.sequence for event in self.events)
        if actual_sequences != expected_sequences:
            raise ValueError("trace event sequences must be contiguous and ordered")
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("trace event IDs must be unique")
        if any(
            left.monotonic_offset_ms > right.monotonic_offset_ms
            for left, right in zip(self.events, self.events[1:], strict=False)
        ):
            raise ValueError("trace monotonic offsets must be nondecreasing")
        if self.highest_sequence != self.events[-1].sequence:
            raise ValueError("highest_sequence must equal the last event sequence")
        return self


class ArtifactRef(FrozenModel):
    artifact_id: ArtifactId
    execution_id: ExecutionId
    name: str = _Field(min_length=1)
    media_type: str | None = None
    size_bytes: int = _Field(ge=0)
    sha256: str = _Field(pattern=r"^[0-9a-f]{64}$")
    # Persisted/exported artifact references are safe by construction.  An
    # unredacted artifact must not be representable in the public model.
    redacted: _Literal[True] = True


__all__ = [
    "ArtifactRef",
    "Event",
    "EventDirection",
    "EventKind",
    "EventOrigin",
    "EventSource",
    "EvidenceRef",
    "JsonRpcId",
    "LifecyclePhase",
    "PayloadRef",
    "ReasoningState",
    "ReasoningVisibility",
    "RequestLink",
    "TraceResult",
]
