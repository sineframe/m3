"""Deterministic Phase 9 lifecycle and trace acceptance regressions."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import cast

import pytest
from mcp.server.lowlevel import Server

from mcp_pal.agent_session import AdapterTurn, AsyncAgentSession
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.harness import DeterministicHarnessAdapter, HarnessAdapterRegistry
from mcp_pal.types import (
    ACPAgent,
    ActivityHealth,
    AgentExecutionSpec,
    EventKind,
    ExecutionOutcome,
    InProcessServer,
    ServerBinding,
    SessionForkRequest,
    StdioServer,
    TextContent,
    TurnResponse,
    UserMessage,
)


def _spec() -> AgentExecutionSpec:
    return AgentExecutionSpec(
        harness=ACPAgent(model="phase9-test"),
        servers=(
            ServerBinding(server=StdioServer(name="required", command="fixture"), alias="required"),
            ServerBinding(
                server=StdioServer(name="optional", command="fixture-optional"),
                alias="optional",
                required=False,
            ),
        ),
    )


def _loopback_server() -> Server:
    return Server("phase9-loopback")


class _SlowStartupAdapter:
    supported_content_kinds = frozenset({"text"})

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.start_count = 0
        self.close_count = 0
        self.messages: list[str] = []

    async def start(self, _spec: AgentExecutionSpec) -> None:
        self.start_count += 1
        self.started.set()
        await self.release.wait()

    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse:
        del timeout, metadata
        text = cast(TextContent, message.content[0]).text
        self.messages.append(text)
        return TurnResponse(content=(TextContent(text=text),))

    async def close(self) -> None:
        self.close_count += 1


@pytest.mark.asyncio
async def test_startup_cancellation_reaps_adapter_and_loopback_manager_once() -> None:
    adapter = _SlowStartupAdapter()
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(
            _spec(),
            adapter=adapter,
            runtime_servers=(InProcessServer(name="loopback", factory=_loopback_server),),
        )
        entering = asyncio.create_task(session.__aenter__())
        await adapter.started.wait()
        entering.cancel()
        with pytest.raises(asyncio.CancelledError):
            await entering

        try:
            assert adapter.close_count == 1
            assert session._server_manager.snapshot().evidence.closed is True
            assert session._closed is True
            with pytest.raises(Exception):
                await session.send("after-cancel")
        finally:
            await asyncio.wait_for(session.aclose(), timeout=2)


@pytest.mark.asyncio
async def test_close_during_startup_has_no_late_enter_or_duplicate_cleanup() -> None:
    adapter = _SlowStartupAdapter()
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(_spec(), adapter=adapter)
        entering = asyncio.create_task(session.__aenter__())
        await adapter.started.wait()
        close_error: BaseException | None = None
        try:
            await asyncio.wait_for(session.aclose(), timeout=0.5)
        except BaseException as error:
            close_error = error
        adapter.release.set()
        enter_result = await asyncio.wait_for(
            asyncio.gather(entering, return_exceptions=True), timeout=2
        )

        assert adapter.close_count == 1
        assert close_error is None
        assert session._closed is True
        assert session._entered is False
        assert session._server_manager.snapshot().evidence.closed is True
        assert isinstance(enter_result[0], BaseException)
        assert session.result.snapshot.lifecycle.value == "finished"


@pytest.mark.asyncio
async def test_repeated_startup_cancel_races_leave_no_tasks_or_open_managers() -> None:
    baseline = {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        for _ in range(12):
            adapter = _SlowStartupAdapter()
            session = kit.agent_session(_spec(), adapter=adapter)
            entering = asyncio.create_task(session.__aenter__())
            await adapter.started.wait()
            entering.cancel()
            with pytest.raises(asyncio.CancelledError):
                await entering
            assert adapter.close_count == 1
            assert session._server_manager.snapshot().evidence.closed is True
        await asyncio.sleep(0)
    remaining = {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}
    assert remaining == baseline


@pytest.mark.asyncio
async def test_fork_preserves_runtime_loopback_registration_and_fresh_identity() -> None:
    source_adapter = DeterministicHarnessAdapter()
    runtime_server = InProcessServer(name="loopback", factory=_loopback_server)
    # Public construction is required for the runtime-only registration; use
    # the kit session so the source manager is the one being forked.
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        source = kit.agent_session(_spec(), adapter=source_adapter, runtime_servers=(runtime_server,))
        async with source:
            await source.send("source")
        request = SessionForkRequest(
            mode="replay",
            replay_inputs=(UserMessage(content=(TextContent(text="replay"),)),),
            source_turn_id=source.result.turns[0].snapshot.turn_id,
        )
        child_adapter = DeterministicHarnessAdapter()
        child = await source.fork(request, adapter_factory=lambda _spec, _provenance: child_adapter)
        async with child:
            await child.send("replay")

    assert child._execution_id != source._execution_id
    assert child._session_id != source._session_id
    assert child_adapter.last_launch is not None
    assert child_adapter.last_launch.configurations[-1].endpoint is not None


class _ToolEvidenceAdapter:
    supported_content_kinds = frozenset({"text"})

    def __init__(self, outcomes: tuple[bool, ...]) -> None:
        self._outcomes = outcomes
        self._index = 0

    async def start(self, _spec: AgentExecutionSpec) -> None:
        return None

    async def send(self, message: UserMessage, *, timeout: float | None = None, metadata: Mapping[str, object] | None = None) -> AdapterTurn:
        del timeout, metadata
        failed = self._outcomes[min(self._index, len(self._outcomes) - 1)]
        self._index += 1
        text = cast(TextContent, message.content[0]).text
        return AdapterTurn(
            response=TurnResponse(content=(TextContent(text=text),)),
            tool_calls=({"name": "fixture", "is_error": failed},),
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_agent_trace_contains_turn_tool_evidence_and_activity_health() -> None:
    spec = _spec().model_copy(update={"message": UserMessage(content=(TextContent(text="run"),))})
    registry = HarnessAdapterRegistry({"acp": lambda _harness: _ToolEvidenceAdapter((False,))})
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project", adapter_registry=registry) as kit:
        result = await kit.run(spec)

    assert result.activity_health is ActivityHealth.ALL_SUCCEEDED
    assert result.trace is not None
    kinds = {event.kind for event in result.trace.events}
    assert EventKind.TURN_CREATED in kinds
    assert EventKind.TOOL_CALL_REQUESTED in kinds
    assert EventKind.TOOL_RESULT_RECEIVED in kinds


@pytest.mark.asyncio
async def test_three_turn_session_activity_health_is_mixed() -> None:
    adapter = _ToolEvidenceAdapter((False, True, False))
    async with AsyncAgentSession(_spec(), adapter) as session:
        await session.send("one")
        await session.send("two")
        await session.send("three")
    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert session.result.activity_health is ActivityHealth.MIXED
