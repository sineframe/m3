"""Frozen, serializable domain values for the MCP Pal public SDK.

This module intentionally contains contracts and value objects only.  It does
not import application settings, FastAPI, Streamlit, pytest, SQLAlchemy, or
any process/transport implementation.
"""

from __future__ import annotations

from datetime import datetime as _datetime, timezone as _timezone
from enum import Enum as _Enum
from math import isfinite as _isfinite
from typing import (
    Annotated as _Annotated,
    Any as _Any,
    Literal as _Literal,
    Mapping as _Mapping,
    Iterator as _Iterator,
    Sequence as _Sequence,
    TYPE_CHECKING as _TYPE_CHECKING,
    Union as _Union,
)

from pydantic import (
    AliasChoices as _AliasChoices,
    BaseModel as _BaseModel,
    ConfigDict as _ConfigDict,
    Field as _Field,
    RootModel as _RootModel,
    StrictInt as _StrictInt,
    StrictStr as _StrictStr,
    field_serializer as _field_serializer,
    field_validator as _field_validator,
    model_validator as _model_validator,
)

if _TYPE_CHECKING:
    from .observability import TraceView as _TraceView

from .errors import InvalidTransitionError as _InvalidTransitionError, ModelValidationError as _ModelValidationError


def _utc_now() -> _datetime:
    return _datetime.now(_timezone.utc)


EVENT_SCHEMA_ID = "mcp_pal.event"
EVENT_SCHEMA_VERSION = "0.2"


