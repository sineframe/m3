"""Execution controller and handle contracts."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from mcp.shared.message import SessionMessage
from mcp_types import JSONRPCRequest, JSONRPCResponse

from m3._types.specs import AgentSpec
from m3.agent_session import AdapterTurn
from m3.async_api import AsyncExecutionHandle, AsyncMCPTestKit
from m3.elicitation import expect_form
from m3.errors import OperationTimeout
from m3.execution_runtime import AsyncExecutionController, _activity_health
from m3.harness import HarnessAdapterRegistry
from m3.storage import SQLiteExecutionStore
from m3.sync_api import ExecutionHandle, MCPTestKit
from m3.testing import FaultInjector
from m3.types import (
    ClaudeCode,
    DirectSpec,
    ErrorCode,
    ErrorInfo,
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    ExecutionResult,
    Ping,
    ServerBinding,
    StdioServer,
    TextContent,
    TurnOutcome,
    TurnResponse,
    UserMessage,
)
from m3.workspace import WorkspaceError, WorkspaceManager

pytestmark = pytest.mark.process_lifecycle


def _spec() -> DirectSpec:
    return DirectSpec(
        servers=(ServerBinding(server=FaultInjector().stdio_server()),),
        operation=Ping(),
    )


async def test_normal_async_submit_retains_typed_spec_in_memory() -> None:
    kit = AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project")
    handle = kit.submit(_spec())
    assert handle._store.get_execution_spec(handle.execution_id) == _spec()
    await kit.aclose()


class _SlowClient:
    def __init__(self, started: asyncio.Event) -> None:
        self.started = started
        self.release = asyncio.Event()

    async def __aenter__(self) -> _SlowClient:
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

    async def start(self, _spec: AgentSpec) -> None:
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


class _ActionBoundaryHarness(_ToolEvidenceHarness):
    def __init__(self) -> None:
        super().__init__()
        self.started = 0

    async def start(self, _spec: AgentSpec) -> None:
        self.started += 1


class _SlowHarness:
    async def start(self, _spec: AgentSpec) -> None:
        return None

    async def send(
        self,
        _message: object,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse:
        del timeout, metadata
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_async_submit_publishes_only_committed_ordered_events() -> None:
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
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
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
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
async def test_interrupted_cancel_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    controller = AsyncExecutionController(
        _SlowKit(_SlowClient(started)),
        worker=False,
    )
    handle = controller.submit(_spec())
    await started.wait()

    entered_cancel_sleep = asyncio.Event()
    release_cancel_sleep = asyncio.Event()
    cancel_task: asyncio.Task[None] | None = None
    original_sleep = asyncio.sleep

    async def hold_cancel_sleep(delay: float) -> None:
        if delay == 0 and asyncio.current_task() is cancel_task:
            entered_cancel_sleep.set()
            await release_cancel_sleep.wait()
        await original_sleep(delay)

    try:
        # Interrupt the first caller at the checkpoint immediately before the
        # single-shot task.cancel transition.
        monkeypatch.setattr(asyncio, "sleep", hold_cancel_sleep)
        cancel_task = asyncio.create_task(handle.cancel())
        await entered_cancel_sleep.wait()
        cancel_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancel_task
        release_cancel_sleep.set()
        await handle.cancel()
        result = await handle.result(timeout=2)
    finally:
        if cancel_task is not None and not cancel_task.done():
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    assert result.snapshot.outcome is ExecutionOutcome.CANCELLED
    await controller.close()


@pytest.mark.asyncio
async def test_persistent_cancel_retries_after_transient_request_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "cancel-retry.sqlite")
    started = asyncio.Event()
    controller = AsyncExecutionController(
        _SlowKit(_SlowClient(started)), store=store, worker=False
    )
    handle = controller.submit(_spec())
    claimed = store.claim_next("unit-owner")
    assert claimed is not None
    command, lease = claimed
    original_request_cancel = store.request_cancel
    request_count = 0

    def flaky_request_cancel(
        execution_id: ExecutionId | str, reason: str | None = None
    ) -> bool:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            raise OSError("transient sqlite write failure")
        return original_request_cancel(execution_id, reason)

    monkeypatch.setattr(store, "request_cancel", flaky_request_cancel)
    owner = asyncio.create_task(handle._start_from_worker())
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        with pytest.raises(OSError, match="transient sqlite write failure"):
            await handle.cancel()
        assert not owner.done()
        await handle.cancel()
        result = await handle.result(timeout=2)
        assert request_count == 2
        assert result.snapshot.outcome is ExecutionOutcome.CANCELLED
    finally:
        if not owner.done():
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
        store.complete_command(
            command.id,
            owner_id=lease.owner_id,
            lease_token=lease.lease_token,
            status="cancelled",
        )
        store.release_lease(lease)
        store.close()


@pytest.mark.asyncio
async def test_persistent_cancel_watcher_interrupts_claimed_owner_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation written by another store instance reaches the owner task."""

    store = SQLiteExecutionStore(tmp_path / "watcher.sqlite")
    started = asyncio.Event()
    controller = AsyncExecutionController(
        _SlowKit(_SlowClient(started)), store=store, worker=False
    )
    handle = controller.submit(_spec())
    claimed = store.claim_next("unit-owner")
    assert claimed is not None
    command, lease = claimed
    original_cancellation_requested = store.cancellation_requested
    first_poll_started = threading.Event()
    poll_count = 0

    def flaky_cancellation_requested(execution_id: ExecutionId | str) -> bool:
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            first_poll_started.set()
            time.sleep(0.15)
            raise OSError("transient sqlite read failure")
        return bool(original_cancellation_requested(execution_id))

    monkeypatch.setattr(store, "cancellation_requested", flaky_cancellation_requested)
    owner = asyncio.create_task(handle._start_from_worker())
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert handle._cancel_watcher is not None
        await asyncio.wait_for(asyncio.to_thread(first_poll_started.wait), timeout=2)
        heartbeat = asyncio.Event()

        async def mark_heartbeat() -> None:
            await asyncio.sleep(0.03)
            heartbeat.set()

        heartbeat_task = asyncio.create_task(mark_heartbeat())
        await asyncio.wait_for(heartbeat.wait(), timeout=0.1)
        await heartbeat_task
        assert await asyncio.to_thread(
            store.request_cancel, handle.execution_id, "cross-process"
        )
        await asyncio.wait_for(owner, timeout=3)
        result = await handle.result(timeout=2)

        assert result.snapshot.outcome is ExecutionOutcome.CANCELLED
        assert result.trace is not None
        assert result.trace.completeness == "partial"
        assert handle._cancel_watcher is None
        assert store.complete_command(
            command.id,
            owner_id=lease.owner_id,
            lease_token=lease.lease_token,
            status="cancelled",
        )
        assert store.release_lease(lease)
        # A second cancellation must not append another terminal event.
        await handle.cancel()
        assert (
            sum(
                event.kind is EventKind.EXECUTION_FINISHED
                for event in store.events(handle.execution_id)
            )
            == 1
        )
    finally:
        if not owner.done():
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
        store.close()


