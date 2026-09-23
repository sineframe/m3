"""Public agent-session wiring and multi-turn ownership tests."""

from __future__ import annotations

import pytest
from mcp import types as mcp_types
from mcp.server.lowlevel import Server

from m3._types.specs import AgentSpec
from m3.async_api import AsyncMCPTestKit
from m3.harness import (
    DeterministicHarnessAdapter,
    HarnessAdapterRegistry,
    HarnessStartupError,
)
from m3.sync_api import MCPTestKit
from m3.types import (
    ACPAgent,
    ClaudeCode,
    EventKind,
    EventOrigin,
    ExecutionOutcome,
    InProcessServer,
    OpenCode,
    ServerBinding,
    StdioServer,
    TextContent,
    TransportKind,
    UserMessage,
)


def _spec() -> AgentSpec:
    return AgentSpec(
        harness=ACPAgent(model="test-model"),
        servers=(
            ServerBinding(
                server=StdioServer(name="first", command="mcp-first"),
                alias="first",
            ),
            ServerBinding(
                server=StdioServer(name="optional", command="mcp-optional"),
                alias="optional",
                required=False,
            ),
        ),
    )


def _loopback_server() -> Server:
    async def list_tools(
        _context: object, _params: object
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(tools=[])

    return Server("runtime-loopback", on_list_tools=list_tools)


@pytest.mark.asyncio
async def test_ordinary_async_factory_preserves_multiturn_and_closes_completed() -> (
    None
):
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        with pytest.raises(HarnessStartupError):
            kit.agent_session(_spec())


@pytest.mark.asyncio
async def test_registered_deterministic_adapter_preserves_multiturn_and_closes_completed() -> (
    None
):
    registry = HarnessAdapterRegistry(
        {"acp": lambda _harness: DeterministicHarnessAdapter(name="test-acp")}
    )
    async with AsyncMCPTestKit(
        env={}, cwd="/tmp/m3-no-project", adapter_registry=registry
    ) as kit:
        session = kit.agent_session(_spec())
        async with session:
            first = await session.send("first")
            second = await session.send("second")
            assert first.snapshot.number == 1
            assert second.snapshot.number == 2
            assert session._server_manager.snapshot().records[0].connection_id
            assert getattr(session.adapter, "name", None) == "test-acp"
        assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert len(session.result.turns) == 2


@pytest.mark.asyncio
async def test_agent_transport_evidence_is_distinct_deduplicated_and_normalized() -> (
    None
):
    adapter = DeterministicHarnessAdapter()
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.agent_session(_spec(), adapter=adapter) as session:
            # A second startup attempt must not duplicate lifecycle evidence.
            await session._start_adapter()
            transports = [
                event
                for event in session._trace_recorder.events()
                if event.kind is EventKind.TRANSPORT_CONNECTED
            ]
            assert len(transports) == 2
            assert {event.server_binding for event in transports} == {
                "first",
                "optional",
            }
            assert len({event.connection_id for event in transports}) == 2
            assert all(
                event.provenance.origin is EventOrigin.NORMALIZED
                and event.provenance.source == "m3.server_group"
                for event in transports
            )
            assert all(
                event.payload["configured_transport"] == TransportKind.STDIO.value
                and event.payload["instrumented_transport"] == TransportKind.STDIO.value
                for event in transports
            )


@pytest.mark.asyncio
async def test_agent_transport_evidence_excludes_unavailable_optional_server() -> None:
    invalid = (
        _spec()
        .servers[1]
        .model_copy(
            update={
                "server": StdioServer(
                    name="optional", command="mcp-optional", cwd="/not/a/real/directory"
                )
            }
        )
    )
    spec = _spec().model_copy(update={"servers": (_spec().servers[0], invalid)})
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.agent_session(
            spec, adapter=DeterministicHarnessAdapter()
        ) as session:
            pass
    transports = [
        event
        for event in session.result.trace.events
        if event.kind is EventKind.TRANSPORT_CONNECTED
    ]
    assert len(transports) == 1
    assert transports[0].server_binding == "first"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "harness",
    [
        OpenCode(model="fixture"),
        ClaudeCode(model="fixture"),
        ACPAgent(model="fixture"),
    ],
    ids=("opencode", "claude-code", "acp"),
)
async def test_agent_transport_is_present_across_harness_contracts(
    harness: object,
) -> None:
    spec = _spec().model_copy(update={"harness": harness})
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.agent_session(
            spec, adapter=DeterministicHarnessAdapter()
        ) as session:
            await session.send("cross-harness")
    entries = session.result.trace_view.transports
    assert entries
    assert all(entry.configured.value is TransportKind.STDIO for entry in entries)
    assert all(entry.instrumented.value is TransportKind.STDIO for entry in entries)