class _FrozenMapping(_Mapping[_Any, _Any]):
    """Tuple-backed immutable mapping with no mutable dict base to bypass."""

    __slots__ = ("_items",)
    _items: tuple[tuple[_Any, _Any], ...]

    def __init__(self, values: _Mapping[_Any, _Any] | None = None) -> None:
        object.__setattr__(self, "_items", tuple((values or {}).items()))

    def __setattr__(self, name: str, value: _Any) -> None:
        raise TypeError("frozen mapping is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("frozen mapping is immutable")

    def __getitem__(self, key: _Any) -> _Any:
        for item_key, item_value in self._items:
            if item_key == key:
                return item_value
        raise KeyError(key)

    def __iter__(self) -> _Iterator[_Any]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


def _deep_thaw(value: _Any) -> _Any:
    """Return JSON-compatible containers for Pydantic's serializer."""

    if isinstance(value, _Mapping):
        return {_deep_thaw(key): _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [_deep_thaw(item) for item in value]
    return value


def _json_safe(value: _Any) -> bool:
    """Return whether a public value can be represented by JSON serialization."""

    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, _datetime):
        return True
    if isinstance(value, _Enum):
        return _json_safe(value.value)
    if isinstance(value, _BaseModel):
        for field_name, field_info in type(value).model_fields.items():
            if field_info.exclude:
                continue
            if not _json_safe(getattr(value, field_name)):
                return False
        return True
    if isinstance(value, _Mapping):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_json_safe(item) for item in value)
    if isinstance(value, _Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return all(_json_safe(item) for item in value)
    return False


def _deep_freeze(value: _Any) -> _Any:
    """Recursively freeze containers while retaining JSON-compatible shapes."""

    if isinstance(value, _BaseModel):
        return value
    if isinstance(value, _Mapping):
        return _FrozenMapping({_deep_freeze(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, _Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        # JSON has no set type.  Use a deterministic immutable tuple so the
        # value remains round-trippable without exposing a mutable list.
        items = (_deep_freeze(item) for item in value)
        return tuple(sorted(items, key=lambda item: (type(item).__name__, repr(item))))
    return value


class FrozenModel(_BaseModel):
    """Base configuration shared by public value objects."""

    model_config = _ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    @_model_validator(mode="after")
    def _freeze_nested_values(self) -> "FrozenModel":
        for field_name, value in self.__dict__.items():
            field_info = type(self).model_fields.get(field_name)
            if field_info is None or not field_info.exclude:
                if not _json_safe(value):
                    raise ValueError(f"{field_name} contains a non-JSON-serializable value")
            object.__setattr__(self, field_name, _deep_freeze(value))
        return self

    @_field_serializer("*", check_fields=False)
    def _serialize_nested_values(self, value: _Any) -> _Any:
        return _deep_thaw(value)


class Identifier(_RootModel[str]):
    """Non-empty stable identifier used in serialized SDK values."""

    model_config = _ConfigDict(frozen=True, str_strip_whitespace=True)

    @_field_validator("root")
    @classmethod
    def _valid_identifier(cls, value: str) -> str:
        if not value or len(value) > 256:
            raise ValueError("identifier must contain 1–256 characters")
        return value


class ExecutionId(Identifier):
    pass


class SessionId(Identifier):
    pass


class TurnId(Identifier):
    pass


class ServerId(Identifier):
    pass


class ServerProfileId(Identifier):
    pass


class HarnessId(Identifier):
    pass


class HarnessProfileId(Identifier):
    pass


class RevisionId(Identifier):
    pass


class ConnectionId(Identifier):
    pass


class EventId(Identifier):
    pass


class ArtifactId(Identifier):
    pass


class TraceId(Identifier):
    pass


class EvaluationId(Identifier):
    pass


class RunId(Identifier):
    """Stable identity for one coordinated test/evaluation run."""

    pass


class Metadata(FrozenModel):
    """Non-secret descriptive metadata carried by public values."""

    name: str | None = _Field(default=None, max_length=256)
    description: str | None = _Field(default=None, max_length=4096)
    labels: _Mapping[str, str] = _Field(default_factory=dict)


class TransportKind(str, _Enum):
    IN_PROCESS = "in_process"
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


class TrustLevel(str, _Enum):
    UNTRUSTED = "untrusted"
    PUBLIC = "public"
    TRUSTED_PRIVATE = "trusted_private"
    SDK_LOOPBACK = "sdk_loopback"


class LifecycleState(str, _Enum):
    CREATED = "created"
    QUEUED = "queued"
    STARTING = "starting"
    IDLE = "idle"
    RUNNING_TURN = "running_turn"
    CLOSING = "closing"
    FINISHED = "finished"


class ExecutionOutcome(str, _Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class TurnLifecycle(str, _Enum):
    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"


class TurnOutcome(str, _Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class EvaluationStatus(str, _Enum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"
    NOT_RUN = "not_run"


class CapabilityStatus(str, _Enum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


class ActivityHealth(str, _Enum):
    NO_CALLS = "no_calls"
    ALL_SUCCEEDED = "all_succeeded"
    ALL_FAILED = "all_failed"
    MIXED = "mixed"


class ArtifactPolicy(str, _Enum):
    FAILED = "failed"
    ALWAYS = "always"
    NEVER = "never"


class WorkspaceKind(str, _Enum):
    TEMPORARY = "temporary"
    COPY = "copy"
    GIT_WORKTREE = "git_worktree"
    READ_ONLY = "read_only"
    IN_PLACE = "in_place"


class ErrorCode(str, _Enum):
    INVALID_ARGUMENT = "invalid_argument"
    INVALID_TRANSITION = "invalid_transition"
    PROTOCOL_ERROR = "protocol_error"
    TRANSPORT_ERROR = "transport_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SESSION_STILL_OPEN = "session_still_open"
    SESSION_BUSY = "session_busy"
    UNSUPPORTED = "unsupported"
    CLEANUP_FAILED = "cleanup_failed"


class SecretReference(FrozenModel):
    """Reference to a secret; resolved values are deliberately not modelled."""

    source: _Literal["environment", "provider"]
    name: str = _Field(min_length=1, max_length=256)


class ErrorInfo(FrozenModel):
    code: ErrorCode
    message: str = _Field(min_length=1, max_length=4096)
    retryable: bool = False
    details: _Mapping[str, _Any] = _Field(default_factory=dict)


class ProtocolConstraint(FrozenModel):
    revision: str | None = _Field(default=None, min_length=1, max_length=128)
    transport: TransportKind | None = None


class RevisionSelection(FrozenModel):
    """Explicit profile revision request: resolve ``latest`` before execution."""

    mode: _Literal["latest", "pinned"]
    revision_id: RevisionId | None = None
    revision_number: int | None = _Field(default=None, ge=1)

    @_model_validator(mode="after")
    def _validate_selection(self) -> "RevisionSelection":
        has_id = self.revision_id is not None
        has_number = self.revision_number is not None
        if self.mode == "pinned" and not (has_id and has_number):
            raise ValueError("pinned revision selection requires revision_id and revision_number")
        if self.mode == "latest" and (has_id or has_number):
            raise ValueError("latest revision selection cannot include an immutable revision")
        return self


class TextContent(FrozenModel):
    kind: _Literal["text"] = "text"
    text: str


class FileContent(FrozenModel):
    kind: _Literal["file"] = "file"
    path: str = _Field(min_length=1)
    media_type: str | None = None

    @_field_validator("path")
    @classmethod
    def _path_is_relative_or_explicit(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("path cannot contain NUL")
        return value


class ImageContent(FrozenModel):
    kind: _Literal["image"] = "image"
    media_type: str
    data: str | None = None
    uri: str | None = None

    @_model_validator(mode="after")
    def _one_source(self) -> "ImageContent":
        if (self.data is None) == (self.uri is None):
            raise ValueError("image requires exactly one of data or uri")
        return self


class AudioContent(FrozenModel):
    kind: _Literal["audio"] = "audio"
    media_type: str
    data: str | None = None
    uri: str | None = None

    @_model_validator(mode="after")
    def _one_source(self) -> "AudioContent":
        if (self.data is None) == (self.uri is None):
            raise ValueError("audio requires exactly one of data or uri")
        return self


class ResourceLinkContent(FrozenModel):
    kind: _Literal["resource_link"] = "resource_link"
    uri: str = _Field(min_length=1)
    name: str | None = None
    description: str | None = None
    media_type: str | None = None


class OpaqueContent(FrozenModel):
    kind: _Literal["opaque"] = "opaque"
    provider: str = _Field(min_length=1, max_length=128)
    payload: _Mapping[str, _Any]


ContentBlock = _Annotated[
    _Union[TextContent, FileContent, ImageContent, AudioContent, ResourceLinkContent, OpaqueContent],
    _Field(discriminator="kind"),
]


class UserMessage(FrozenModel):
    """Typed user message; a string is accepted as text shorthand."""

    content: tuple[ContentBlock, ...]
    metadata: _Mapping[str, _Any] = _Field(default_factory=dict)

    @_field_validator("content", mode="before")
    @classmethod
    def _text_shorthand(cls, value: _Any) -> _Any:
        if isinstance(value, str):
            return (TextContent(text=value),)
        if isinstance(value, _Mapping):
            return (value,)
        if isinstance(value, _Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return tuple(TextContent(text=item) if isinstance(item, str) else item for item in value)
        return value


class ServerDefinition(FrozenModel):
    name: str = _Field(min_length=1, max_length=256)
    trust: TrustLevel = TrustLevel.UNTRUSTED


class StdioServer(ServerDefinition):
    kind: _Literal["stdio"] = "stdio"
    command: str = _Field(min_length=1)
    args: tuple[str, ...] = ()
    environment: _Mapping[str, SecretReference | str] = _Field(default_factory=dict)
    cwd: str | None = None

    @_field_validator("cwd")
    @classmethod
    def _cwd_is_safe_text(cls, value: str | None) -> str | None:
        if value is not None and (not value or "\x00" in value):
            raise ValueError("stdio cwd must be a non-empty path without NUL")
        return value


class StreamableHTTPServer(ServerDefinition):
    kind: _Literal["streamable_http"] = "streamable_http"
    url: str = _Field(min_length=1)
    headers: _Mapping[str, SecretReference | str] = _Field(default_factory=dict)


class SSEServer(ServerDefinition):
    kind: _Literal["sse"] = "sse"
    url: str = _Field(min_length=1)
    headers: _Mapping[str, SecretReference | str] = _Field(default_factory=dict)


class InProcessServer(ServerDefinition):
    """Runtime-only server descriptor; the factory is excluded from serialization."""

    kind: _Literal["in_process"] = "in_process"
    factory: _Any = _Field(exclude=True, repr=False)
    descriptor: _Mapping[str, _Any] = _Field(default_factory=dict)
    origin: str = _Field(default="python_registration", min_length=1, max_length=256)
    trust: TrustLevel = TrustLevel.SDK_LOOPBACK

    @_field_validator("factory")
    @classmethod
    def _factory_is_callable(cls, value: _Any) -> _Any:
        if not callable(value):
            raise ValueError("in-process server factory must be callable")
        return value

    def __getstate__(self) -> _Any:
        raise TypeError("in-process server factories are runtime registrations and cannot be pickled")


ServerValue = _Annotated[
    _Union[StdioServer, StreamableHTTPServer, SSEServer, InProcessServer],
    _Field(discriminator="kind"),
]


class ServerProfileRef(FrozenModel):
    profile_id: ServerProfileId
    revision: RevisionSelection


class ServerBinding(FrozenModel):
    server: ServerValue | None = None
    profile: ServerProfileRef | None = None
    alias: str | None = _Field(default=None, min_length=1, max_length=256)
    required: bool = True

    @_model_validator(mode="after")
    def _one_binding_source(self) -> "ServerBinding":
        if (self.server is None) == (self.profile is None):
            raise ValueError("server binding requires exactly one of server or profile")
        return self


class HarnessValue(FrozenModel):
    name: str = _Field(min_length=1, max_length=128)
    model: str = _Field(min_length=1, max_length=512)
    executable: str | None = None

    def with_model(self, model: str) -> "HarnessValue":
        """Return an immutable copy with a different model identifier."""

        values = self.model_dump(mode="python")
        values["model"] = model
        return type(self).model_validate(values)


class ClaudeCode(HarnessValue):
    kind: _Literal["claude_code"] = "claude_code"
    name: str = "claude-code"
    credential_references: _Mapping[str, SecretReference] = _Field(default_factory=dict)

    @_field_validator("credential_references")
    @classmethod
    def _valid_credential_names(cls, values: _Mapping[str, SecretReference]) -> _Mapping[str, SecretReference]:
        import re
        if any(not isinstance(key, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None for key in values):
            raise ValueError("Claude Code credential target is invalid")
        return values


class OpenCode(HarnessValue):
    kind: _Literal["opencode"] = "opencode"
    name: str = "opencode"
    provider: str | None = None
    dialect: _Literal["auto", "legacy", "v2"] = "auto"
    credential_references: _Mapping[str, SecretReference] = _Field(default_factory=dict)

    @_field_validator("credential_references")
    @classmethod
    def _valid_credential_names(cls, values: _Mapping[str, SecretReference]) -> _Mapping[str, SecretReference]:
        import re
        if any(not isinstance(key, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None for key in values):
            raise ValueError("OpenCode credential target is invalid")
        return values


class ACPAgent(HarnessValue):
    kind: _Literal["acp"] = "acp"
    name: str = "acp"
    manifest: _Mapping[str, _Any] = _Field(default_factory=dict)
    agent_mode_id: str | None = _Field(default=None, min_length=1, max_length=256)
    session_config: _Mapping[str, _Any] = _Field(default_factory=dict)

    @_field_validator("session_config")
    @classmethod
    def _valid_session_config(cls, values: _Mapping[str, _Any]) -> _Mapping[str, _Any]:
        if any(not isinstance(key, str) or not key for key in values):
            raise ValueError("ACP session configuration keys must be non-empty text")
        if any(not isinstance(value, (str, bool)) for value in values.values()):
            raise ValueError("ACP session configuration values must be text or boolean")
        return values


HarnessSpec = _Annotated[_Union[ClaudeCode, OpenCode, ACPAgent], _Field(discriminator="kind")]


class HarnessProfileRef(FrozenModel):
    profile_id: HarnessProfileId
    revision: RevisionSelection


class WorkspacePolicy(FrozenModel):
    kind: WorkspaceKind = WorkspaceKind.TEMPORARY
    source: str | None = None
    acknowledge_risk: bool = False

    @_model_validator(mode="after")
    def _validate_workspace(self) -> "WorkspacePolicy":
        if self.kind in {WorkspaceKind.COPY, WorkspaceKind.GIT_WORKTREE, WorkspaceKind.READ_ONLY} and not self.source:
            raise ValueError(f"{self.kind.value} workspace requires source")
        if self.kind is WorkspaceKind.IN_PLACE and not self.acknowledge_risk:
            raise ValueError("in-place workspace requires acknowledge_risk=True")
        return self


class RestrictiveToolPolicy(FrozenModel):
    kind: _Literal["restrictive"] = "restrictive"
    allowed_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()


class FullToolPolicy(FrozenModel):
    kind: _Literal["full"] = "full"
    acknowledge_risk: bool = False

    @_model_validator(mode="after")
    def _risk_acknowledged(self) -> "FullToolPolicy":
        if not self.acknowledge_risk:
            raise ValueError("FullToolPolicy requires acknowledge_risk=True")
        return self


class NativeToolPolicy(FrozenModel):
    kind: _Literal["native"] = "native"
    harness: str = _Field(min_length=1)
    policy: _Mapping[str, _Any]
    nonportable_reason: str = _Field(min_length=1)


ToolPolicy = _Annotated[_Union[RestrictiveToolPolicy, FullToolPolicy, NativeToolPolicy], _Field(discriminator="kind")]


class PermissionPolicy(FrozenModel):
    mode: _Literal["deny", "prompt", "allow"] = "deny"


class ElicitationPolicy(FrozenModel):
    mode: _Literal["deny", "allow"] = "deny"


class SamplingPolicy(FrozenModel):
    mode: _Literal["deny", "allow"] = "deny"


class FilesystemPolicy(FrozenModel):
    mode: _Literal["deny", "read_only", "read_write"] = "deny"


class TerminalPolicy(FrozenModel):
    mode: _Literal["deny", "allow"] = "deny"


class EvaluationRegistration(FrozenModel):
    name: str = _Field(min_length=1, max_length=256)
    required: bool = False


class DirectOperationBase(FrozenModel):
    """Serializable selector shared by one direct MCP operation."""

    server: str | None = _Field(default=None, min_length=1, max_length=256)


class ListToolsOperation(DirectOperationBase):
    kind: _Literal["list_tools"] = "list_tools"
    cursor: str | None = _Field(default=None, max_length=256)
    all_pages: bool = True


class ListResourcesOperation(DirectOperationBase):
    kind: _Literal["list_resources"] = "list_resources"
    cursor: str | None = _Field(default=None, max_length=256)
    all_pages: bool = True


class ListResourceTemplatesOperation(DirectOperationBase):
    kind: _Literal["list_resource_templates"] = "list_resource_templates"
    cursor: str | None = _Field(default=None, max_length=256)
    all_pages: bool = True


class ListPromptsOperation(DirectOperationBase):
    kind: _Literal["list_prompts"] = "list_prompts"
    cursor: str | None = _Field(default=None, max_length=256)
    all_pages: bool = True


class CallToolOperation(DirectOperationBase):
    kind: _Literal["call_tool"] = "call_tool"
    name: str = _Field(min_length=1, max_length=256)
    arguments: _Mapping[str, _Any] = _Field(default_factory=dict)


class ReadResourceOperation(DirectOperationBase):
    kind: _Literal["read_resource"] = "read_resource"
    uri: str = _Field(min_length=1, max_length=4096)


class GetPromptOperation(DirectOperationBase):
    kind: _Literal["get_prompt"] = "get_prompt"
    name: str = _Field(min_length=1, max_length=256)
    arguments: _Mapping[str, _Any] = _Field(default_factory=dict)


class PingOperation(DirectOperationBase):
    kind: _Literal["ping"] = "ping"


DirectOperation = _Annotated[
    _Union[
        ListToolsOperation,
        ListResourcesOperation,
        ListResourceTemplatesOperation,
        ListPromptsOperation,
        CallToolOperation,
        ReadResourceOperation,
        GetPromptOperation,
        PingOperation,
    ],
    _Field(discriminator="kind"),
]


class BaseExecutionSpec(FrozenModel):
    run_id: RunId | None = None
    # Optional logical case identity. It is stable across repeated trials;
    # execution_id remains the identity of one attempt.
    case_id: str | None = _Field(default=None, min_length=1, max_length=256)
    servers: tuple[ServerBinding, ...] = ()
    protocol: ProtocolConstraint = _Field(default_factory=ProtocolConstraint)
    timeout_seconds: float | None = _Field(default=None, gt=0)
    goal: str | None = _Field(default=None, max_length=32768)
    evaluations: tuple[EvaluationRegistration, ...] = ()
    artifact_policy: ArtifactPolicy = ArtifactPolicy.FAILED
    # Relative paths explicitly promoted to persisted, redacted artifacts.
    # Other changed files remain visible in the workspace diff only.
    declared_artifacts: tuple[str, ...] = ()
    workspace: WorkspacePolicy = _Field(default_factory=WorkspacePolicy)
    tool_policy: ToolPolicy = _Field(default_factory=RestrictiveToolPolicy)
    permission_policy: PermissionPolicy = _Field(default_factory=PermissionPolicy)
    elicitation_policy: ElicitationPolicy = _Field(default_factory=ElicitationPolicy)
    sampling_policy: SamplingPolicy = _Field(default_factory=SamplingPolicy)
    filesystem_policy: FilesystemPolicy = _Field(default_factory=FilesystemPolicy)
    terminal_policy: TerminalPolicy = _Field(default_factory=TerminalPolicy)
    metadata: _Mapping[str, str | int | float | bool | None] = _Field(default_factory=dict)

    @_model_validator(mode="after")
    def _validate_serializable_bindings(self) -> "BaseExecutionSpec":
        if not self.servers:
            raise ValueError("execution spec requires at least one ordered server binding")
        if any(isinstance(binding.server, InProcessServer) for binding in self.servers):
            raise ValueError("InProcessServer factories are runtime registrations, not serializable execution specs")
        for artifact in self.declared_artifacts:
            if (
                not artifact
                or artifact.startswith(("/", "\\"))
                or "\\" in artifact
                or any(part in {"", ".", ".."} for part in artifact.split("/"))
            ):
                raise ValueError("declared artifact paths must be safely relative")
        return self


class DirectExecutionSpec(BaseExecutionSpec):
    kind: _Literal["direct"] = "direct"
    operation: DirectOperation
    validate_schemas: bool = False

    @_model_validator(mode="after")
    def _validate_operation_server_selection(self) -> "DirectExecutionSpec":
        # A direct server's name is its default selector; profile bindings do
        # not have a resolved server name yet, so their profile id is the
        # stable fallback unless the author supplies an alias.  Compare these
        # effective selectors, rather than aliases alone, to catch collisions
        # such as ``alias='echo'`` next to a server named ``echo``.
        effective_selectors = tuple(
            binding.alias
            or (binding.server.name if binding.server is not None else binding.profile.profile_id.root if binding.profile is not None else None)
            for binding in self.servers
        )
        known_selectors = tuple(selector for selector in effective_selectors if selector is not None)
        if len(set(known_selectors)) != len(known_selectors):
            raise ValueError("direct execution servers must have unique aliases")
        selector = self.operation.server
        if len(self.servers) > 1 and selector is None:
            raise ValueError("direct operation server selector is required when multiple servers are bound")
        if selector is not None and selector not in known_selectors:
            raise ValueError("direct operation server selector does not match a configured server")
        return self


class AgentExecutionSpec(BaseExecutionSpec):
    kind: _Literal["agent"] = "agent"
    harness: HarnessSpec | None = None
    harness_profile: HarnessProfileRef | None = None
    message: UserMessage | None = None

    @_model_validator(mode="after")
    def _one_harness_source(self) -> "AgentExecutionSpec":
        if (self.harness is None) == (self.harness_profile is None):
            raise ValueError("agent execution spec requires exactly one of harness or harness_profile")
        return self


ExecutionSpec = _Annotated[_Union[DirectExecutionSpec, AgentExecutionSpec], _Field(discriminator="kind")]


class SessionForkRequest(FrozenModel):
    """Explicit inputs for creating a portable child execution."""

    mode: _Literal["fork", "replay"] = "replay"
    replay_inputs: tuple[UserMessage, ...] = ()
    source_turn_id: TurnId | None = None
    metadata: _Mapping[str, str | int | float | bool | None] = _Field(default_factory=dict)

    @_model_validator(mode="after")
    def _explicit_inputs(self) -> "SessionForkRequest":
        if not self.replay_inputs:
            raise ValueError("fork/replay requires explicit replay inputs")
        return self


class SessionProvenance(FrozenModel):
    """Immutable link from a new session to its terminal source execution."""

    mode: _Literal["fork", "replay"]
    source_execution_id: ExecutionId
    source_session_id: SessionId
    source_turn_id: TurnId | None = None


class ExecutionSnapshot(FrozenModel):
    execution_id: ExecutionId
    run_id: RunId | None = None
    lifecycle: LifecycleState = LifecycleState.CREATED
    outcome: ExecutionOutcome | None = None
    sequence: int = _Field(default=0, ge=0)
    created_at: _datetime = _Field(default_factory=_utc_now)
    finished_at: _datetime | None = None
    provenance: SessionProvenance | None = None

    @_model_validator(mode="after")
    def _terminal_consistency(self) -> "ExecutionSnapshot":
        if self.lifecycle is LifecycleState.FINISHED and self.outcome is None:
            raise ValueError("finished execution requires an outcome")
        if self.lifecycle is LifecycleState.FINISHED and self.finished_at is None:
            raise ValueError("finished execution requires finished_at")
        if self.lifecycle is not LifecycleState.FINISHED and self.outcome is not None:
            raise ValueError("non-finished execution cannot have an outcome")
        if self.lifecycle is not LifecycleState.FINISHED and self.finished_at is not None:
            raise ValueError("non-finished execution cannot have finished_at")
        return self

    def transition(self, lifecycle: LifecycleState, outcome: ExecutionOutcome | None = None) -> "ExecutionSnapshot":
        lifecycle = LifecycleState(lifecycle)
        outcome = ExecutionOutcome(outcome) if outcome is not None else None
        transitions = {
            LifecycleState.CREATED: {LifecycleState.QUEUED, LifecycleState.STARTING, LifecycleState.FINISHED},
            LifecycleState.QUEUED: {LifecycleState.STARTING, LifecycleState.FINISHED},
            LifecycleState.STARTING: {LifecycleState.IDLE, LifecycleState.RUNNING_TURN, LifecycleState.CLOSING, LifecycleState.FINISHED},
            LifecycleState.IDLE: {LifecycleState.RUNNING_TURN, LifecycleState.CLOSING, LifecycleState.FINISHED},
            LifecycleState.RUNNING_TURN: {LifecycleState.IDLE, LifecycleState.CLOSING, LifecycleState.FINISHED},
            LifecycleState.CLOSING: {LifecycleState.FINISHED},
            LifecycleState.FINISHED: set(),
        }
        if lifecycle not in transitions[self.lifecycle]:
            raise _InvalidTransitionError(f"execution cannot transition {self.lifecycle.value} → {lifecycle.value}")
        if lifecycle is not LifecycleState.FINISHED and outcome is not None:
            raise _ModelValidationError("non-finished execution cannot have an outcome")
        if lifecycle is LifecycleState.FINISHED and outcome is None:
            raise _ModelValidationError("finished execution requires an outcome")
        values = self.model_dump(mode="python")
        values.update(
            lifecycle=lifecycle,
            outcome=outcome,
            sequence=self.sequence + 1,
            finished_at=_utc_now() if lifecycle is LifecycleState.FINISHED else self.finished_at,
        )
        return type(self).model_validate(values)


class ExecutionPage(FrozenModel):
    """Bounded, stable page of persisted execution snapshots."""

    items: tuple[ExecutionSnapshot, ...] = ()
    limit: int = _Field(default=50, ge=1, le=100)
    offset: int = _Field(default=0, ge=0)
    total: int = _Field(default=0, ge=0)


class TurnSnapshot(FrozenModel):
    turn_id: TurnId
    session_id: SessionId
    number: int = _Field(ge=1)
    lifecycle: TurnLifecycle = TurnLifecycle.QUEUED
    outcome: TurnOutcome | None = None
    created_at: _datetime = _Field(default_factory=_utc_now)
    finished_at: _datetime | None = None

    @_model_validator(mode="after")
    def _terminal_consistency(self) -> "TurnSnapshot":
        if self.lifecycle is TurnLifecycle.FINISHED and self.outcome is None:
            raise ValueError("finished turn requires an outcome")
        if self.lifecycle is TurnLifecycle.FINISHED and self.finished_at is None:
            raise ValueError("finished turn requires finished_at")
        if self.lifecycle is not TurnLifecycle.FINISHED and self.outcome is not None:
            raise ValueError("non-finished turn cannot have an outcome")
        if self.lifecycle is not TurnLifecycle.FINISHED and self.finished_at is not None:
            raise ValueError("non-finished turn cannot have finished_at")
        return self

    def transition(self, lifecycle: TurnLifecycle, outcome: TurnOutcome | None = None) -> "TurnSnapshot":
        lifecycle = TurnLifecycle(lifecycle)
        outcome = TurnOutcome(outcome) if outcome is not None else None
        transitions = {
            TurnLifecycle.QUEUED: {TurnLifecycle.RUNNING, TurnLifecycle.FINISHED},
            TurnLifecycle.RUNNING: {TurnLifecycle.FINISHED},
            TurnLifecycle.FINISHED: set(),
        }
        if lifecycle not in transitions[self.lifecycle]:
            raise _InvalidTransitionError(f"turn cannot transition {self.lifecycle.value} → {lifecycle.value}")
        if lifecycle is not TurnLifecycle.FINISHED and outcome is not None:
            raise _ModelValidationError("non-finished turn cannot have an outcome")
        if lifecycle is TurnLifecycle.FINISHED and outcome is None:
            raise _ModelValidationError("finished turn requires an outcome")
        values = self.model_dump(mode="python")
        values.update(
            lifecycle=lifecycle,
            outcome=outcome,
            finished_at=_utc_now() if lifecycle is TurnLifecycle.FINISHED else self.finished_at,
        )
        return type(self).model_validate(values)


class EventKind(str, _Enum):
    """Closed taxonomy for canonical harness-neutral events."""

    EXECUTION_CREATED = "execution.created"
    EXECUTION_STATE_CHANGED = "execution.state_changed"
    EXECUTION_FINISHED = "execution.finished"
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


class RawEvidenceRef(FrozenModel):
    evidence_id: str = _Field(min_length=1, max_length=256)
    sha256: str | None = _Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = _Field(default=None, ge=0)
    media_type: str | None = _Field(default=None, max_length=256)
    storage_key: str | None = _Field(default=None, min_length=1, max_length=1024)


class EventPayloadRef(FrozenModel):
    blob_id: str = _Field(min_length=1, max_length=256)
    sha256: str = _Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = _Field(ge=0)
    media_type: str = _Field(default="application/json", max_length=256)
    compression: str | None = _Field(default=None, max_length=64)


class EventProvenance(FrozenModel):
    origin: EventOrigin
    source: str = _Field(min_length=1, max_length=256)
    provider_kind: str | None = _Field(default=None, max_length=256)
    derived_from_sequence: int | None = _Field(default=None, ge=0)


class ReasoningState(FrozenModel):
    visibility: ReasoningVisibility
    explicit: bool = False
    payload_ref: EventPayloadRef | None = None

    @_model_validator(mode="after")
    def _validate_visibility(self) -> "ReasoningState":
        if self.visibility is ReasoningVisibility.VISIBLE and not self.explicit:
            raise ValueError("visible reasoning must be explicitly emitted")
        if self.visibility is not ReasoningVisibility.VISIBLE and self.explicit:
            raise ValueError("unavailable or hidden reasoning cannot be marked explicit")
        if self.visibility in {ReasoningVisibility.UNAVAILABLE, ReasoningVisibility.PROVIDER_HIDDEN} and self.payload_ref is not None:
            raise ValueError("unavailable or provider-hidden reasoning cannot carry a payload reference")
        return self


class RequestCorrelation(FrozenModel):
    jsonrpc_id: JsonRpcId | None = None
    direction: EventDirection
    request_sequence: int | None = _Field(default=None, ge=1)

    @_field_validator("jsonrpc_id", mode="before")
    @classmethod
    def _reject_bool_ids(cls, value: _Any) -> _Any:
        if isinstance(value, bool):
            raise ValueError("JSON-RPC IDs cannot be boolean")
        return value


class CanonicalEvent(FrozenModel):
    schema_id: _Literal["mcp_pal.event"] = _Field(default="mcp_pal.event", alias="schema")
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
    correlation: RequestCorrelation | None = None
    lifecycle_phase: LifecyclePhase = LifecyclePhase.UNKNOWN
    payload: _Mapping[str, _Any] = _Field(default_factory=dict)
    payload_ref: EventPayloadRef | None = None
    provenance: EventProvenance = _Field(
        default_factory=lambda: EventProvenance(origin=EventOrigin.NORMALIZED, source="mcp_pal")
    )
    raw_evidence_ref: RawEvidenceRef | None = _Field(
        default=None,
        validation_alias=_AliasChoices("raw_evidence_ref", "raw_evidence"),
    )
    reasoning: ReasoningState | None = None

    @_field_validator("schema_id")
    @classmethod
    def _schema_is_canonical(cls, value: str) -> str:
        if value != EVENT_SCHEMA_ID:
            raise ValueError("schema must be 'mcp_pal.event'")
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
    def _correlation_requires_connection(self) -> "CanonicalEvent":
        if self.correlation is not None and self.correlation.request_sequence is not None and self.connection_id is None:
            raise ValueError("request sequence requires a connection identity")
        if self.kind is EventKind.REASONING and self.reasoning is None:
            raise ValueError("reasoning events require an explicit visibility state")
        return self


CanonicalEventEnvelope = CanonicalEvent


class TraceResult(FrozenModel):
    trace_id: TraceId
    execution_id: ExecutionId
    completeness: _Literal["complete", "partial"] = "complete"
    highest_sequence: int = _Field(default=0, ge=0)
    events: tuple[CanonicalEvent, ...] = ()
    limitations: tuple[str, ...] = ()

    def view(self) -> "_TraceView":
        """Project this finalized canonical trace into the typed view."""

        from .trace.projector import TraceProjector

        return TraceProjector.project(self)

    @_model_validator(mode="after")
    def _validate_trace_invariants(self) -> "TraceResult":
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
            for left, right in zip(self.events, self.events[1:])
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


class EvaluationContext(FrozenModel):
    subject: _Any = None
    subject_kind: str = "unknown"
    execution_id: ExecutionId | None = None
    case_id: str | None = _Field(default=None, min_length=1, max_length=256)
    turn_id: TurnId | None = None
    goal: str | None = None
    trace: TraceResult | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    metadata: _Mapping[str, str | int | float | bool | None] = _Field(default_factory=dict)


class EvaluationProvenance(FrozenModel):
    """Optional, redaction-safe provenance for a structured judgment."""

    kind: str = _Field(min_length=1, max_length=128)
    provider: str | None = _Field(default=None, max_length=256)
    model: str | None = _Field(default=None, max_length=256)
    rubric_id: str | None = _Field(default=None, max_length=256)
    rubric_version: str | None = _Field(default=None, max_length=128)
    config_digest: str | None = _Field(default=None, max_length=256)


class EvaluationDecision(FrozenModel):
    """Structured evaluator output, compatible with scalar verdicts."""

    status: EvaluationStatus
    score: float | None = None
    rationale: str | None = None
    metrics: _Mapping[str, float] = _Field(default_factory=dict)
    provenance: EvaluationProvenance | None = None

    @_field_validator("score", mode="before")
    @classmethod
    def _finite_score(cls, value: _Any) -> float | None:
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not _isfinite(float(value)) or not 0 <= float(value) <= 1):
            raise ValueError("score must be finite and between 0 and 1")
        return float(value) if value is not None else None

    @_field_validator("metrics", mode="before")
    @classmethod
    def _finite_metrics(cls, value: _Any) -> _Mapping[str, float]:
        if not isinstance(value, _Mapping):
            raise ValueError("metrics must be a mapping")
        if any(not key.strip() for key in value):
            raise ValueError("metric names must not be empty")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not _isfinite(float(item)) for item in value.values()):
            raise ValueError("metric values must be finite")
        return {key: float(item) for key, item in value.items()}


class EvaluationResult(FrozenModel):
    evaluation_id: EvaluationId
    name: str
    status: EvaluationStatus
    required: bool = False
    message: str | None = None
    context: EvaluationContext | None = None
    score: float | None = None
    rationale: str | None = None
    metrics: _Mapping[str, float] = _Field(default_factory=dict)
    provenance: EvaluationProvenance | None = None

    @_field_validator("score", mode="before")
    @classmethod
    def _strict_result_score(cls, value: _Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not _isfinite(float(value)) or not 0 <= float(value) <= 1:
            raise ValueError("score must be finite and between 0 and 1")
        return float(value)


class PersistedEvaluationRecord(FrozenModel):
    """Compact durable evaluation row linked to an execution report."""

    evaluation_id: EvaluationId
    execution_id: ExecutionId
    case_id: str | None = _Field(default=None, min_length=1, max_length=256)
    turn_id: TurnId | None = None
    name: str
    status: EvaluationStatus
    required: bool = False
    message: str | None = None
    score: float | None = None
    rationale: str | None = None
    metrics: _Mapping[str, float] = _Field(default_factory=dict)
    provenance: EvaluationProvenance | None = None
    goal: str | None = None
    metadata: _Mapping[str, str | int | float | bool | None] = _Field(default_factory=dict)
    subject_kind: str = "unknown"
    subject_digest: str | None = _Field(default=None, pattern=r"^[0-9a-f]{64}$")
    run_id: RunId | None = None
    created_at: _datetime = _Field(default_factory=_utc_now)


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
        return "".join(block.text for block in self.content if isinstance(block, TextContent))


class TurnResult(FrozenModel):
    snapshot: TurnSnapshot
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
    def _requires_terminal_snapshot(self) -> "TurnResult":
        if self.snapshot.lifecycle is not TurnLifecycle.FINISHED:
            raise ValueError("turn result requires a terminal turn snapshot")
        return self


class DirectTool(FrozenModel):
    # Process-local official MCP evidence; excluded from public serialization.
    raw: _Any = _Field(default=None, exclude=True, repr=False)
    name: str = _Field(min_length=1, max_length=256)
    title: str | None = None
    description: str | None = None
    input_schema: _Mapping[str, _Any] | bool = _Field(default_factory=dict)
    output_schema: _Mapping[str, _Any] | bool | None = None


class DirectResource(FrozenModel):
    raw: _Any = _Field(default=None, exclude=True, repr=False)
    name: str = _Field(min_length=1, max_length=256)
    title: str | None = None
    uri: str = _Field(min_length=1, max_length=4096)
    description: str | None = None
    mime_type: str | None = None
    size: int | None = _Field(default=None, ge=0)


class DirectResourceTemplate(FrozenModel):
    raw: _Any = _Field(default=None, exclude=True, repr=False)
    name: str = _Field(min_length=1, max_length=256)
    title: str | None = None
    uri_template: str = _Field(min_length=1, max_length=4096)
    description: str | None = None
    mime_type: str | None = None


class DirectPrompt(FrozenModel):
    raw: _Any = _Field(default=None, exclude=True, repr=False)
    name: str = _Field(min_length=1, max_length=256)
    title: str | None = None
    description: str | None = None
    arguments: tuple[_Mapping[str, _Any], ...] = ()


class DirectOperationResultBase(FrozenModel):
    """Typed direct result base retaining process-local official MCP output."""

    # The official response is available to in-process callers, but is never
    # part of durable/public JSON evidence.
    raw: _Any = _Field(default=None, exclude=True, repr=False)


class ListToolsOperationResult(DirectOperationResultBase):
    kind: _Literal["list_tools"] = "list_tools"
    tools: tuple[DirectTool, ...] = ()
    next_cursor: str | None = None


class ListResourcesOperationResult(DirectOperationResultBase):
    kind: _Literal["list_resources"] = "list_resources"
    resources: tuple[DirectResource, ...] = ()
    next_cursor: str | None = None


class ListResourceTemplatesOperationResult(DirectOperationResultBase):
    kind: _Literal["list_resource_templates"] = "list_resource_templates"
    resource_templates: tuple[DirectResourceTemplate, ...] = ()
    next_cursor: str | None = None


class ListPromptsOperationResult(DirectOperationResultBase):
    kind: _Literal["list_prompts"] = "list_prompts"
    prompts: tuple[DirectPrompt, ...] = ()
    next_cursor: str | None = None


class CallToolOperationResult(DirectOperationResultBase):
    kind: _Literal["call_tool"] = "call_tool"
    content: tuple[_Mapping[str, _Any], ...] = ()
    structured_content: _Any = None
    is_error: bool = False


class ReadResourceOperationResult(DirectOperationResultBase):
    kind: _Literal["read_resource"] = "read_resource"
    contents: tuple[_Mapping[str, _Any], ...] = ()


class GetPromptOperationResult(DirectOperationResultBase):
    kind: _Literal["get_prompt"] = "get_prompt"
    description: str | None = None
    messages: tuple[_Mapping[str, _Any], ...] = ()


class PingOperationResult(DirectOperationResultBase):
    kind: _Literal["ping"] = "ping"
    result_type: str | None = None


DirectOperationResult = _Annotated[
    _Union[
        ListToolsOperationResult,
        ListResourcesOperationResult,
        ListResourceTemplatesOperationResult,
        ListPromptsOperationResult,
        CallToolOperationResult,
        ReadResourceOperationResult,
        GetPromptOperationResult,
        PingOperationResult,
    ],
    _Field(discriminator="kind"),
]


class ExecutionResult(FrozenModel):
    snapshot: ExecutionSnapshot
    turns: tuple[TurnResult, ...] = ()
    trace: TraceResult | None = None
    direct_result: DirectOperationResult | None = None
    evaluations: tuple[EvaluationResult, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    activity_health: ActivityHealth = ActivityHealth.NO_CALLS
    error: ErrorInfo | None = None
    provenance: SessionProvenance | None = None

    @property
    def trace_view(self) -> "_TraceView":
        """Return the finalized typed view for this execution trace."""

        if self.trace is None:
            from .errors import TraceUnavailable

            raise TraceUnavailable("execution has no trace evidence")
        return self.trace.view()

    @_model_validator(mode="after")
    def _requires_terminal_snapshot(self) -> "ExecutionResult":
        if self.snapshot.lifecycle is not LifecycleState.FINISHED:
            raise ValueError("execution result requires a terminal execution snapshot")
        return self


class ExecutionEvidence(FrozenModel):
    """Typed completeness markers persisted in canonical terminal events."""

    completeness: _Literal["complete", "partial"]
    limitations: tuple[str, ...] = ()
    reason: str | None = None

    @_model_validator(mode="after")
    def _validate_completeness(self) -> "ExecutionEvidence":
        if self.completeness == "complete" and self.limitations:
            raise ValueError("complete evidence cannot declare limitations")
        if self.completeness == "partial" and not self.limitations:
            raise ValueError("partial evidence must declare limitations")
        return self


class PersistedExecutionReport(FrozenModel):
    """Portable evidence that is actually persisted by an execution store."""

    snapshot: ExecutionSnapshot
    events: tuple[CanonicalEvent, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    direct_result: DirectOperationResult | None = None
    error: ErrorInfo | None = None
    evidence: ExecutionEvidence | None = None
    turns: tuple[TurnResult, ...] = ()
    evaluations: tuple[PersistedEvaluationRecord, ...] = ()
    event_count: int = _Field(default=0, ge=0)
    events_truncated: bool = False
    next_after_sequence: int | None = _Field(default=None, ge=-1)
    artifact_count: int = _Field(default=0, ge=0)
    artifacts_truncated: bool = False

    @_model_validator(mode="after")
    def _validate_projection(self) -> "PersistedExecutionReport":
        if any(event.execution_id != self.snapshot.execution_id for event in self.events):
            raise ValueError("report events must belong to its execution")
        if any(evaluation.execution_id != self.snapshot.execution_id for evaluation in self.evaluations):
            raise ValueError("report evaluations must belong to its execution")
        if any(left.sequence >= right.sequence for left, right in zip(self.events, self.events[1:])):
            raise ValueError("report events must be ordered")
        if len(self.events) > self.event_count or (not self.events_truncated and len(self.events) != self.event_count):
            raise ValueError("report event count is inconsistent with its projection")
        if len(self.artifacts) > self.artifact_count or (not self.artifacts_truncated and len(self.artifacts) != self.artifact_count):
            raise ValueError("report artifact count is inconsistent with its projection")
        return self


__all__ = [
    "EVENT_SCHEMA_ID", "EVENT_SCHEMA_VERSION", "ACPAgent", "ActivityHealth", "AgentExecutionSpec", "ArtifactId", "ArtifactPolicy", "ArtifactRef",
    "AudioContent", "BaseExecutionSpec", "CanonicalEvent", "CanonicalEventEnvelope", "Capability", "CapabilityStatus", "ClaudeCode",
    "ConnectionId", "ContentBlock", "DirectExecutionSpec", "DirectOperation", "DirectOperationBase", "DirectOperationResultBase", "DirectTool", "DirectResource", "DirectResourceTemplate", "DirectPrompt", "ElicitationPolicy", "ErrorCode", "ErrorInfo",
    "EventDirection", "EventKind", "EventOrigin", "EventPayloadRef", "EventProvenance",
    "EvaluationContext", "EvaluationDecision", "EvaluationId", "EvaluationProvenance", "EvaluationRegistration", "EvaluationResult", "EvaluationStatus", "PersistedEvaluationRecord", "RunId",
    "EventId", "ExecutionId", "ExecutionEvidence", "ExecutionOutcome", "ExecutionPage", "ExecutionResult", "ExecutionSnapshot", "ExecutionSpec", "FileContent",
    "FilesystemPolicy", "FrozenModel", "FullToolPolicy", "HarnessId", "HarnessProfileId", "HarnessProfileRef",
    "HarnessSpec", "HarnessValue", "Identifier", "ImageContent", "InProcessServer", "LifecycleState",
    "JsonRpcId", "LifecyclePhase", "Metadata", "NativeToolPolicy", "OpaqueContent", "OpenCode", "PermissionPolicy", "ProtocolConstraint",
    "Readiness", "ResourceLinkContent", "RevisionId", "RevisionSelection", "RestrictiveToolPolicy", "SSEServer", "SamplingPolicy", "SecretReference",
    "ServerBinding", "ServerDefinition", "ServerId", "ServerProfileId", "ServerProfileRef", "ServerValue", "SessionForkRequest", "SessionId", "SessionProvenance", "PersistedExecutionReport",
    "RawEvidenceRef", "ReasoningState", "ReasoningVisibility", "RequestCorrelation", "StdioServer", "StreamableHTTPServer", "TerminalPolicy", "TextContent", "TraceId", "TraceResult",
    "ToolPolicy", "TransportKind", "TrustLevel", "TurnId", "TurnLifecycle", "TurnOutcome", "TurnResponse", "TurnResult", "TurnSnapshot",
    "UserMessage", "WorkspaceKind", "WorkspacePolicy", "ListToolsOperation", "ListResourcesOperation", "ListResourceTemplatesOperation", "ListPromptsOperation", "CallToolOperation", "ReadResourceOperation", "GetPromptOperation", "PingOperation", "DirectOperationResult", "ListToolsOperationResult", "ListResourcesOperationResult", "ListResourceTemplatesOperationResult", "ListPromptsOperationResult", "CallToolOperationResult", "ReadResourceOperationResult", "GetPromptOperationResult", "PingOperationResult",
]
