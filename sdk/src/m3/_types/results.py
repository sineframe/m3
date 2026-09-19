from __future__ import annotations

from collections.abc import Mapping as _Mapping
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Annotated as _Annotated
from typing import Any as _Any
from typing import Literal as _Literal

from pydantic import Field as _Field
from pydantic import model_validator as _model_validator

from .base import (
    ActivityHealth,
    CapabilityStatus,
    ErrorInfo,
    ExecutionStatus,
    FrozenModel,
    TextContent,
    TransportKind,
    TurnId,
    TurnStatus,
)
from .evals import EvaluationRecord, EvaluationResult
from .events import ArtifactRef, Event, TraceResult
from .specs import ContentBlock, SessionSource
from .state import ExecutionState, TurnState

if _TYPE_CHECKING:
    from ..observability import TraceView as _TraceView


class Capability(FrozenModel):
    name: str
    status: CapabilityStatus
    reason: str | None = None
    detected_version: str | None = None
    protocol_version: str | None = None
    transport: TransportKind | None = None


class Readiness(FrozenModel):
    ready: bool
    capabilities: tuple[Capability, ...] = ()
    reason: str | None = None


class TurnResponse(FrozenModel):
    content: tuple[ContentBlock, ...] = ()
    metadata: _Mapping[str, _Any] = _Field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(
            block.text for block in self.content if isinstance(block, TextContent)
        )


class TurnResult(FrozenModel):
    snapshot: TurnState
    response: TurnResponse | None = None
    error: ErrorInfo | None = None
    trace: TraceResult | None = None
    # Structured adapter observations.  Adapters must supply only scalar,
    # redaction-safe values; the session boundary projects hostile input
    # before constructing this public immutable model.
    evidence: _Mapping[str, _Any] = _Field(default_factory=dict)

    @property
    def turn_id(self) -> TurnId:
        """Stable identifier usable to scope finalized session assertions."""
        return self.snapshot.turn_id

    @_model_validator(mode="after")
    def _requires_terminal_snapshot(self) -> TurnResult:
        if self.snapshot.lifecycle is not TurnStatus.FINISHED:
            raise ValueError("turn result requires a terminal turn snapshot")
        return self


class ToolInfo(FrozenModel):
    # Process-local official MCP evidence; excluded from public serialization.
    raw: _Any = _Field(default=None, exclude=True, repr=False)
    name: str = _Field(min_length=1, max_length=256)
    title: str | None = None
    description: str | None = None
    input_schema: _Mapping[str, _Any] | bool = _Field(default_factory=dict)
    output_schema: _Mapping[str, _Any] | bool | None = None


class ResourceInfo(FrozenModel):
    raw: _Any = _Field(default=None, exclude=True, repr=False)
    name: str = _Field(min_length=1, max_length=256)
    title: str | None = None
    uri: str = _Field(min_length=1, max_length=4096)
    description: str | None = None
    mime_type: str | None = None
    size: int | None = _Field(default=None, ge=0)


class TemplateInfo(FrozenModel):
    raw: _Any = _Field(default=None, exclude=True, repr=False)
    name: str = _Field(min_length=1, max_length=256)
    title: str | None = None
    uri_template: str = _Field(min_length=1, max_length=4096)
    description: str | None = None
    mime_type: str | None = None


class PromptInfo(FrozenModel):
    raw: _Any = _Field(default=None, exclude=True, repr=False)
    name: str = _Field(min_length=1, max_length=256)
    title: str | None = None
    description: str | None = None
    arguments: tuple[_Mapping[str, _Any], ...] = ()


class _DirectResult(FrozenModel):
    """Typed direct result base retaining process-local official MCP output."""

    # The official response is available to in-process callers, but is never
    # part of durable/public JSON evidence.
    raw: _Any = _Field(default=None, exclude=True, repr=False)


class ListToolsResult(_DirectResult):
    kind: _Literal["list_tools"] = "list_tools"
    tools: tuple[ToolInfo, ...] = ()
    next_cursor: str | None = None


class ListResourcesResult(_DirectResult):
    kind: _Literal["list_resources"] = "list_resources"
    resources: tuple[ResourceInfo, ...] = ()
    next_cursor: str | None = None


class ListTemplatesResult(_DirectResult):
    kind: _Literal["list_resource_templates"] = "list_resource_templates"
    resource_templates: tuple[TemplateInfo, ...] = ()
    next_cursor: str | None = None


class ListPromptsResult(_DirectResult):
    kind: _Literal["list_prompts"] = "list_prompts"
    prompts: tuple[PromptInfo, ...] = ()
    next_cursor: str | None = None


