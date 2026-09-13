import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

JsonObject = dict[str, Any]


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class McpProfile(Base):
    __tablename__ = "mcp_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )
    revisions: Mapped[list["McpProfileRevision"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class McpProfileRevision(Base):
    __tablename__ = "mcp_profile_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_profiles.id"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    mcp_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    profile: Mapped[McpProfile] = relationship(back_populates="revisions")
    __table_args__ = (UniqueConstraint("profile_id", "revision_number"),)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    parent_run_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    profile_revision_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_profile_revisions.id"), nullable=False
    )
    enabled_server: Mapped[str] = mapped_column(String(200), nullable=False)
    harness: Mapped[str] = mapped_column(String(50), default="claude-code")
    model: Mapped[str] = mapped_column(String(300), nullable=False)
    tool_mode: Mapped[str] = mapped_column(String(30), default="mcp_only")
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[str] = mapped_column(Text, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_turns: Mapped[int] = mapped_column(Integer, nullable=False)
    max_budget_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    claude_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    mcp_assertion: Mapped[str] = mapped_column(String(30), default="not_evaluated")
    semantic_assertion: Mapped[str] = mapped_column(String(30), default="not_evaluated")
    semantic_reason: Mapped[str] = mapped_column(
        String(200), default="LLM judge deferred"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[JsonObject] = mapped_column(JSON, nullable=False)
    raw_event: Mapped[JsonObject | str] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)


class RunTrace(Base):
    __tablename__ = "run_traces"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    harness: Mapped[str] = mapped_column(
        String(50), nullable=False, default="claude-code"
    )
    schema_version: Mapped[str] = mapped_column(
        String(30), nullable=False, default="claude.v2"
    )
    capture_status: Mapped[str] = mapped_column(String(30), nullable=False)
    trace: Mapped[JsonObject] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class HarnessProfile(Base):
    __tablename__ = "harness_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )
    revisions: Mapped[list["HarnessProfileRevision"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class HarnessProfileRevision(Base):
    __tablename__ = "harness_profile_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("harness_profiles.id"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[JsonObject] = mapped_column(JSON, nullable=False)
    trusted_unsandboxed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    profile: Mapped[HarnessProfile] = relationship(back_populates="revisions")
    __table_args__ = (UniqueConstraint("profile_id", "revision_number"),)


class HarnessProbe(Base):
    __tablename__ = "harness_probes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("harness_profile_revisions.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    evidence: Mapped[JsonObject] = mapped_column(JSON, default=dict)
    transport: Mapped[str] = mapped_column(String(20), default="stdio")
    mode_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    session_config: Mapped[JsonObject] = mapped_column(JSON, default=dict)
    agent_identity: Mapped[JsonObject | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RunHarnessSnapshot(Base):
    __tablename__ = "run_harness_snapshots"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    manifest: Mapped[JsonObject] = mapped_column(JSON, nullable=False)
    session_config: Mapped[JsonObject] = mapped_column(JSON, default=dict)
    agent_mode_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_mode: Mapped[str] = mapped_column(String(40), default="agent_default")
    verification: Mapped[JsonObject] = mapped_column(JSON, default=dict)
