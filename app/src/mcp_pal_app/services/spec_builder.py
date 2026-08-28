"""Typed execution-spec construction for direct application clients."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING as _TYPE_CHECKING, Any, Mapping

from pydantic import BaseModel, Field, field_validator

from mcp_pal import (
    ACPAgent,
    AgentExecutionSpec,
    ClaudeCode,
    OpenCode,
    RevisionSelection,
    SecretReference,
    ServerBinding,
    SSEServer,
    StdioServer,
    StreamableHTTPServer,
    TextContent,
    TrustLevel,
    UserMessage,
)
from mcp_pal.storage import StorageConflict
from mcp_pal.types import NativeToolPolicy

if _TYPE_CHECKING:
    from mcp_pal.storage import ProfileRevisionRecord

from mcp_pal_app.services.profile_service import ProfileServiceError, ProfileStore
from mcp_pal_app.settings import Settings


class OneTurnRunDraft(BaseModel):
    """The intentionally narrow one-turn UI input mapped to a rich SDK spec."""

    profile_id: str = Field(min_length=1)
    profile_revision: RevisionSelection = Field(default_factory=lambda: RevisionSelection(mode="latest"))
    enabled_server: str = Field(min_length=1, max_length=256)
    harness: str = Field(default="claude-code", pattern="^(claude-code|opencode|acp)$")
    harness_profile_id: str | None = None
    harness_revision: RevisionSelection = Field(default_factory=lambda: RevisionSelection(mode="latest"))
    model: str = Field(min_length=1, max_length=512)
    prompt: str = Field(min_length=1, max_length=32768)
    expected_goal: str = Field(min_length=1, max_length=32768)
    tool_mode: str = Field(default="mcp_only", pattern="^(mcp_only|mcp_read_only|full|agent_default)$")
    agent_mode_id: str | None = None
    session_config: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = Field(default=None, gt=0)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("prompt", "expected_goal")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_CLAUDE_READ_ONLY_TOOLS = (
    "Agent",
    "Read",
    "Glob",
    "Grep",
    "LSP",
    "WebFetch",
    "WebSearch",
    "ToolSearch",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "TaskGet",
    "TaskList",
    "TaskOutput",
)
_OPENCODE_READ_ONLY_TOOLS = ("read", "glob", "grep", "lsp", "webfetch", "websearch")


def _secret_or_literal(value: Any) -> SecretReference | str:
    if isinstance(value, SecretReference):
        return value
    if isinstance(value, Mapping) and set(value) == {"source", "name"}:
        try:
            return SecretReference.model_validate(dict(value))
        except ValueError as exc:
            raise ProfileServiceError("MCP credential reference is invalid") from exc
    if isinstance(value, str):
        match = _ENV_REFERENCE.fullmatch(value)
        if match:
            return SecretReference(source="environment", name=match.group(1))
        return value
    raise ProfileServiceError("MCP credential values must be strings or references")


def _server(name: str, raw: Mapping[str, Any]) -> StdioServer | StreamableHTTPServer | SSEServer:
    typ = str(raw.get("type", "stdio")).lower()
    trust = TrustLevel(str(raw.get("trust", TrustLevel.UNTRUSTED.value)))
    if typ == "stdio":
        return StdioServer(
            name=name,
            trust=trust,
            command=str(raw.get("command", "")),
            args=tuple(str(item) for item in (raw.get("args") or ())),
            environment={str(key): _secret_or_literal(item) for key, item in (raw.get("env") or {}).items()},
            cwd=raw.get("cwd"),
        )
    headers = {str(key): _secret_or_literal(item) for key, item in (raw.get("headers") or {}).items()}
    if typ == "http":
        return StreamableHTTPServer(name=name, trust=trust, url=str(raw.get("url", "")), headers=headers)
    if typ == "sse":
        return SSEServer(name=name, trust=trust, url=str(raw.get("url", "")), headers=headers)
    raise ProfileServiceError(f"unsupported MCP transport: {typ}")


class ExecutionSpecBuilder:
    """Resolve app profile revisions into concrete, runnable public SDK specs."""

    def __init__(self, store: ProfileStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    def build(self, draft: OneTurnRunDraft) -> AgentExecutionSpec:
        profile = self.store.get_profile(draft.profile_id)
        if profile is None or profile.kind != "server":
            raise ProfileServiceError("MCP profile does not exist")
        if profile.archived:
            raise ProfileServiceError("MCP profile is archived")
        try:
            revision = self.store.resolve_revision(draft.profile_id, draft.profile_revision)
        except StorageConflict as exc:
            raise ProfileServiceError("MCP profile revision does not exist") from exc
        config = revision.value
        servers = config.get("mcpServers") if isinstance(config, Mapping) else None
        raw = servers.get(draft.enabled_server) if isinstance(servers, Mapping) else None
        if not isinstance(raw, Mapping):
            raise ProfileServiceError("selected MCP server does not exist")
        selected = _server(draft.enabled_server, raw)
        harness_profile_revision: ProfileRevisionRecord | None = None
        harness: ClaudeCode | OpenCode | ACPAgent
        if draft.harness == "acp":
            if draft.tool_mode != "agent_default" or draft.model != "agent-default":
                raise ProfileServiceError("ACP requires agent-default model and tool mode")
            if not draft.harness_profile_id:
                raise ProfileServiceError("ACP requires a harness profile")
            hp = self.store.get_profile(draft.harness_profile_id)
            if hp is None or hp.kind != "harness" or hp.archived:
                raise ProfileServiceError("ACP harness profile is missing or archived")
            try:
                harness_profile_revision = self.store.resolve_revision(draft.harness_profile_id, draft.harness_revision)
            except StorageConflict as exc:
                raise ProfileServiceError("ACP harness revision does not exist") from exc
            value = harness_profile_revision.value
            manifest = value.get("manifest") if isinstance(value, Mapping) else None
            if not isinstance(manifest, Mapping) or not bool(value.get("trusted_unsandboxed")):
                raise ProfileServiceError("trusted unsandboxed acknowledgment is required")
            harness = ACPAgent(
                model="agent-default",
                executable=str(manifest.get("command", "")),
                manifest=dict(manifest),
                agent_mode_id=draft.agent_mode_id,
                session_config=draft.session_config,
            )
        elif draft.harness == "claude-code":
            if draft.tool_mode not in {"mcp_only", "mcp_read_only", "full"}:
                raise ProfileServiceError("invalid Claude Code tool mode")
            if draft.model not in self.settings.model_ids():
                raise ProfileServiceError("model is not configured for Claude Code")
            harness = ClaudeCode(
                model=draft.model,
                executable=self.settings.claude_executable,
                credential_references={
                    "ANTHROPIC_API_KEY": SecretReference(source="environment", name="ANTHROPIC_API_KEY")
                },
            )
        else:
            if draft.tool_mode not in {"mcp_only", "mcp_read_only", "full"}:
                raise ProfileServiceError("invalid OpenCode tool mode")
            if draft.model not in self.settings.opencode_models():
                raise ProfileServiceError("model is not configured for OpenCode")
            provider = draft.model.split("/", 1)[0] if "/" in draft.model else None
            credential_name = (
                {
                    "anthropic": "ANTHROPIC_API_KEY",
                    "openrouter": "OPENROUTER_API_KEY",
                    "opencode": "OPENCODE_API_KEY",
                    "opencode-go": "OPENCODE_API_KEY",
                }.get(provider.lower(), f"{provider.upper().replace('-', '_')}_API_KEY")
                if provider
                else None
            )
            refs = {} if credential_name is None else {credential_name: SecretReference(source="environment", name=credential_name)}
            harness = OpenCode(
                model=draft.model,
                executable=self.settings.opencode_executable,
                provider=provider,
                credential_references=refs,
            )
        if draft.harness == "acp":
            policy: Any = NativeToolPolicy(
                harness="acp",
                policy={"mode": "agent_default", "server": draft.enabled_server},
                nonportable_reason="ACP owns MCP tool selection",
            )
        else:
            read_only_tools = _CLAUDE_READ_ONLY_TOOLS if draft.harness == "claude-code" else _OPENCODE_READ_ONLY_TOOLS
            policy = NativeToolPolicy(
                harness=draft.harness,
                policy={"mode": draft.tool_mode, "server": draft.enabled_server, "read_only_tools": read_only_tools},
                nonportable_reason=f"{draft.harness} provider policy",
            )
        metadata = dict(draft.metadata)
        metadata.update(
            {
                "mcp_profile_id": profile.id,
                "mcp_revision_id": revision.id.root,
                "enabled_server": draft.enabled_server,
                "expected_goal": draft.expected_goal,
                "tool_mode": draft.tool_mode,
            }
        )
        if harness_profile_revision is not None:
            metadata.update({"harness_profile_id": draft.harness_profile_id, "harness_revision_id": harness_profile_revision.id.root})
        return AgentExecutionSpec(
            servers=(ServerBinding(server=selected, alias=draft.enabled_server),),
            harness=harness,
            message=UserMessage(content=(TextContent(text=draft.prompt),)),
            timeout_seconds=draft.timeout_seconds or float(self.settings.run_timeout_seconds),
            goal=draft.expected_goal,
            tool_policy=policy,
            metadata=metadata,
        )


__all__ = ["ExecutionSpecBuilder", "OneTurnRunDraft"]
