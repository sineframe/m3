from __future__ import annotations

import asyncio

import pytest

from mcp_pal.harness.contracts import (
    DeterministicHarnessAdapter,
    HarnessAdapterCapabilities,
    HarnessLaunch,
    HarnessStartupError,
    HarnessTurnRequest,
    UnsupportedHarnessFeature,
)
from mcp_pal.server_group import ServerGroupManager
from mcp_pal.types import ACPAgent, AgentSpec, RestrictiveToolPolicy, ServerBinding, StdioServer


def _launch(manager: ServerGroupManager) -> HarnessLaunch:
    spec = AgentSpec(
        harness=ACPAgent(model="fake"),
        servers=(
            ServerBinding(
                server=StdioServer(name="memory", command="memory-server"),
            ),
        ),
    )
    return HarnessLaunch(spec, manager.snapshot(), manager.configurations(), spec.tool_policy)


@pytest.mark.asyncio
async def test_fake_adapter_preserves_conversation_state_and_configuration() -> None:
    manager = ServerGroupManager(
        (ServerBinding(server=StdioServer(name="memory", command="memory-server")),)
    )
    await manager.start()
    adapter = DeterministicHarnessAdapter()
    session = await adapter.open(_launch(manager))

    first = await session.send(HarnessTurnRequest.from_message("remember 42"))
    second = await session.send(HarnessTurnRequest.from_message("retrieve it"))
    snapshot = session.snapshot()

    assert first.status == second.status == "completed"
    assert first.response is not None and first.response.text == "remember 42"
    assert second.response is not None and second.response.text == "retrieve it"
    assert snapshot.turns == 2
    assert snapshot.server_configuration_count == 1
    assert snapshot.session_id == session.session_id
    await session.close()
    await manager.close()


@pytest.mark.asyncio
async def test_fake_adapter_rejects_unsupported_attachment_before_handler() -> None:
    manager = ServerGroupManager(
        (ServerBinding(server=StdioServer(name="memory", command="memory-server")),)
    )
    await manager.start()
    adapter = DeterministicHarnessAdapter(
        capabilities=HarnessAdapterCapabilities(
            name="text-only", supported_content_kinds=frozenset({"text"})
        )
    )
    session = await adapter.open(_launch(manager))
    from mcp_pal.types import ImageContent, UserMessage

    with pytest.raises(UnsupportedHarnessFeature):
        await session.send(
            HarnessTurnRequest(UserMessage(content=(ImageContent(media_type="image/png", data="x"),)))
        )
    await session.close()
    await manager.close()


@pytest.mark.asyncio
async def test_fake_adapter_attaches_portable_policy_evidence_to_effective_launch() -> None:
    manager = ServerGroupManager(
        (ServerBinding(server=StdioServer(name="memory", command="memory-server")),)
    )
    await manager.start()
    spec = AgentSpec(
        harness=ACPAgent(model="fake"),
        servers=(ServerBinding(server=StdioServer(name="memory", command="memory-server")),),
        tool_policy=RestrictiveToolPolicy(allowed_tools=("memory:read",)),
    )
    adapter = DeterministicHarnessAdapter()
    await adapter.open(HarnessLaunch(spec, manager.snapshot(), manager.configurations(), spec.tool_policy))
    assert adapter.last_launch is not None
    assert adapter.last_launch.tool_policy_evidence is not None
    assert adapter.last_launch.tool_policy_evidence.enforced == "portable"
    assert adapter.last_launch.tool_policy_evidence.requested == "restrictive"
    await adapter.close()
    await manager.close()


@pytest.mark.asyncio
async def test_fake_adapter_rejects_portable_policy_when_capability_is_unavailable() -> None:
    manager = ServerGroupManager(
        (ServerBinding(server=StdioServer(name="memory", command="memory-server")),)
    )
    await manager.start()
    spec = AgentSpec(
        harness=ACPAgent(model="fake"),
        servers=(ServerBinding(server=StdioServer(name="memory", command="memory-server")),),
        tool_policy=RestrictiveToolPolicy(allowed_tools=("memory:read",)),
    )
    adapter = DeterministicHarnessAdapter(
        capabilities=HarnessAdapterCapabilities(name="no-policy", supports_tool_policy=False)
    )
    readiness = await adapter.preflight(HarnessLaunch(spec, manager.snapshot(), manager.configurations(), spec.tool_policy))
    assert readiness.ready is False
    assert readiness.reason == "tool_policy_unsupported"
    with pytest.raises(HarnessStartupError):
        await adapter.open(HarnessLaunch(spec, manager.snapshot(), manager.configurations(), spec.tool_policy))
    await manager.close()


@pytest.mark.asyncio
async def test_fake_adapter_timeout_and_cancel_are_typed_and_cleanup_is_idempotent() -> None:
    async def slow_handler(request: HarnessTurnRequest, state: dict[str, object]) -> str:
        del request, state
        await asyncio.sleep(1)
        return "late"

    manager = ServerGroupManager(
        (ServerBinding(server=StdioServer(name="memory", command="memory-server")),)
    )
    await manager.start()
    adapter = DeterministicHarnessAdapter(handler=slow_handler)
    session = await adapter.open(_launch(manager))

    timed_out = await session.send(HarnessTurnRequest.from_message("slow", timeout_seconds=0.01))
    assert timed_out.status == "timed_out"
    running = asyncio.create_task(session.send(HarnessTurnRequest.from_message("cancel")))
    await asyncio.sleep(0)
    await session.cancel()
    cancelled = await running
    assert cancelled.status == "cancelled"
    await session.close()
    await session.close()
    await manager.close()
