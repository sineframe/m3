from __future__ import annotations

from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Sequence
from typing import Annotated as _Annotated
from typing import Any as _Any
from typing import Literal as _Literal

from pydantic import Field as _Field
from pydantic import field_validator as _field_validator
from pydantic import model_serializer as _model_serializer
from pydantic import model_validator as _model_validator

from ..elicitation import ElicitationPlan as _ElicitationPlan
from .base import (
    ArtifactPolicy,
    AudioContent,
    ExecutionId,
    FileContent,
    FrozenModel,
    HarnessProfileId,
    ImageContent,
    OpaqueContent,
    ProjectId,
    ProtocolConstraint,
    ResourceLink,
    RevisionSelection,
    RunId,
    SecretReference,
    ServerProfileId,
    SessionId,
    TextContent,
    TrustLevel,
    TurnId,
    WorkspaceKind,
)

ContentBlock = _Annotated[
    TextContent
    | FileContent
    | ImageContent
    | AudioContent
    | ResourceLink
    | OpaqueContent,
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
        if isinstance(value, _Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return tuple(
                TextContent(text=item) if isinstance(item, str) else item
                for item in value
            )
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


class HTTPServer(ServerDefinition):
    kind: _Literal["streamable_http"] = "streamable_http"
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
        raise TypeError(
            "in-process server factories are runtime registrations and cannot be pickled"
        )


ServerValue = _Annotated[
    StdioServer | HTTPServer | InProcessServer,
    _Field(discriminator="kind"),
]


class ServerProfileRef(FrozenModel):
    profile_id: ServerProfileId
    # The logical server selector is part of the reference, rather than being
    # inferred from mutable profile metadata.  This lets a submitted spec
    # retain a stable selector after the profile is resolved.
    server_name: str = _Field(min_length=1, max_length=256)
    revision: RevisionSelection


class ServerBinding(FrozenModel):
    server: ServerValue | None = None
    profile: ServerProfileRef | None = None
    alias: str | None = _Field(default=None, min_length=1, max_length=256)
    required: bool = True

    @_model_validator(mode="after")
    def _one_binding_source(self) -> ServerBinding:
        if (self.server is None) == (self.profile is None):
            raise ValueError("server binding requires exactly one of server or profile")
        return self


class HarnessValue(FrozenModel):
    name: str = _Field(min_length=1, max_length=128)
    model: str = _Field(min_length=1, max_length=512)
    executable: str | None = None
    runtime: _Literal["system", "managed"] = "system"
    version: str | None = _Field(default=None, min_length=1, max_length=64)

    @_field_validator("runtime", mode="before")
    @classmethod
    def _none_runtime_is_system(cls, value: _Any) -> _Any:
        return "system" if value is None else value

    @_model_validator(mode="after")
    def _validate_runtime(self) -> HarnessValue:
        import re

        if self.version is not None and self.runtime != "managed":
            raise ValueError("harness version requires managed runtime")
        if self.version == "latest":
            return self
        if (
            self.version is not None
            and re.fullmatch(
                r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9a-z]+(?:\.[0-9a-z]+)*)?",
                self.version,
            )
            is None
        ):
            raise ValueError("harness version is invalid")
        return self

    @_model_serializer(mode="wrap")
    def _serialize_runtime_fields(self, handler: _Any) -> dict[str, _Any]:
        value = dict(handler(self))
        if value.get("runtime") in {None, "system"}:
            value.pop("runtime", None)
        if value.get("version") is None:
            value.pop("version", None)
        return value

    def with_model(self, model: str) -> HarnessValue:
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
    def _valid_credential_names(
        cls, values: _Mapping[str, SecretReference]
    ) -> _Mapping[str, SecretReference]:
        import re

        if any(
            not isinstance(key, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None
            for key in values
        ):
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
    def _valid_credential_names(
        cls, values: _Mapping[str, SecretReference]
    ) -> _Mapping[str, SecretReference]:
        import re

        if any(
            not isinstance(key, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None
            for key in values
        ):
            raise ValueError("OpenCode credential target is invalid")
        return values


class Codex(HarnessValue):
    """Native OpenAI Codex App Server harness profile."""

    kind: _Literal["codex"] = "codex"
    name: str = "codex"
    credential_references: _Mapping[str, SecretReference] = _Field(default_factory=dict)

    @_field_validator("credential_references")
    @classmethod
    def _valid_credential_names(
        cls, values: _Mapping[str, SecretReference]
    ) -> _Mapping[str, SecretReference]:
        import re

        if any(
            not isinstance(key, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None
            for key in values
        ):
            raise ValueError("Codex credential target is invalid")
        return values


class Pi(HarnessValue):
    """Native Pi RPC harness profile."""

    kind: _Literal["pi"] = "pi"
    name: str = "pi"
    provider: str | None = None
    credential_references: _Mapping[str, SecretReference] = _Field(default_factory=dict)

    @_field_validator("credential_references")
    @classmethod
    def _valid_credential_names(
        cls, values: _Mapping[str, SecretReference]
    ) -> _Mapping[str, SecretReference]:
        import re

        if any(
            not isinstance(key, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None
            for key in values
        ):
            raise ValueError("Pi credential target is invalid")
        return values


class ACPAgent(HarnessValue):
    kind: _Literal["acp"] = "acp"
    name: str = "acp"
    manifest: _Mapping[str, _Any] = _Field(default_factory=dict)
    agent_mode_id: str | None = _Field(default=None, min_length=1, max_length=256)
    session_config: _Mapping[str, _Any] = _Field(default_factory=dict)

    @_model_validator(mode="after")
    def _managed_runtime_unsupported(self) -> ACPAgent:
        if self.runtime == "managed":
            raise ValueError("managed runtime is supported only for native harnesses")
        return self

    @_field_validator("session_config")
    @classmethod
    def _valid_session_config(cls, values: _Mapping[str, _Any]) -> _Mapping[str, _Any]:
        if any(not isinstance(key, str) or not key for key in values):
            raise ValueError("ACP session configuration keys must be non-empty text")
        if any(not isinstance(value, (str, bool)) for value in values.values()):
            raise ValueError("ACP session configuration values must be text or boolean")
        return values


HarnessSpec = _Annotated[
    ClaudeCode | OpenCode | Codex | Pi | ACPAgent, _Field(discriminator="kind")
]


class HarnessProfileRef(FrozenModel):
    profile_id: HarnessProfileId
    revision: RevisionSelection


class WorkspacePolicy(FrozenModel):
    kind: WorkspaceKind = WorkspaceKind.TEMPORARY
    source: str | None = None
    acknowledge_risk: bool = False

    @_model_validator(mode="after")
    def _validate_workspace(self) -> WorkspacePolicy:
        if (
            self.kind
            in {WorkspaceKind.COPY, WorkspaceKind.GIT_WORKTREE, WorkspaceKind.READ_ONLY}
            and not self.source
        ):
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
    def _risk_acknowledged(self) -> FullToolPolicy:
        if not self.acknowledge_risk:
            raise ValueError("FullToolPolicy requires acknowledge_risk=True")
        return self


class NativeToolPolicy(FrozenModel):
    kind: _Literal["native"] = "native"
    harness: str = _Field(min_length=1)
    policy: _Mapping[str, _Any]
    nonportable_reason: str = _Field(min_length=1)


ToolPolicy = _Annotated[
    RestrictiveToolPolicy | FullToolPolicy | NativeToolPolicy,
    _Field(discriminator="kind"),
]


class PermissionPolicy(FrozenModel):
    mode: _Literal["deny", "prompt", "allow"] = "deny"


class SamplingPolicy(FrozenModel):
    mode: _Literal["deny", "allow"] = "deny"


class FilesystemPolicy(FrozenModel):
    mode: _Literal["deny", "read_only", "read_write"] = "deny"


class TerminalPolicy(FrozenModel):
    mode: _Literal["deny", "allow"] = "deny"


class EvaluationRegistration(FrozenModel):
    name: str = _Field(min_length=1, max_length=256)
    required: bool = False


class _DirectOperation(FrozenModel):
    """Serializable selector shared by one direct MCP operation."""

    server: str | None = _Field(default=None, min_length=1, max_length=256)


class ListTools(_DirectOperation):
    kind: _Literal["list_tools"] = "list_tools"
    cursor: str | None = _Field(default=None, max_length=256)
    all_pages: bool = True


class ListResources(_DirectOperation):
    kind: _Literal["list_resources"] = "list_resources"
    cursor: str | None = _Field(default=None, max_length=256)
    all_pages: bool = True


class ListTemplates(_DirectOperation):
    kind: _Literal["list_resource_templates"] = "list_resource_templates"
    cursor: str | None = _Field(default=None, max_length=256)
    all_pages: bool = True


class ListPrompts(_DirectOperation):
    kind: _Literal["list_prompts"] = "list_prompts"
    cursor: str | None = _Field(default=None, max_length=256)
    all_pages: bool = True


class CallTool(_DirectOperation):
    kind: _Literal["call_tool"] = "call_tool"
    name: str = _Field(min_length=1, max_length=256)
    arguments: _Mapping[str, _Any] = _Field(default_factory=dict)


class ReadResource(_DirectOperation):
    kind: _Literal["read_resource"] = "read_resource"
    uri: str = _Field(min_length=1, max_length=4096)


class GetPrompt(_DirectOperation):
    kind: _Literal["get_prompt"] = "get_prompt"
    name: str = _Field(min_length=1, max_length=256)
    arguments: _Mapping[str, _Any] = _Field(default_factory=dict)


class Ping(_DirectOperation):
    kind: _Literal["ping"] = "ping"


DirectOperation = _Annotated[
    ListTools
    | ListResources
    | ListTemplates
    | ListPrompts
    | CallTool
    | ReadResource
    | GetPrompt
    | Ping,
    _Field(discriminator="kind"),
]


class _ExecutionSpecBase(FrozenModel):
    run_id: RunId | None = None
    project_id: ProjectId | None = None
    project_name: str | None = _Field(default=None, min_length=1, max_length=256)
    suite_name: str | None = _Field(default=None, min_length=1, max_length=256)
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
    sampling_policy: SamplingPolicy = _Field(default_factory=SamplingPolicy)
    filesystem_policy: FilesystemPolicy = _Field(default_factory=FilesystemPolicy)
    terminal_policy: TerminalPolicy = _Field(default_factory=TerminalPolicy)
    metadata: _Mapping[str, str | int | float | bool | None] = _Field(
        default_factory=dict
    )

    @_model_validator(mode="after")
    def _validate_serializable_bindings(self) -> _ExecutionSpecBase:
        if not self.servers:
            raise ValueError(
                "execution spec requires at least one ordered server binding"
            )
        if any(isinstance(binding.server, InProcessServer) for binding in self.servers):
            raise ValueError(
                "InProcessServer factories are runtime registrations, not serializable execution specs"
            )
        for artifact in self.declared_artifacts:
            if (
                not artifact
                or artifact.startswith(("/", "\\"))
                or "\\" in artifact
                or any(part in {"", ".", ".."} for part in artifact.split("/"))
            ):
                raise ValueError("declared artifact paths must be safely relative")
        return self


class DirectSpec(_ExecutionSpecBase):
    kind: _Literal["direct"] = "direct"
    operation: DirectOperation
    validate_schemas: bool = False

    @_model_validator(mode="after")
    def _validate_operation_server_selection(self) -> DirectSpec:
        # A direct server's name is its default selector; profile bindings do
        # not have a resolved server name yet, so their profile id is the
        # stable fallback unless the author supplies an alias.  Compare these
        # effective selectors, rather than aliases alone, to catch collisions
        # such as ``alias='echo'`` next to a server named ``echo``.
        effective_selectors = tuple(
            binding.alias
            or (
                binding.server.name
                if binding.server is not None
                else binding.profile.server_name
                if binding.profile is not None
                else None
            )
            for binding in self.servers
        )
        known_selectors = tuple(
            selector for selector in effective_selectors if selector is not None
        )
        if len(set(known_selectors)) != len(known_selectors):
            raise ValueError("direct execution servers must have unique aliases")
        selector = self.operation.server
        if len(self.servers) > 1 and selector is None:
            raise ValueError(
                "direct operation server selector is required when multiple servers are bound"
            )
        if selector is not None and selector not in known_selectors:
            raise ValueError(
                "direct operation server selector does not match a configured server"
            )
        return self


class AgentSpec(_ExecutionSpecBase):
    kind: _Literal["agent"] = "agent"
    harness: HarnessSpec | None = None
    harness_profile: HarnessProfileRef | None = None
    message: UserMessage | None = None
    elicitation: _ElicitationPlan | None = None
    elicitation_round_limit: int = 10

    @_field_validator("elicitation_round_limit", mode="before")
    @classmethod
    def _validate_elicitation_round_limit(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("elicitation_round_limit must be a positive integer")
        return value

    @_model_validator(mode="after")
    def _validate_elicitation_plan(self) -> AgentSpec:
        if self.elicitation is not None and not self.elicitation.is_complete:
            raise ValueError("elicitation plan must be complete")
        return self

    @_model_validator(mode="after")
    def _one_harness_source(self) -> AgentSpec:
        if (self.harness is None) == (self.harness_profile is None):
            raise ValueError(
                "agent execution spec requires exactly one of harness or harness_profile"
            )
        return self


ExecutionSpec = _Annotated[DirectSpec | AgentSpec, _Field(discriminator="kind")]


class SessionForkRequest(FrozenModel):
    """Explicit inputs for creating a portable child execution."""

    mode: _Literal["fork", "replay"] = "replay"
    replay_inputs: tuple[UserMessage, ...] = ()
    source_turn_id: TurnId | None = None
    metadata: _Mapping[str, str | int | float | bool | None] = _Field(
        default_factory=dict
    )

    @_model_validator(mode="after")
    def _explicit_inputs(self) -> SessionForkRequest:
        if not self.replay_inputs:
            raise ValueError("fork/replay requires explicit replay inputs")
        return self


class SessionSource(FrozenModel):
    """Immutable link from a new session to its terminal source execution."""

    mode: _Literal["fork", "replay"]
    source_execution_id: ExecutionId
    source_session_id: SessionId
    source_turn_id: TurnId | None = None


__all__ = [
    "ACPAgent",
    "AgentSpec",
    "CallTool",
    "ClaudeCode",
    "Codex",
    "ContentBlock",
    "DirectOperation",
    "DirectSpec",
    "EvaluationRegistration",
    "ExecutionSpec",
    "FilesystemPolicy",
    "FullToolPolicy",
    "GetPrompt",
    "HTTPServer",
    "HarnessProfileRef",
    "HarnessSpec",
    "HarnessValue",
    "InProcessServer",
    "ListPrompts",
    "ListResources",
    "ListTemplates",
    "ListTools",
    "NativeToolPolicy",
    "OpenCode",
    "PermissionPolicy",
    "Pi",
    "Ping",
    "ReadResource",
    "RestrictiveToolPolicy",
    "SamplingPolicy",
    "ServerBinding",
    "ServerDefinition",
    "ServerProfileRef",
    "ServerValue",
    "SessionForkRequest",
    "SessionSource",
    "StdioServer",
    "TerminalPolicy",
    "ToolPolicy",
    "UserMessage",
    "WorkspacePolicy",
]