@pytest.mark.asyncio
async def test_persistent_cancel_watcher_does_not_overwrite_natural_terminal_result(
    tmp_path: Path,
) -> None:
    """A late durable request cannot turn an already-finished owner cancelled."""

    store = SQLiteExecutionStore(tmp_path / "watcher-natural.sqlite")
    started = asyncio.Event()
    client = _SlowClient(started)
    controller = AsyncExecutionController(_SlowKit(client), store=store, worker=False)
    handle = controller.submit(_spec())
    claimed = store.claim_next("unit-owner")
    assert claimed is not None
    command, lease = claimed
    owner = asyncio.create_task(handle._start_from_worker())
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        client.release.set()
        await asyncio.wait_for(owner, timeout=3)
        result = await handle.result(timeout=2)
        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED

        assert await asyncio.to_thread(
            store.request_cancel, handle.execution_id, "too-late"
        )
        assert (
            store.get_snapshot(handle.execution_id).outcome
            is ExecutionOutcome.COMPLETED
        )
        assert (
            sum(
                event.kind is EventKind.EXECUTION_FINISHED
                for event in store.events(handle.execution_id)
            )
            == 1
        )
        assert store.complete_command(
            command.id,
            owner_id=lease.owner_id,
            lease_token=lease.lease_token,
            status="cancelled",
        )
        assert store.release_lease(lease)
    finally:
        if not owner.done():
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
        store.close()


