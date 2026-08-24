from datetime import datetime
from typing import Any, Literal
import re
from pydantic import BaseModel, Field, field_validator
from ..domain.validation import validate_mcp_config, ProfileValidationError

class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    mcp_json: dict[str, Any]
    @field_validator("mcp_json")
    @classmethod
    def valid_json(cls, v):
        validate_mcp_config(v); return v

class RevisionCreate(BaseModel):
    mcp_json: dict[str, Any]
    @field_validator("mcp_json")
    @classmethod
    def valid_json(cls, v):
        validate_mcp_config(v); return v

class RunCreate(BaseModel):
    harness: Literal["claude-code", "opencode", "acp"] = "claude-code"
    harness_revision_id: str | None = None
    agent_mode_id: str | None = None
    session_config: dict[str, Any] = Field(default_factory=dict)
    model: str
    prompt: str
    expected_output: str = Field(min_length=1)
    profile_revision_id: str
    enabled_server: str
    tool_mode: Literal["mcp_only", "mcp_read_only", "full", "agent_default"] = "mcp_only"
    @field_validator("prompt", "expected_output")
    @classmethod
    def nonblank(cls, v):
        if not v.strip(): raise ValueError("must be nonblank")
        return v

class RunClone(BaseModel):
    harness: Literal["claude-code", "opencode", "acp"] | None = None
    model: str | None = None
    prompt: str | None = None
    expected_output: str | None = None
    profile_revision_id: str | None = None
    enabled_server: str | None = None
    tool_mode: Literal["mcp_only", "mcp_read_only", "full", "agent_default"] | None = None
    harness_revision_id: str | None = None
    agent_mode_id: str | None = None
    session_config: dict[str, Any] | None = None
    transport: Literal["stdio", "http", "sse"] | None = None
    use_latest_harness_revision: bool = False
    use_latest_revision: bool = False
    model_config = {"extra": "forbid"}

class HarnessManifest(BaseModel):
    schema_version: Literal["mcp-pal.harness.v1"] = "mcp-pal.harness.v1"
    protocol: Literal["acp"] = "acp"
    protocol_version: Literal[1] = 1
    command: str = Field(min_length=1, max_length=1000)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    # Manifests are persisted and later executed.  Silently dropping a field
    # here would make the exported manifest differ from what the caller sent
    # and, more importantly, could hide an unsupported execution control.
    model_config = {"extra": "forbid"}

    @field_validator("env")
    @classmethod
    def references_only(cls, value):
        for name, ref in value.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"invalid environment variable name: {name}")
            if not isinstance(ref, str) or not re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", ref):
                raise ValueError(f"environment value for {name} must be a reference")
        return value

class HarnessProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    manifest: HarnessManifest
    trusted_unsandboxed: bool = False

class HarnessRevisionCreate(BaseModel):
    """Revision input; profile metadata is not part of an immutable revision."""
    manifest: HarnessManifest
    trusted_unsandboxed: bool = False
    # Kept as tolerated compatibility fields for the original UI payload.
    name: str | None = None
    description: str | None = None

class Assertion(BaseModel):
    status: str
    reason: str | None = None

class EventOut(BaseModel):
    sequence: int
    timestamp: datetime
    event_type: str
    payload: Any
    raw_event: Any

class RunOut(BaseModel):
    id: str; parent_run_id: str | None = None; profile_revision_id: str; enabled_server: str
    harness: str; model: str; tool_mode: str; prompt: str; expected_output: str
    timeout_seconds: int; max_turns: int | None; max_budget_usd: float | None; status: str
    claude_result: str | None = None; exit_code: int | None = None; error_message: str | None = None
    stderr: str | None = None; cost_usd: float | None = None; turns: int | None = None; session_id: str | None = None
    mcp_assertion: str; semantic_assertion: str; semantic_reason: str
    transport: str | None = None; trace_available: bool = False
    created_at: datetime; started_at: datetime | None = None; finished_at: datetime | None = None
    model_config = {"from_attributes": True}
