"""Intentionally invalid adapter usage checked as a negative type fixture."""

from __future__ import annotations

from m3 import AgentSpec, MCPTestKit
from m3.harness import HarnessAdapterContract


def session_only_contract_is_rejected(
    kit: MCPTestKit, agent_spec: AgentSpec, adapter: HarnessAdapterContract
) -> None:
    """A session-only contract cannot drive controller turns."""

    kit.agent_session(agent_spec, adapter=adapter)