@pytest.mark.asyncio
async def test_workspace_cleanup_failure_keeps_execution_terminal_and_trace_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_cleanup(_manager: WorkspaceManager) -> None:
        raise WorkspaceError("workspace cleanup failed")

    monkeypatch.setattr(WorkspaceManager, "cleanup", fail_cleanup)
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        result = await kit.run(_spec())

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert result.error is not None
    assert result.error.code is ErrorCode.CLEANUP_FAILED
    assert result.trace is not None
    assert result.trace.completeness == "partial"
    assert "cleanup_failed" in result.trace.limitations


@pytest.mark.asyncio
async def test_event_iterator_can_resume_after_sequence_and_abandon_cleanly() -> None:
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        handle = kit.submit(_spec())
        await handle.result(timeout=10)
        first = handle.events(after_sequence=-1)
        prefix = [await first.__anext__(), await first.__anext__()]
        await cast(Any, first).aclose()
        suffix = [
            event async for event in handle.events(after_sequence=prefix[-1].sequence)
        ]

    assert prefix[-1].sequence < suffix[0].sequence
    assert [event.sequence for event in prefix + suffix] == list(
        range(prefix[0].sequence, suffix[-1].sequence + 1)
    )
    assert suffix[-1].kind is EventKind.EXECUTION_FINISHED


@pytest.mark.asyncio
async def test_agent_without_registered_adapter_returns_typed_unavailable() -> None:
    spec = AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model", executable="m3-missing-claude"),
        message=UserMessage(content=(TextContent(text="run"),)),
    )
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.error is not None
    assert result.error.code.value == "unsupported"


@pytest.mark.asyncio
async def test_submitted_agent_execution_requires_message_and_is_terminal_invalid_argument() -> (
    None
):
    spec = AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model", executable="m3-missing-claude"),
    )
    async with AsyncMCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.error is not None
    assert result.error.code is ErrorCode.INVALID_ARGUMENT
    assert result.error.message == "execution validation failed"


@pytest.mark.asyncio
async def test_submitted_agent_execution_sends_exactly_one_message() -> None:
    adapter = _ToolEvidenceHarness()
    registry = HarnessAdapterRegistry({"claude_code": lambda _harness: adapter})
    spec = AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model"),
        message=UserMessage(content=(TextContent(text="once"),)),
    )
    async with AsyncMCPTestKit(
        env={}, cwd="/tmp/m3-no-project", adapter_registry=registry
    ) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert len(adapter.messages) == 1


@pytest.mark.asyncio
async def test_plan_bound_agent_run_rejects_before_harness_startup() -> None:
    adapter = _ActionBoundaryHarness()
    registry = HarnessAdapterRegistry({"claude_code": lambda _harness: adapter})
    spec = AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model"),
        message=UserMessage(content=(TextContent(text="continue"),)),
        elicitation=expect_form("confirm").accept({"confirmed": True}),
    )

    async with AsyncMCPTestKit(
        env={}, cwd="/tmp/m3-no-project", adapter_registry=registry
    ) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.FAILED
    assert result.error is not None
    assert result.error.code is ErrorCode.UNSUPPORTED
    assert adapter.started == 0
    assert adapter.messages == []


