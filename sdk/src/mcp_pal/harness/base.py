"""Common harness data structures and interface."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunSpec:
    prompt: str
    model: str
    mcp_config: dict
    enabled_server: str
    tool_mode: str = "mcp_only"
    timeout_seconds: int = 120
    max_turns: int = 5
    max_budget_usd: float = 0.5


@dataclass
class AcpRunSpec:
    """Inputs for an ACP run. Kept separate from native ``RunSpec``."""

    prompt: str
    model: str
    mcp_config: dict
    enabled_server: str
    manifest: dict
    tool_mode: str = "agent_default"
    agent_mode_id: str | None = None
    session_config: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 120
    # Local/private MCP endpoints are deliberately opt-in.  This is primarily
    # useful for deterministic local E2E fixtures; production runs retain the
    # public-upstream SSRF guard unless a caller explicitly opts in.
    allow_private_upstream: bool = False


@dataclass
class HarnessResult:
    status: str
    events: list[Any] = field(default_factory=list)
    normalized: list[tuple[str, dict]] = field(default_factory=list)
    # Receipt-timed Claude stream events and decoded MCP frames are kept in
    # memory until RunManager builds the redacted, versioned persisted trace.
    event_records: list[dict[str, Any]] = field(default_factory=list)
    protocol_events: list[dict[str, Any]] = field(default_factory=list)
    transport: str = "stdio"
    # Keep configured and instrumented transports distinct for ACP reports.
    # They are equal for the current runner, but this avoids implying that the
    # local proxy changed the selected MCP transport.
    configured_transport: str = "stdio"
    instrumented_transport: str = "stdio"
    final_text: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: str | None = None
    # ACP failures retain the legacy human-readable ``error`` while exposing
    # stable machine-readable classification for API/reporting consumers.
    error_code: str | None = None
    error_phase: str | None = None
    error_details: dict[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    turns: int | None = None
    session_id: str | None = None
    final_result_seen: bool = False


class HarnessRunner:
    async def run(
        self, spec: RunSpec, on_event: Callable | None = None, cancel_event: Any = None
    ) -> HarnessResult:
        raise NotImplementedError