class CallToolResult(_DirectResult):
    kind: _Literal["call_tool"] = "call_tool"
    content: tuple[_Mapping[str, _Any], ...] = ()
    structured_content: _Any = None
    is_error: bool = False


class ReadResourceResult(_DirectResult):
    kind: _Literal["read_resource"] = "read_resource"
    contents: tuple[_Mapping[str, _Any], ...] = ()


class GetPromptResult(_DirectResult):
    kind: _Literal["get_prompt"] = "get_prompt"
    description: str | None = None
    messages: tuple[_Mapping[str, _Any], ...] = ()


class PingResult(_DirectResult):
    kind: _Literal["ping"] = "ping"
    result_type: str | None = None


DirectResult = _Annotated[
    ListToolsResult
    | ListResourcesResult
    | ListTemplatesResult
    | ListPromptsResult
    | CallToolResult
    | ReadResourceResult
    | GetPromptResult
    | PingResult,
    _Field(discriminator="kind"),
]


class ExecutionResult(FrozenModel):
    snapshot: ExecutionState
    turns: tuple[TurnResult, ...] = ()
    trace: TraceResult | None = None
    direct_result: DirectResult | None = None
    evaluations: tuple[EvaluationResult, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    activity_health: ActivityHealth = ActivityHealth.NO_CALLS
    error: ErrorInfo | None = None
    provenance: SessionSource | None = None

    @property
    def trace_view(self) -> _TraceView:
        """Return the finalized typed view for this execution trace."""

        if self.trace is None:
            from ..errors import TraceUnavailable

            raise TraceUnavailable("execution has no trace evidence")
        return self.trace.view()

    @_model_validator(mode="after")
    def _requires_terminal_snapshot(self) -> ExecutionResult:
        if self.snapshot.lifecycle is not ExecutionStatus.FINISHED:
            raise ValueError("execution result requires a terminal execution snapshot")
        return self


class ExecutionEvidence(FrozenModel):
    """Typed completeness markers persisted in stable terminal events."""

    completeness: _Literal["complete", "partial"]
    limitations: tuple[str, ...] = ()
    reason: str | None = None

    @_model_validator(mode="after")
    def _validate_completeness(self) -> ExecutionEvidence:
        if self.completeness == "complete" and self.limitations:
            raise ValueError("complete evidence cannot declare limitations")
        if self.completeness == "partial" and not self.limitations:
            raise ValueError("partial evidence must declare limitations")
        return self


class ExecutionReport(FrozenModel):
    """Portable evidence that is actually persisted by an execution store."""

    snapshot: ExecutionState
    events: tuple[Event, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    direct_result: DirectResult | None = None
    error: ErrorInfo | None = None
    evidence: ExecutionEvidence | None = None
    turns: tuple[TurnResult, ...] = ()
    evaluations: tuple[EvaluationRecord, ...] = ()
    event_count: int = _Field(default=0, ge=0)
    events_truncated: bool = False
    next_after_sequence: int | None = _Field(default=None, ge=-1)
    artifact_count: int = _Field(default=0, ge=0)
    artifacts_truncated: bool = False

    @_model_validator(mode="after")
    def _validate_projection(self) -> ExecutionReport:
        if any(
            event.execution_id != self.snapshot.execution_id for event in self.events
        ):
            raise ValueError("report events must belong to its execution")
        if any(
            evaluation.execution_id != self.snapshot.execution_id
            for evaluation in self.evaluations
        ):
            raise ValueError("report evaluations must belong to its execution")
        if any(
            left.sequence >= right.sequence
            for left, right in zip(self.events, self.events[1:], strict=False)
        ):
            raise ValueError("report events must be ordered")
        if len(self.events) > self.event_count or (
            not self.events_truncated and len(self.events) != self.event_count
        ):
            raise ValueError("report event count is inconsistent with its projection")
        if len(self.artifacts) > self.artifact_count or (
            not self.artifacts_truncated and len(self.artifacts) != self.artifact_count
        ):
            raise ValueError(
                "report artifact count is inconsistent with its projection"
            )
        return self


__all__ = [
    "CallToolResult",
    "Capability",
    "DirectResult",
    "ExecutionEvidence",
    "ExecutionReport",
    "ExecutionResult",
    "GetPromptResult",
    "ListPromptsResult",
    "ListResourcesResult",
    "ListTemplatesResult",
    "ListToolsResult",
    "PingResult",
    "PromptInfo",
    "ReadResourceResult",
    "Readiness",
    "ResourceInfo",
    "TemplateInfo",
    "ToolInfo",
    "TurnResponse",
    "TurnResult",
]