@pytest.mark.asyncio
async def test_persistent_agent_command_reconstructs_action_plan(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "action-plan.sqlite")
    spec = AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model"),
        message=UserMessage(content=(TextContent(text="continue"),)),
        elicitation=expect_form("confirm").accept({"confirmed": True}),
        elicitation_round_limit=6,
    )
    kit = AsyncMCPTestKit(store=store, embedded_worker=False)

    try:
        handle = kit.submit(spec)
        persisted = store.get_execution_spec(handle.execution_id)
        command = store.get_command(f"command-{handle.execution_id.root}")
        assert persisted == spec
        assert command is not None
        restored = AgentSpec.model_validate(command.payload["spec"])
        assert restored == spec
    finally:
        await kit.aclose()
        store.close()


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
async def test_execution_deadline_finalizes_partial_timeout_trace() -> None:
    started = asyncio.Event()
    client = _SlowClient(started)
    controller = AsyncExecutionController(_SlowKit(client))
    spec = _spec().model_copy(update={"timeout_seconds": 0.05})
    handle = controller.submit(spec)
    await started.wait()

    result = await handle.result(timeout=2)

    assert result.snapshot.outcome is ExecutionOutcome.TIMED_OUT
    assert result.trace is not None
    assert "capture_incomplete" in result.trace.limitations
    diagnostics = result.trace.view().diagnostics
    timeout = next(item for item in diagnostics if item.code == "operation_timeout")
    assert timeout.stage == "execution_startup"
    assert timeout.operation == "execution.startup"
    assert timeout.timeout_seconds == 0.05
    assert timeout.elapsed_seconds is not None
    assert timeout.elapsed_seconds >= 0.05
    await controller.close()


@pytest.mark.asyncio
async def test_agent_deadline_identifies_harness_response_wait() -> None:
    adapter = _SlowHarness()
    registry = HarnessAdapterRegistry({"claude_code": lambda _harness: adapter})
    spec = AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model"),
        message=UserMessage(content=(TextContent(text="wait"),)),
        timeout_seconds=0.05,
    )
    async with AsyncMCPTestKit(
        env={}, cwd="/tmp/m3-no-project", adapter_registry=registry
    ) as kit:
        result = await kit.run(spec)

    assert result.snapshot.outcome is ExecutionOutcome.TIMED_OUT
    assert result.trace is not None
    timeout = next(
        item
        for item in result.trace.view().diagnostics
        if item.code == "operation_timeout"
    )
    assert timeout.stage == "waiting_for_harness_response"
    assert timeout.operation == "harness.response"
    assert timeout.timeout_seconds == 0.05
    diagnostics = result.trace.view().diagnostics
    started = next(
        index
        for index, item in enumerate(diagnostics)
        if item.code == "stage_started" and item.stage == "waiting_for_harness_response"
    )
    assert started < diagnostics.index(timeout)


@pytest.mark.asyncio
async def test_agent_tool_errors_do_not_change_lifecycle_but_do_change_health() -> None:
    adapter = _ToolEvidenceHarness()
    spec = AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model"),
    )
    from m3.agent_session import AsyncAgentSession

    async with AsyncAgentSession(spec, adapter) as session:
        first = await session.send("first")
        second = await session.send("second")
        assert first.error is None
        assert second.error is not None
        assert (await session.snapshot()).lifecycle.value == "idle"

    assert session.result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert session.result.activity_health.value == "mixed"


def test_sync_agent_session_result_survives_external_kit_close() -> None:
    from m3.sync_api import MCPTestKit

    spec = AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ClaudeCode(model="test-model"),
    )
    kit = MCPTestKit(env={}, cwd="/tmp/m3-no-project")
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
    from m3.direct_trace import DirectTraceBridge

    bridge = DirectTraceBridge(execution_id="execution-health")
    for identifier, is_error in enumerate(results, start=1):
        bridge.observe(
            SessionMessage(
                message=JSONRPCRequest(
                    jsonrpc="2.0",
                    id=identifier,
                    method="tools/call",
                    params={"name": "echo"},
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
    with MCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        handle = kit.submit(_spec())
        assert isinstance(handle, ExecutionHandle)
        result = handle.result(timeout=10)
        events = tuple(handle.events())

    assert isinstance(result, ExecutionResult)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
    assert events[-1].kind is EventKind.EXECUTION_FINISHED
    assert all(not asyncio.iscoroutine(event) for event in events)


def test_sync_run_delegates_to_submit_and_result() -> None:
    with MCPTestKit(env={}, cwd="/tmp/m3-no-project") as kit:
        result = kit.run(_spec())
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
