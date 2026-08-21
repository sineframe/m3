from datetime import datetime
from typing import Any, Literal
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
    harness: Literal["claude-code", "opencode"] = "claude-code"
    model: str
    prompt: str
    expected_output: str = Field(min_length=1)
    profile_revision_id: str
    enabled_server: str
    tool_mode: Literal["mcp_only", "mcp_read_only", "full"] = "mcp_only"
    @field_validator("prompt", "expected_output")
    @classmethod
    def nonblank(cls, v):
        if not v.strip(): raise ValueError("must be nonblank")
        return v

class RunClone(BaseModel):
    harness: Literal["claude-code", "opencode"] | None = None
    model: str | None = None
    prompt: str | None = None
    expected_output: str | None = None
    profile_revision_id: str | None = None
    enabled_server: str | None = None
    tool_mode: Literal["mcp_only", "mcp_read_only", "full"] | None = None
    use_latest_revision: bool = False
    model_config = {"extra": "forbid"}

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
    created_at: datetime; started_at: datetime | None = None; finished_at: datetime | None = None
    model_config = {"from_attributes": True}
