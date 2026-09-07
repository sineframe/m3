from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from mcp_pal.harness import (
    DeterministicACPAdapter,
    FakeClaudeCodeAdapter,
    FakeOpenCodeAdapter,
    HarnessCleanupError,
    HarnessAdapterContract,
    HarnessLaunch,
    HarnessStartupError,
    HarnessTurnResult,
    HarnessTurnRequest,
    UnsupportedHarnessFeature,
    default_adapters,
)
from mcp_pal.interaction_handlers import Interactions, InteractionHandlers, PermissionRequest
from mcp_pal.server_group import ServerGroupManager
from mcp_pal.types import (
    ACPAgent,
    AgentSpec,
    AudioContent,
    PermissionPolicy,
    RestrictiveToolPolicy,
    ServerBinding,
    StdioServer,
    TextContent,
    TurnResponse,
    UserMessage,
)


def _spec() -> AgentSpec:
    return AgentSpec(
        harness=ACPAgent(model="fake-contract"),
        servers=(
            ServerBinding(server=StdioServer(name="memory", command="memory-server")),
            ServerBinding(server=StdioServer(name="other", command="other-server")),
        ),
        tool_policy=RestrictiveToolPolicy(allowed_tools=("memory:read",)),
    )


def _factories() -> tuple[Callable[..., HarnessAdapterContract], ...]:
    return (DeterministicACPAdapter, FakeClaudeCodeAdapter, FakeOpenCodeAdapter)


