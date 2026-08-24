"""Examples checked with mypy strict mode as part of the public API contract."""

from __future__ import annotations

from mcp_pal import AgentExecutionSpec, MCPTestKit, SDKConfig, ServerValue
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.sync_api import ProbeKind, ProbeRequest


def sync_usage(kit: MCPTestKit) -> None:
    with kit:
        baseline = kit.capabilities()
        assert baseline.readiness.ready
        requested = kit.capabilities([ProbeRequest(ProbeKind.STORAGE, "memory")])
        assert requested.result_for("memory") is not None
        assert isinstance(kit.config, SDKConfig)


async def async_usage(kit: AsyncMCPTestKit) -> None:
    async with kit:
        baseline = await kit.capabilities()
        assert baseline.readiness.ready
        requested = await kit.capabilities([ProbeRequest(ProbeKind.STORAGE, "memory")])
        assert requested.result_for("memory") is not None
        assert isinstance(kit.config, SDKConfig)


def sync_runtime_usage(kit: MCPTestKit, server: ServerValue, agent_spec: AgentExecutionSpec) -> None:
    """The planned runtime shape remains type-safe before its implementation phase."""

    with kit:
        with kit.direct(server, protocol=None, timeout=None) as client:
            client.call_tool("tool", {"key": "value"})

        kit.run(agent_spec)
        with kit.agent_session(agent_spec) as session:
            session.send("message", timeout=None, metadata=None)
            session.snapshot()
            session.result


async def async_runtime_usage(kit: AsyncMCPTestKit, server: ServerValue, agent_spec: AgentExecutionSpec) -> None:
    """The async twin mirrors the planned direct and agent lifecycle."""

    async with kit:
        async with kit.direct(server, protocol=None, timeout=None) as client:
            await client.call_tool("tool", {"key": "value"})

        await kit.run(agent_spec)
        session = kit.agent_session(agent_spec)
        async with session:
            await session.send("message", timeout=None, metadata=None)
            await session.snapshot()
            session.result


def sync_pytest_usage() -> None:
    """Ordinary pytest tests use the blocking twin without an event loop."""

    with MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        report = kit.capabilities()
        assert report.readiness.ready


async def async_pytest_usage() -> None:
    """pytest-asyncio tests use the async twin and await blocking work."""

    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        report = await kit.capabilities()
        assert report.readiness.ready