@pytest.mark.asyncio
async def test_injected_adapter_receives_one_server_launch_for_all_turns() -> None:
    adapter = DeterministicHarnessAdapter()
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.agent_session(_spec(), adapter=adapter) as session:
            await session.send("one")
            await session.send("two")
        assert adapter.open_count == 1
        assert adapter.last_launch is not None
        assert tuple(item.key for item in adapter.last_launch.configurations) == (
            "first",
            "optional",
        )
        assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED


@pytest.mark.asyncio
async def test_optional_server_preflight_reaches_adapter_as_unavailable() -> None:
    invalid_optional = (
        _spec()
        .servers[1]
        .model_copy(
            update={
                "server": StdioServer(
                    name="optional",
                    command="mcp-optional",
                    cwd="/path/that/is/not/available",
                )
            }
        )
    )
    spec = _spec().model_copy(
        update={"servers": (_spec().servers[0], invalid_optional)}
    )
    adapter = DeterministicHarnessAdapter()
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        async with kit.agent_session(spec, adapter=adapter):
            pass
    assert adapter.last_launch is not None
    assert adapter.last_launch.configurations[0].available is True
    assert adapter.last_launch.configurations[1].available is False


@pytest.mark.asyncio
async def test_public_runtime_server_registration_exposes_one_persistent_loopback() -> (
    None
):
    runtime_server = InProcessServer(name="runtime-loopback", factory=_loopback_server)
    adapter = DeterministicHarnessAdapter()
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        session = kit.agent_session(
            _spec(), adapter=adapter, runtime_servers=(runtime_server,)
        )
        async with session:
            await session.send("one")
            first_endpoint = (
                adapter.last_launch.configurations[-1].endpoint
                if adapter.last_launch
                else None
            )
            await session.send("two")
            second_endpoint = (
                adapter.last_launch.configurations[-1].endpoint
                if adapter.last_launch
                else None
            )
        assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert session._server_manager.snapshot().evidence.closed is True
    assert first_endpoint is not None
    assert first_endpoint == second_endpoint
    assert first_endpoint.startswith("http://127.0.0.1:")


def test_sync_runtime_server_registration_is_closed_with_session() -> None:
    runtime_server = InProcessServer(name="runtime-loopback", factory=_loopback_server)
    adapter = DeterministicHarnessAdapter()
    with MCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        with kit.agent_session(
            _spec(), adapter=adapter, runtime_servers=(runtime_server,)
        ) as session:
            assert session.send("one").snapshot.number == 1
        assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert adapter.last_launch is not None


@pytest.mark.asyncio
async def test_explicit_cancel_remains_cancelled() -> None:
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        session = kit.agent_session(_spec(), adapter=DeterministicHarnessAdapter())
        await session.__aenter__()
        await session.cancel()
        assert session.result.snapshot.outcome is ExecutionOutcome.CANCELLED


def test_sync_factory_rejects_unregistered_harness_without_fallback() -> None:
    with MCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        with pytest.raises(HarnessStartupError):
            kit.agent_session(_spec())


@pytest.mark.asyncio
async def test_execution_controller_uses_the_same_agent_session_path() -> None:
    spec = _spec().model_copy(
        update={"message": UserMessage(content=(TextContent(text="initial"),))}
    )
    registry = HarnessAdapterRegistry(
        {"acp": lambda _harness: DeterministicHarnessAdapter()}
    )
    async with AsyncMCPTestKit(
        env={}, cwd="/tmp/m3-no-project", adapter_registry=registry
    ) as kit:
        result = await kit.run(spec)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert result.error is None
    assert result.trace is not None