async def _launch() -> tuple[ServerGroupManager, HarnessLaunch]:
    manager = ServerGroupManager(_spec().servers)
    await manager.start()
    manager.register_tools("memory", ("read",))
    manager.register_tools("other", ("read",))
    spec = _spec()
    return manager, HarnessLaunch(spec, manager.snapshot(), manager.configurations(), spec.tool_policy)


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", _factories(), ids=("deterministic-acp", "fake-claude", "fake-opencode"))
async def test_common_harness_contract_is_stateful_and_cleanup_safe(
    factory: Callable[..., HarnessAdapterContract],
) -> None:
    manager, launch = await _launch()
    adapter = factory()
    try:
        readiness = await adapter.preflight(launch)
        assert readiness.ready is True
        session = await adapter.open(launch)
        session_id = session.session_id
        before = session.snapshot()
        results = [
            await session.send(HarnessTurnRequest.from_message(f"turn-{number}"))
            for number in range(1, 4)
        ]
        snapshot = session.snapshot()
        assert [result.sequence for result in results] == [1, 2, 3]
        assert all(result.status == "completed" for result in results)
        assert all(result.response is not None for result in results)
        assert snapshot.session_id == session_id
        assert snapshot.turns == 3
        assert snapshot.server_configuration_count == 2
        assert snapshot.evidence["process_scope"] == before.evidence["process_scope"]
        assert snapshot.evidence["connection_scope"] == before.evidence["connection_scope"]
        assert snapshot.evidence["usage_provenance"] == "unavailable"
        assert snapshot.evidence["policy_requested"] == "restrictive"
        assert snapshot.evidence["policy_enforced"] == "portable"
        assert snapshot.evidence["policy_observed"] == "preflight"
        assert snapshot.evidence["policy_portable"] is True
        await session.close()
        await session.close()
        assert session.snapshot().closed is True
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", _factories(), ids=("deterministic-acp", "fake-claude", "fake-opencode"))
async def test_common_contract_preserves_evidence_and_interaction_wiring(
    factory: Callable[..., HarnessAdapterContract],
) -> None:
    manager, base_launch = await _launch()
    seen: list[bool] = []

    async def permission(_request: PermissionRequest) -> bool:
        return True

    interactions = Interactions(
        permission_policy=PermissionPolicy(mode="prompt"),
        handlers=InteractionHandlers(permission=permission),
    )

    async def handler(_request: HarnessTurnRequest, state: dict[str, object]) -> HarnessTurnResult:
        controller = state["interactions"]
        assert controller is interactions
        result = await interactions.permission(PermissionRequest("read", "fixture"))
        seen.append(result.allowed)
        return HarnessTurnResult(
            sequence=99,
            status="completed",
            response=TurnResponse(content=(TextContent(text="ok"),)),
            tool_calls=({"server": "memory", "tool": "read", "status": "completed"},),
            evidence={"usage_provenance": "unavailable"},
        )

    launch = HarnessLaunch(
        base_launch.spec,
        base_launch.servers,
        base_launch.configurations,
        base_launch.tool_policy,
        interactions,
    )
    adapter = factory(handler=handler)
    try:
        session = await adapter.open(launch)
        result = await session.send(HarnessTurnRequest.from_message("capture"))
        assert result.sequence == 1
        assert result.tool_calls[0]["tool"] == "read"
        assert result.evidence["process_capture"] == "in_process"
        assert result.evidence["transport_capture"] == "in_process"
        assert result.evidence["mcp_capture"] == "normalized"
        assert result.evidence["tool_capture"] == "adapter_reported"
        assert result.evidence["content_capture"] == "structured"
        assert result.evidence["usage_provenance"] == "unavailable"
        assert seen == [True]
        await session.close()
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", _factories(), ids=("deterministic-acp", "fake-claude", "fake-opencode"))
async def test_common_contract_enforces_declared_attachment_capability(
    factory: Callable[..., HarnessAdapterContract],
) -> None:
    manager, launch = await _launch()
    adapter = factory()
    try:
        session = await adapter.open(launch)
        message = UserMessage(content=(AudioContent(media_type="audio/wav", data="YQ=="),))
        if "audio" in adapter.capabilities.supported_content_kinds:
            result = await session.send(HarnessTurnRequest(message))
            assert result.status == "completed"
        else:
            with pytest.raises(UnsupportedHarnessFeature):
                await session.send(HarnessTurnRequest(message))
        await session.close()
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", _factories(), ids=("deterministic-acp", "fake-claude", "fake-opencode"))
async def test_common_contract_rejects_startup_failure_and_records_cleanup_failure(
    factory: Callable[..., HarnessAdapterContract],
) -> None:
    manager, launch = await _launch()
    failing = factory(startup_error=True)
    try:
        with pytest.raises(HarnessStartupError):
            await failing.open(launch)
    finally:
        await manager.close()

    manager, launch = await _launch()
    cleanup = factory(cleanup_error=True)
    try:
        session = await cleanup.open(launch)
        with pytest.raises(HarnessCleanupError):
            await session.close()
        assert session.snapshot().closed is True
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_common_contract_isolates_concurrent_sessions_and_no_fallback() -> None:
    manager, launch = await _launch()

    async def open_one(factory: Callable[..., HarnessAdapterContract]) -> tuple[str, str]:
        adapter = factory()
        session = await adapter.open(launch)
        await session.send(HarnessTurnRequest.from_message("one"))
        snapshot = session.snapshot()
        await session.close()
        return str(snapshot.session_id), str(snapshot.evidence["connection_scope"])

    try:
        first, second = await asyncio.gather(
            open_one(DeterministicACPAdapter),
            open_one(FakeClaudeCodeAdapter),
        )
        assert first[0] != second[0]
        assert first[1] != second[1]
    finally:
        await manager.close()

    registry = default_adapters()
    with pytest.raises(HarnessStartupError):
        registry.resolve(_spec())


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", _factories(), ids=("deterministic-acp", "fake-claude", "fake-opencode"))
async def test_common_harness_contract_maps_timeout_and_cancellation(
    factory: Callable[..., HarnessAdapterContract],
) -> None:
    async def slow(_request: HarnessTurnRequest, _state: dict[str, object]) -> str:
        await asyncio.sleep(1)
        return "late"

    manager, launch = await _launch()
    adapter = factory(handler=slow)
    try:
        session = await adapter.open(launch)
        timed_out = await session.send(HarnessTurnRequest.from_message("timeout", timeout_seconds=0.01))
        assert timed_out.status == "timed_out"
        pending = asyncio.create_task(session.send(HarnessTurnRequest.from_message("cancel")))
        await asyncio.sleep(0)
        await session.cancel()
        cancelled = await pending
        assert cancelled.status == "cancelled"
        await session.close()
    finally:
        await manager.close()


def test_fake_capability_shapes_are_explicit_and_do_not_fallback() -> None:
    claude = FakeClaudeCodeAdapter()
    opencode = FakeOpenCodeAdapter()
    assert claude.name == "fake-claude-code"
    assert opencode.name == "fake-opencode"
    assert claude.capabilities.supports_streaming is True
    assert opencode.capabilities.supports_streaming is True
    assert "audio" in claude.capabilities.supported_content_kinds
    assert "audio" not in opencode.capabilities.supported_content_kinds
