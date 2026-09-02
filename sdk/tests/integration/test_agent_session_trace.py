"""Trace-authority regressions for direct and submitted agent sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from mcp_pal.agent_session import AdapterTurn, AsyncAgentSession, HarnessAdapter, HarnessTurnError
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.errors import CleanupError, TransportError
from mcp_pal.harness import HarnessAdapterRegistry
from mcp_pal.server_group import ServerStartupError
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal.sync_api import MCPTestKit
from mcp_pal.types import (
    ACPAgent,
    ArtifactPolicy,
    AgentExecutionSpec,
    CanonicalEvent,
    ErrorCode,
    ErrorInfo,
    EventKind,
    ExecutionOutcome,
    ExecutionResult,
    ServerBinding,
    StdioServer,
    TextContent,
    TurnResponse,
    TurnLifecycle,
    TurnOutcome,
    UserMessage,
)


def _spec(*, message: str | None = None) -> AgentExecutionSpec:
    return AgentExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ACPAgent(model="fixture"),
        message=(UserMessage(content=(TextContent(text=message),)) if message is not None else None),
    )


class _TraceHarness(HarnessAdapter):
    async def start(self, _spec: AgentExecutionSpec) -> None:
        return None

    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse | AdapterTurn:
        del timeout, metadata
        content = message.content[0]
        assert isinstance(content, TextContent)
        return TurnResponse(content=(TextContent(text=content.text),))

    async def close(self) -> None:
        return None


def _assert_trace_identity(result: ExecutionResult) -> None:
    snapshot = result.snapshot
    trace = result.trace
    assert trace is not None
    assert trace.execution_id == snapshot.execution_id
    assert trace.events[-1].kind is EventKind.EXECUTION_FINISHED
    assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in trace.events) == 1
    assert all(event.execution_id == snapshot.execution_id for event in trace.events)
    terminal = trace.events[-1]
    assert snapshot.outcome is not None
    assert terminal.payload["outcome"] == snapshot.outcome.value
    assert terminal.payload["completeness"] == trace.completeness
    assert trace.highest_sequence == terminal.sequence


class _StartupFailureHarness(_TraceHarness):
    async def start(self, _spec: AgentExecutionSpec) -> None:
        raise RuntimeError("provider-startup-secret")


class _TerminalFailureHarness(_TraceHarness):
    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse:
        del message, timeout, metadata
        raise HarnessTurnError("provider-turn-secret", terminal=True)


class _SecondTurnFailureHarness(_TraceHarness):
    def __init__(self) -> None:
        self._turns = 0

    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse:
        del timeout, metadata
        self._turns += 1
        if self._turns > 1:
            raise HarnessTurnError("provider-second-turn-secret", terminal=True)
        content = message.content[0]
        assert isinstance(content, TextContent)
        return TurnResponse(content=(TextContent(text=content.text),))


class _TerminalCompleteEvidenceHarness(_TraceHarness):
    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AdapterTurn:
        del message, timeout, metadata
        return AdapterTurn(
            terminal=True,
            outcome=TurnOutcome.FAILED,
            error=ErrorInfo(code=ErrorCode.TRANSPORT_ERROR, message="terminal adapter failure"),
            evidence={"wire_complete": True},
        )


class _TimeoutHarness(_TraceHarness):
    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse:
        del message, timeout, metadata
        await asyncio.sleep(10)
        raise AssertionError("timeout harness unexpectedly returned")


class _ActiveCancellationHarness(_TraceHarness):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancel_calls = 0

    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse:
        del message, timeout, metadata
        self.started.set()
        await self.release.wait()
        return TurnResponse(content=(TextContent(text="released"),))

    async def start(self, _spec: AgentExecutionSpec) -> None:
        return None

    async def cancel(self) -> None:
        self.cancel_calls += 1
        self.release.set()

    async def close(self) -> None:
        return None


class _CloseBarrierHarness(_TraceHarness):
    def __init__(self) -> None:
        self.close_called = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self) -> None:
        self.close_called.set()
        await self.release_close.wait()


class _TransientCleanupHarness(_TraceHarness):
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise RuntimeError("cleanup-secret")


class _FailingServerManager:
    async def start(self) -> None:
        raise ServerStartupError("server-startup-secret")

    async def close(self) -> None:
        return None


async def _wait_for(predicate: Callable[[], bool], *, attempts: int = 50) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")


@pytest.mark.asyncio
async def test_direct_async_agent_session_result_owns_one_finalized_trace() -> None:
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(_spec(), adapter=_TraceHarness())
        async with session:
            await session.send("hello")
        result = session.result

    _assert_trace_identity(result)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert result.trace is not None
    assert result.trace.execution_id == session._execution_id
    assert result.trace.events[-1].kind is EventKind.EXECUTION_FINISHED


def test_direct_sync_agent_session_result_owns_one_finalized_trace() -> None:
    with MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        session = kit.agent_session(_spec(), adapter=_TraceHarness())
        with session:
            session.send("hello")
        result = session.result

    _assert_trace_identity(result)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED


@pytest.mark.asyncio
async def test_caller_async_agent_session_uses_configured_store_for_all_turns(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "caller-async.sqlite")
    try:
        async with AsyncMCPTestKit(
            env={},
            cwd="/tmp/mcp-pal-no-project",
            store=store,
            embedded_worker=False,
        ) as kit:
            session = kit.agent_session(_spec(), adapter=_TraceHarness())
            async with session:
                await session.send("first")
                await session.send("second")
            result = session.result

        execution_id = result.snapshot.execution_id
        persisted = store.get_report(execution_id)
        assert persisted is not None
        assert persisted.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert persisted.events
        assert sum(event.kind is EventKind.EXECUTION_CREATED for event in persisted.events) == 1
        assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in persisted.events) == 1
        assert [
            event.payload["number"]
            for event in persisted.events
            if event.kind is EventKind.TURN_CREATED
        ] == [1, 2]
        assert store.get_execution_spec(execution_id) == _spec()
        reopened = SQLiteExecutionStore(tmp_path / "caller-async.sqlite")
        try:
            assert reopened.get_trace(execution_id) is not None
        finally:
            reopened.close()
    finally:
        store.close()


def test_caller_sync_agent_session_uses_configured_store_for_all_turns(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "caller-sync.sqlite")
    try:
        with MCPTestKit(
            env={},
            cwd="/tmp/mcp-pal-no-project",
            store=store,
            embedded_worker=False,
        ) as kit:
            session = kit.agent_session(_spec(), adapter=_TraceHarness())
            with session:
                session.send("first")
                session.send("second")
            result = session.result

        execution_id = result.snapshot.execution_id
        persisted = store.get_report(execution_id)
        assert persisted is not None
        assert persisted.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert sum(event.kind is EventKind.EXECUTION_CREATED for event in persisted.events) == 1
        assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in persisted.events) == 1
        assert [
            event.payload["number"]
            for event in persisted.events
            if event.kind is EventKind.TURN_CREATED
        ] == [1, 2]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_caller_async_agent_session_startup_failure_is_terminal_and_persisted(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "caller-startup-failure.sqlite")
    try:
        async with AsyncMCPTestKit(
            env={},
            cwd="/tmp/mcp-pal-no-project",
            store=store,
            embedded_worker=False,
        ) as kit:
            session = kit.agent_session(_spec(), adapter=_StartupFailureHarness())
            with pytest.raises(TransportError):
                await session.__aenter__()
            result = session.result

        execution_id = result.snapshot.execution_id
        persisted = store.get_report(execution_id)
        assert persisted is not None
        assert persisted.snapshot.outcome is ExecutionOutcome.FAILED
        assert sum(event.kind is EventKind.EXECUTION_CREATED for event in persisted.events) == 1
        assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in persisted.events) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_caller_async_agent_session_uses_store_artifact_backend(
    tmp_path: Path,
) -> None:
    class _ArtifactHarness(_TraceHarness):
        async def open(self, launch: object) -> "_ArtifactHarness":
            root = Path(str(getattr(launch, "workspace_root")))
            (root / "result.txt").write_bytes(b"caller-session-artifact")
            return self

    store = SQLiteExecutionStore(tmp_path / "caller-artifact.sqlite")
    try:
        spec = _spec().model_copy(
            update={
                "artifact_policy": ArtifactPolicy.ALWAYS,
                "declared_artifacts": ("result.txt",),
            }
        )
        async with AsyncMCPTestKit(
            env={},
            cwd="/tmp/mcp-pal-no-project",
            store=store,
            embedded_worker=False,
        ) as kit:
            session = kit.agent_session(spec, adapter=_ArtifactHarness())
            async with session:
                await session.send("artifact")
            result = session.result

        assert len(result.artifacts) == 1
        assert store.artifacts.get(result.artifacts[0]) == b"caller-session-artifact"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_submitted_agent_execution_reuses_outer_execution_trace_authority() -> None:
    registry = HarnessAdapterRegistry({"acp": lambda _harness: _TraceHarness()})
    async with AsyncMCPTestKit(
        env={}, cwd="/tmp/mcp-pal-no-project", adapter_registry=registry
    ) as kit:
        handle = kit.submit(_spec(message="hello"))
        result = await handle.result(timeout=5)
        events = [event async for event in handle.events()]

    _assert_trace_identity(result)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert result.trace is not None
    assert [event.kind for event in events] == [event.kind for event in result.trace.events]
    assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in events) == 1
    assert result.trace.events[-1].kind is EventKind.EXECUTION_FINISHED


@pytest.mark.asyncio
async def test_submitted_agent_execution_persists_one_outer_trace_in_sqlite(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "submitted-agent.sqlite")
    try:
        registry = HarnessAdapterRegistry({"acp": lambda _harness: _TraceHarness()})
        async with AsyncMCPTestKit(
            env={},
            cwd="/tmp/mcp-pal-no-project",
            adapter_registry=registry,
            store=store,
        ) as kit:
            result = await kit.run(_spec(message="persist"))

        _assert_trace_identity(result)
        persisted = store.get_report(result.snapshot.execution_id)
        assert persisted is not None
        assert sum(event.kind is EventKind.EXECUTION_CREATED for event in persisted.events) == 1
        assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in persisted.events) == 1
        assert result.trace is not None
        assert [event.event_id for event in persisted.events] == [
            event.event_id for event in result.trace.events
        ]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_caller_async_agent_session_persists_prior_turn_before_terminal_failure(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "caller-late-failure.sqlite")
    try:
        async with AsyncMCPTestKit(
            env={},
            cwd="/tmp/mcp-pal-no-project",
            store=store,
            embedded_worker=False,
        ) as kit:
            session = kit.agent_session(_spec(), adapter=_SecondTurnFailureHarness())
            async with session:
                first = await session.send("first")
                second = await session.send("second")
                assert first.snapshot.outcome is TurnOutcome.COMPLETED
                assert second.snapshot.outcome is TurnOutcome.FAILED
            result = session.result

        _assert_trace_identity(result)
        assert result.snapshot.outcome is ExecutionOutcome.FAILED
        assert len(result.turns) == 2
        persisted = store.get_report(result.snapshot.execution_id)
        assert persisted is not None
        assert persisted.snapshot.outcome is ExecutionOutcome.FAILED
        assert sum(event.kind is EventKind.EXECUTION_CREATED for event in persisted.events) == 1
        assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in persisted.events) == 1
        assert [
            event.payload["number"]
            for event in persisted.events
            if event.kind is EventKind.TURN_CREATED
        ] == [1, 2]
        assert [
            event.turn_id
            for event in persisted.events
            if event.kind is EventKind.TURN_STATE_CHANGED
            and event.payload.get("lifecycle") == TurnLifecycle.FINISHED.value
        ] == [first.snapshot.turn_id, second.snapshot.turn_id]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_direct_startup_failure_finalizes_after_cleanup() -> None:
    session = AsyncAgentSession(_spec(), _StartupFailureHarness())
    with pytest.raises(TransportError):
        await session.__aenter__()

    result = session.result
    _assert_trace_identity(result)
    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.trace is not None
    assert any(event.kind is EventKind.SESSION_STATE_CHANGED for event in result.trace.events)


@pytest.mark.asyncio
async def test_direct_mcp_server_startup_failure_is_traced() -> None:
    session = AsyncAgentSession(
        _spec(),
        _TraceHarness(),
        server_manager=_FailingServerManager(),
    )
    with pytest.raises(TransportError):
        await session.__aenter__()

    result = session.result
    _assert_trace_identity(result)
    assert result.snapshot.outcome is ExecutionOutcome.FAILED


@pytest.mark.asyncio
async def test_direct_terminal_turn_failure_has_provisional_then_final_trace() -> None:
    session = AsyncAgentSession(_spec(), _TerminalFailureHarness())
    await session.__aenter__()
    turn = await session.send("fail")
    assert turn.error is not None
    assert session.result.trace is None
    await session.aclose()

    result = session.result
    _assert_trace_identity(result)
    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    trace = result.trace
    assert trace is not None
    assert any(event.kind is EventKind.TURN_STATE_CHANGED for event in trace.events)


@pytest.mark.asyncio
async def test_terminal_adapter_failure_with_complete_evidence_keeps_complete_trace() -> None:
    session = AsyncAgentSession(_spec(), _TerminalCompleteEvidenceHarness())
    await session.__aenter__()
    turn = await session.send("fail-with-complete-evidence")
    assert turn.snapshot.outcome is TurnOutcome.FAILED
    await session.aclose()

    result = session.result
    _assert_trace_identity(result)
    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.trace is not None
    assert result.trace.completeness == "complete"
    assert result.trace.limitations == ()


@pytest.mark.asyncio
async def test_direct_timeout_is_traced_with_retained_turn_events() -> None:
    session = AsyncAgentSession(_spec(), _TimeoutHarness())
    await session.__aenter__()
    turn = await session.send("slow", timeout=0.001)
    assert turn.snapshot.outcome is not None
    assert turn.snapshot.outcome.value == "timed_out"
    assert session.result.trace is None
    await session.aclose()

    result = session.result
    _assert_trace_identity(result)
    assert result.snapshot.outcome is ExecutionOutcome.TIMED_OUT
    trace = result.trace
    assert trace is not None
    assert any(event.kind is EventKind.TURN_CREATED for event in trace.events)


@pytest.mark.asyncio
async def test_direct_active_cancellation_is_traced_once() -> None:
    adapter = _ActiveCancellationHarness()
    session = AsyncAgentSession(_spec(), adapter)
    await session.__aenter__()
    sending = asyncio.create_task(session.send("cancel-me"))
    await adapter.started.wait()
    await session.cancel()
    await sending

    _assert_trace_identity(session.result)
    assert session.result.snapshot.outcome is ExecutionOutcome.CANCELLED
    assert adapter.cancel_calls == 1


@pytest.mark.asyncio
async def test_close_cancellation_does_not_leave_trace_unfinalized() -> None:
    adapter = _CloseBarrierHarness()
    session = AsyncAgentSession(_spec(), adapter)
    await session.__aenter__()
    closing = asyncio.create_task(session.aclose())
    await adapter.close_called.wait()
    closing.cancel()
    adapter.release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await _wait_for(lambda: session._closed)

    _assert_trace_identity(session.result)
    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED


@pytest.mark.asyncio
async def test_cleanup_retry_publishes_one_partial_terminal_event() -> None:
    adapter = _TransientCleanupHarness()
    session = AsyncAgentSession(_spec(), adapter)
    await session.__aenter__()
    with pytest.raises(CleanupError):
        await session.aclose()
    assert session.result.trace is None
    assert not any(
        event.kind is EventKind.EXECUTION_FINISHED
        for event in session._trace_recorder.events()
    )

    await session.aclose()
    result = session.result
    _assert_trace_identity(result)
    assert result.trace is not None
    assert result.trace.completeness == "partial"
    assert "cleanup_failed" in result.trace.limitations
    assert adapter.close_calls == 2


@pytest.mark.asyncio
async def test_submitted_terminal_failure_has_outer_trace_without_duplicate_ids() -> None:
    registry = HarnessAdapterRegistry({"acp": lambda _harness: _TerminalFailureHarness()})
    async with AsyncMCPTestKit(
        env={}, cwd="/tmp/mcp-pal-no-project", adapter_registry=registry
    ) as kit:
        handle = kit.submit(_spec(message="fail"))
        result = await handle.result(timeout=5)
        events = [event async for event in handle.events()]

    _assert_trace_identity(result)
    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    trace = result.trace
    assert trace is not None
    assert [event.event_id for event in events] == [event.event_id for event in trace.events]
    assert len({event.event_id for event in events}) == len(events)
    assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in events) == 1


@pytest.mark.asyncio
async def test_submitted_active_cancellation_overrides_session_close_outcome() -> None:
    adapter = _ActiveCancellationHarness()
    registry = HarnessAdapterRegistry({"acp": lambda _harness: adapter})
    async with AsyncMCPTestKit(
        env={}, cwd="/tmp/mcp-pal-no-project", adapter_registry=registry
    ) as kit:
        handle = kit.submit(_spec(message="cancel-me"))
        await adapter.started.wait()
        await handle.cancel()
        result = await handle.result(timeout=5)

    _assert_trace_identity(result)
    assert result.snapshot.outcome is ExecutionOutcome.CANCELLED
    assert result.error is not None
    assert result.error.code is ErrorCode.CANCELLED


def test_submitted_agent_trace_reopens_with_identical_canonical_events(tmp_path: Path) -> None:
    async def run() -> tuple[ExecutionResult, tuple[CanonicalEvent, ...], str]:
        path = tmp_path / "agent-trace.sqlite"
        store = SQLiteExecutionStore(path)
        registry = HarnessAdapterRegistry({"acp": lambda _harness: _TraceHarness()})
        async with AsyncMCPTestKit(
            env={}, cwd="/tmp/mcp-pal-no-project", adapter_registry=registry, store=store
        ) as kit:
            handle = kit.submit(_spec(message="persist"))
            result = await handle.result(timeout=10)
            events = tuple([event async for event in handle.events()])
            execution_id = handle.execution_id.root
        return result, events, execution_id

    result, events, execution_id = asyncio.run(run())
    reopened = SQLiteExecutionStore(tmp_path / "agent-trace.sqlite")
    persisted = tuple(reopened.iter_events(execution_id))
    _assert_trace_identity(result)
    assert [event.model_dump(mode="json") for event in persisted] == [
        event.model_dump(mode="json") for event in events
    ]
    trace = result.trace
    assert trace is not None
    assert [event.model_dump(mode="json") for event in persisted] == [
        event.model_dump(mode="json") for event in trace.events
    ]
