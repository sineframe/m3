"""Phase 9 execution controller and handle contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest
from mcp.shared.message import SessionMessage
from mcp_types import JSONRPCRequest, JSONRPCResponse

from mcp_pal.async_api import AsyncExecutionHandle, AsyncMCPTestKit
from mcp_pal.execution_runtime import AsyncExecutionController, _activity_health
from mcp_pal.errors import OperationTimeout
from mcp_pal.agent_session import AdapterTurn
from mcp_pal.harness import HarnessAdapterRegistry
from mcp_pal.sync_api import ExecutionHandle, MCPTestKit
from mcp_pal.testing import FaultInjector
from mcp_pal.types import (
    AgentExecutionSpec,
    ClaudeCode,
    DirectExecutionSpec,
    EventKind,
    ExecutionOutcome,
    ExecutionResult,
    PingOperation,
    ServerBinding,
    StdioServer,
    ErrorInfo,
    ErrorCode,
    TextContent,
    TurnResponse,
    TurnOutcome,
    UserMessage,
)
from mcp_pal.workspace import WorkspaceError, WorkspaceManager


def _spec() -> DirectExecutionSpec:
    return DirectExecutionSpec(
        servers=(ServerBinding(server=FaultInjector().stdio_server()),),
        operation=PingOperation(),
    )


class _SlowClient:
    def __init__(self, started: asyncio.Event) -> None:
        self.started = started
        self.release = asyncio.Event()

    async def __aenter__(self) -> "_SlowClient":
        self.started.set()
        await self.release.wait()
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def ping(self) -> object:
        return SimpleNamespace(raw=None, result_type=None)


class _SlowKit:
    def __init__(self, client: _SlowClient) -> None:
        self.client = client

    def direct(self, _server: object, **_options: object) -> _SlowClient:
        return self.client


class _ToolEvidenceHarness:
    def __init__(self) -> None:
        self.failed = False
        self.messages: list[object] = []

    async def start(self, _spec: AgentExecutionSpec) -> None:
        return None

    async def send(
        self,
        _message: object,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse | AdapterTurn:
        del timeout, metadata
        self.messages.append(_message)
        if self.failed:
            return AdapterTurn(
                error=ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="tool error"),
                outcome=TurnOutcome.FAILED,
                tool_calls=({"is_error": True},),
            )
        self.failed = True
        return AdapterTurn(
            response=TurnResponse(content=(TextContent(text="ok"),)),
            tool_calls=({"is_error": False},),
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_async_submit_publishes_only_committed_ordered_events() -> None:
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        handle = kit.submit(_spec())
        assert isinstance(handle, AsyncExecutionHandle)
        observed: list[int] = []
        unsubscribe = handle.on_event(lambda event: observed.append(event.sequence))
        result = await handle.result(timeout=10)
        events = [event async for event in handle.events(after_sequence=-1)]
        unsubscribe()

    assert isinstance(result, ExecutionResult)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert result.trace is not None
    assert [event.sequence for event in events] == list(range(len(events)))
    assert events[-1].kind is EventKind.EXECUTION_FINISHED
    assert observed == sorted(observed)
    assert set(observed).issubset({event.sequence for event in events})


@pytest.mark.asyncio
async def test_async_cancel_is_terminal_and_idempotent() -> None:
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        handle = kit.submit(_spec())
        await handle.cancel()
        await handle.cancel()
        result = await handle.result(timeout=2)

    assert result.snapshot.outcome is ExecutionOutcome.CANCELLED
    assert result.error is not None
    assert result.error.code.value == "cancelled"
    events = [event async for event in handle.events()]
    assert events[-1].kind is EventKind.EXECUTION_FINISHED


@pytest.mark.asyncio
async def test_workspace_cleanup_failure_keeps_execution_terminal_and_trace_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_cleanup(_manager: WorkspaceManager) -> None:
        raise WorkspaceError("workspace cleanup failed")

    monkeypatch.setattr(WorkspaceManager, "cleanup", fail_cleanup)
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        result = await kit.run(_spec())

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert result.error is not None
    assert result.error.code is ErrorCode.CLEANUP_FAILED
    assert result.trace is not None
    assert result.trace.completeness == "partial"
    assert "cleanup_failed" in result.trace.limitations


@pytest.mark.asyncio
async def test_event_iterator_can_resume_after_sequence_and_abandon_cleanly() -> None:
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        handle = kit.submit(_spec())
        await handle.result(timeout=10)
        first = handle.events(after_sequence=-1)
        prefix = [await first.__anext__(), await first.__anext__()]
        await cast(Any, first).aclose()
        suffix = [event async for event in handle.events(after_sequence=prefix[-1].sequence)]

    assert prefix[-1].sequence < suffix[0].sequence
    assert [event.sequence for event in prefix + suffix] == list(
        range(prefix[0].sequence, suffix[-1].sequence + 1)
    )
    assert suffix[-1].kind is EventKind.EXECUTION_FINISHED


@pytest.mark.asyncio
async def test_agent_without_registered_adapter_returns_typed_unavailable() -> None:
    spec = AgentExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model", executable="mcp-pal-missing-claude"),
        message=UserMessage(content=(TextContent(text="run"),)),
    )
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.error is not None
    assert result.error.code.value == "unsupported"


@pytest.mark.asyncio
async def test_submitted_agent_execution_requires_message_and_is_terminal_invalid_argument() -> None:
    spec = AgentExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model", executable="mcp-pal-missing-claude"),
    )
    async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.error is not None
    assert result.error.code is ErrorCode.INVALID_ARGUMENT
    assert result.error.message == "execution validation failed"


@pytest.mark.asyncio
async def test_submitted_agent_execution_sends_exactly_one_message() -> None:
    adapter = _ToolEvidenceHarness()
    registry = HarnessAdapterRegistry({"claude_code": lambda _harness: adapter})
    spec = AgentExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model"),
        message=UserMessage(content=(TextContent(text="once"),)),
    )
    async with AsyncMCPTestKit(
        env={}, cwd="/tmp/mcp-pal-no-project", adapter_registry=registry
    ) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert len(adapter.messages) == 1


@pytest.mark.asyncio
async def test_result_wait_timeout_does_not_cancel_background_execution() -> None:
    started = asyncio.Event()
    client = _SlowClient(started)
    controller = AsyncExecutionController(_SlowKit(client))
    handle = controller.submit(_spec())
    await started.wait()

    with pytest.raises(OperationTimeout):
        await handle.result(timeout=0.001)
    assert not handle._terminal.is_set()

    client.release.set()
    result = await handle.result(timeout=2)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    await controller.close()


@pytest.mark.asyncio
async def test_agent_tool_errors_do_not_change_lifecycle_but_do_change_health() -> None:
    adapter = _ToolEvidenceHarness()
    spec = AgentExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model"),
    )
    from mcp_pal.agent_session import AsyncAgentSession

    async with AsyncAgentSession(spec, adapter) as session:
        first = await session.send("first")
        second = await session.send("second")
        assert first.error is None
        assert second.error is not None
        assert (await session.snapshot()).lifecycle.value == "idle"

    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert session.result.activity_health.value == "mixed"


def test_sync_agent_session_result_survives_external_kit_close() -> None:
    from mcp_pal.sync_api import MCPTestKit

    spec = AgentExecutionSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model"),
    )
    kit = MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project")
    session = kit.agent_session(spec, adapter=_ToolEvidenceHarness())
    session.__enter__()
    kit.close()
    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ((), "no_calls"),
        ((False,), "all_succeeded"),
        ((True,), "all_failed"),
        ((False, True), "mixed"),
    ],
)
def test_direct_activity_health_is_independent_of_execution_outcome(
    results: tuple[bool, ...], expected: str
) -> None:
    from mcp_pal.direct_trace import DirectTraceBridge

    bridge = DirectTraceBridge(execution_id="execution-health")
    for identifier, is_error in enumerate(results, start=1):
        bridge.observe(
            SessionMessage(
                message=JSONRPCRequest(
                    jsonrpc="2.0", id=identifier, method="tools/call", params={"name": "echo"}
                )
            ),
            "outbound",
        )
        bridge.observe(
            SessionMessage(
                message=JSONRPCResponse(
                    jsonrpc="2.0",
                    id=identifier,
                    result={"content": [], "isError": is_error},
                )
            ),
            "inbound",
        )
    assert _activity_health(bridge.trace).value == expected


def test_sync_handle_is_a_blocking_twin_without_async_values() -> None:
    with MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        handle = kit.submit(_spec())
        assert isinstance(handle, ExecutionHandle)
        result = handle.result(timeout=10)
        events = tuple(handle.events())

    assert isinstance(result, ExecutionResult)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert events[-1].kind is EventKind.EXECUTION_FINISHED
    assert all(not asyncio.iscoroutine(event) for event in events)


def test_sync_run_delegates_to_submit_and_result() -> None:
    with MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        result = kit.run(_spec())
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
