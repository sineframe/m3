"""Focused same-worker managed-input runtime tests."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import MutableMapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from m3._types.specs import AgentSpec
from m3.async_api import AsyncMCPTestKit
from m3.elicitation import (
    ElicitationResponse,
    FormElicitationRequest,
    PendingElicitationRound,
)
from m3.errors import (
    ElicitationRoundLimitError,
    ManagedInputConflict,
    ManagedInputRecoveryError,
    ManagedInputValidationError,
    OperationCancelled,
    OperationTimeout,
)
from m3.execution_trace import ExecutionTraceRecorder
from m3.harness import (
    DeterministicHarnessAdapter,
    HarnessAdapterCapabilities,
    HarnessAdapterRegistry,
    HarnessLaunch,
    HarnessSession,
    HarnessSessionSnapshot,
    HarnessTurnRequest,
    HarnessTurnResult,
)
from m3.harness.contracts import HarnessInteractionCapabilities
from m3.managed_input_api import respond_elicitation
from m3.managed_runtime import (
    ManagedInputRuntime,
    ManagedRuntimeMismatch,
    ManagedRuntimeStateError,
    _ManagedInputCoordinator,
)
from m3.storage import SQLiteExecutionStore
from m3.sync_api import MCPTestKit
from m3.types import (
    EventKind,
    ExecutionId,
    ExecutionStatus,
    OpenCode,
    ServerBinding,
    StdioServer,
    TextContent,
    UserMessage,
)


def _pending(execution_id: str, round_id: str = "round-1") -> PendingElicitationRound:
    return PendingElicitationRound(
        round_id=round_id,
        execution_id=execution_id,
        logical_operation_id="operation-1",
        server="shipping",
        operation_kind="tool",
        operation_name="book",
        request_state="opaque-state",
        requests={
            "address": FormElicitationRequest(
                request_key="address",
                message="Address",
                requested_schema={
                    "type": "object",
                    "required": ["city"],
                    "properties": {"city": {"type": "string"}},
                },
            )
        },
        created_at=datetime.now(timezone.utc),
    )


def _managed_registry(
    adapter_holder: dict[str, DeterministicHarnessAdapter] | None = None,
) -> HarnessAdapterRegistry:
    capabilities = HarnessAdapterCapabilities(
        name="managed-fake",
        interaction=HarnessInteractionCapabilities(
            supports_elicitation=True,
            preserves_request_keys=True,
            preserves_multi_request_rounds=True,
            supports_interaction_cancellation=True,
        ),
    )

    def factory(_spec: object) -> DeterministicHarnessAdapter:
        calls = 0

        def handler(
            _request: HarnessTurnRequest, state: MutableMapping[str, Any]
        ) -> HarnessTurnResult | str:
            nonlocal calls
            calls += 1
            if calls == 1:
                runtime = cast(ManagedInputRuntime, state["_managed_input_runtime"])
                execution = runtime.execution_id  # private fake-adapter hook
                return HarnessTurnResult(
                    sequence=1,
                    status="completed",
                    elicitation=_pending(execution),
                    operation_parameters={"arguments": {"amount": 1}},
                )
            return "completed"

        adapter = DeterministicHarnessAdapter(
            name="managed-fake", capabilities=capabilities, handler=handler
        )
        if adapter_holder is not None:
            adapter_holder["adapter"] = adapter
        return adapter

    return HarnessAdapterRegistry({"opencode": factory})


def _agent_spec() -> AgentSpec:
    return AgentSpec(
        harness=OpenCode(model="fake/model"),
        message=UserMessage(content=(TextContent(text="book"),)),
        servers=(
            ServerBinding(
                server=StdioServer(name="shipping", command="echo"),
                required=False,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_response_commit_wakes_waiter_and_preserves_lifecycle(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite")
    execution_id = ExecutionId("execution-runtime")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store,
        execution_id.root,
        recorder,
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(execution_id.root)

    persisted = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(persisted.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    waiting = asyncio.create_task(
        coordinator.await_round(
            pending,
            {
                "target": "shipping/book",
                "arguments": {"amount": 1},
                "meta": {"request_id": "request-1"},
                "task": {"id": "task-1"},
            },
        )
    )
    await asyncio.wait_for(persisted.wait(), 2)
    unsubscribe()
    record = store.managed_input_store.get_round(execution_id.root, pending.round_id)
    assert record is not None and record.status == "pending"
    assert record.operation_parameters == {
        "target": "shipping/book",
        "arguments": {"amount": 1},
        "meta": {"request_id": "request-1"},
        "task": {"id": "task-1"},
        "logical_operation_id": "operation-1",
        "operation_kind": "tool",
        "operation_name": "book",
    }
    with pytest.raises(ManagedInputValidationError):
        respond_elicitation(
            store,
            execution_id,
            pending.round_id,
            {"wrong": ElicitationResponse(action="decline")},
            idempotency_key="invalid",
        )
    assert not waiting.done()
    respond_elicitation(
        store,
        execution_id,
        pending.round_id,
        {"address": ElicitationResponse(action="accept", content={"city": "Pune"})},
        idempotency_key="response-1",
    )
    coordinator.notify_response(pending.round_id)
    responses = await asyncio.wait_for(waiting, 2)
    assert responses["address"].content == {"city": "Pune"}
    await coordinator.resolve_round(pending.round_id)
    assert (
        store.managed_input_store.get_round(execution_id.root, pending.round_id).status
        == "resolved"
    )
    kinds = [event.kind for event in recorder.events()]
    assert EventKind.ELICITATION_REQUEST in kinds
    assert EventKind.ELICITATION_RESPONSE in kinds
    assert kinds.index(EventKind.ELICITATION_REQUEST) < kinds.index(
        EventKind.ELICITATION_RESPONSE
    )
    response_event = next(
        event
        for event in recorder.events()
        if event.kind is EventKind.ELICITATION_RESPONSE
    )
    redacted = store.managed_input_store.redacted_responses(
        execution_id.root, pending.round_id
    )
    assert response_event.payload["responses"] == dict(redacted or {})
    assert "Pune" not in repr(response_event.payload)
    assert recorder.snapshot().lifecycle is ExecutionStatus.RUNNING_TURN


@pytest.mark.asyncio
async def test_response_commit_from_another_store_wakes_waiter(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cross-process.sqlite"
    worker_store = SQLiteExecutionStore(database)
    responder_store = SQLiteExecutionStore(database)
    execution_id = ExecutionId("cross-process-runtime")
    recorder = ExecutionTraceRecorder(worker_store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        worker_store.managed_input_store,
        execution_id.root,
        recorder,
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(execution_id.root)
    waiting = asyncio.create_task(coordinator.await_round(pending))

    async def wait_for_pending() -> None:
        while True:
            record = worker_store.managed_input_store.get_round(
                execution_id.root, pending.round_id
            )
            if record is not None and record.status == "pending":
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_for_pending(), 2)
    respond_elicitation(
        responder_store,
        execution_id.root,
        pending.round_id,
        {"address": ElicitationResponse(action="accept", content={"city": "Pune"})},
        idempotency_key="cross-process-response",
    )

    responses = await asyncio.wait_for(waiting, 2)
    assert responses["address"].content == {"city": "Pune"}
    assert recorder.snapshot().lifecycle is ExecutionStatus.RUNNING_TURN
    await coordinator.resolve_round(pending.round_id)
    assert (
        responder_store.managed_input_store.get_round(
            execution_id.root, pending.round_id
        ).status
        == "resolved"
    )


@pytest.mark.asyncio
async def test_round_limit_rejects_the_eleventh_round_without_persisting(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite")
    execution_id = ExecutionId("execution-limit")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store,
        execution_id.root,
        recorder,
        round_limit=1,
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(execution_id.root)
    persisted = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(persisted.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    first = asyncio.create_task(coordinator.await_round(pending))
    await asyncio.wait_for(persisted.wait(), 2)
    unsubscribe()
    respond_elicitation(
        store,
        execution_id,
        pending.round_id,
        {"address": ElicitationResponse(action="decline")},
        idempotency_key="response-1",
    )
    coordinator.notify_response(pending.round_id)
    await first
    await coordinator.resolve_round(pending.round_id)
    with pytest.raises(ElicitationRoundLimitError):
        await coordinator.await_round(_pending(execution_id.root, "round-2"))
    assert store.managed_input_store.get_round(execution_id.root, "round-2") is None


@pytest.mark.asyncio
async def test_async_submit_uses_managed_runtime_end_to_end(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "async-execution.sqlite")
    async with AsyncMCPTestKit(
        env={}, cwd=tmp_path, store=store, adapter_registry=_managed_registry()
    ) as kit:
        handle = kit.submit(_agent_spec(), human_input="managed")
        pending = None
        async for event in handle.events():
            if event.kind is EventKind.ELICITATION_REQUEST:
                pending = await handle.pending_elicitation()
                break
        assert pending is not None
        await handle.respond_elicitation(
            pending.round_id,
            {"address": ElicitationResponse(action="decline")},
            idempotency_key="async-response",
        )
        result = await handle.result(5)
        assert result.snapshot.outcome is not None
        assert result.snapshot.outcome.value == "completed"
        assert result.trace is not None
        assert any(
            event.kind is EventKind.ELICITATION_RESPONSE
            for event in result.trace.events
        )


@pytest.mark.asyncio
async def test_managed_round_binds_adapter_harness_session_identity(
    tmp_path: Path,
) -> None:
    adapter_holder: dict[str, DeterministicHarnessAdapter] = {}

    def factory(_spec: object) -> DeterministicHarnessAdapter:
        adapter_factory = _managed_registry(adapter_holder)._factories["opencode"]
        return adapter_factory(_spec)

    store = SQLiteExecutionStore(tmp_path / "harness-session-identity.sqlite")
    async with AsyncMCPTestKit(
        env={},
        cwd=tmp_path,
        store=store,
        adapter_registry=HarnessAdapterRegistry({"opencode": factory}),
    ) as kit:
        handle = kit.submit(_agent_spec(), human_input="managed")
        async for event in handle.events():
            if event.kind is EventKind.ELICITATION_REQUEST:
                break
        pending = await handle.pending_elicitation()
        assert pending is not None
        record = store.managed_input_store.get_round(
            handle.execution_id.root, pending.round_id
        )
        adapter = adapter_holder["adapter"]
        assert adapter._active_session is not None
        assert record is not None
        assert record.harness_session_id == adapter._active_session.session_id
        assert record.session_id != record.harness_session_id
        await handle.respond_elicitation(
            pending.round_id,
            {"address": ElicitationResponse(action="decline")},
            idempotency_key="harness-session-response",
        )
        await handle.result(5)


def test_sync_submit_uses_managed_runtime_end_to_end(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "sync-execution.sqlite")
    kit = MCPTestKit(
        env={}, cwd=tmp_path, store=store, adapter_registry=_managed_registry()
    )
    try:
        handle = kit.submit(_agent_spec(), human_input="managed")
        pending = None
        for event in handle.events():
            if event.kind is EventKind.ELICITATION_REQUEST:
                pending = handle.pending_elicitation()
                break
        assert pending is not None
        handle.respond_elicitation(
            pending.round_id,
            {"address": ElicitationResponse(action="decline")},
            idempotency_key="sync-response",
        )
        result = handle.result(5)
        assert result.snapshot.outcome is not None
        assert result.snapshot.outcome.value == "completed"
    finally:
        kit.close()


@pytest.mark.asyncio
async def test_managed_runtime_rechecks_capability_after_adapter_downgrade(
    tmp_path: Path,
) -> None:
    capabilities = HarnessAdapterCapabilities(
        name="managed-downgrade",
        interaction=HarnessInteractionCapabilities(
            supports_elicitation=True,
            preserves_request_keys=True,
            preserves_multi_request_rounds=True,
            supports_interaction_cancellation=True,
        ),
    )
    calls = 0

    class DowngradingAdapter(DeterministicHarnessAdapter):
        async def open(self, launch: HarnessLaunch) -> HarnessSession:
            delegate = await super().open(launch)

            class DowngradedSession:
                @property
                def session_id(self) -> str:
                    return delegate.session_id

                @property
                def capabilities(self) -> HarnessAdapterCapabilities:
                    return HarnessAdapterCapabilities(name="downgraded")

                async def send(self, request: HarnessTurnRequest) -> HarnessTurnResult:
                    return await delegate.send(request)

                async def cancel(self) -> None:
                    await delegate.cancel()

                def snapshot(self) -> HarnessSessionSnapshot:
                    return delegate.snapshot()

                async def close(self) -> None:
                    await delegate.close()

                def _set_managed_input_runtime(
                    self, runtime: ManagedInputRuntime
                ) -> None:
                    delegate._set_managed_input_runtime(runtime)

            return DowngradedSession()

    def factory(_spec: object) -> DeterministicHarnessAdapter:
        def handler(
            _request: HarnessTurnRequest, _state: MutableMapping[str, Any]
        ) -> str:
            nonlocal calls
            calls += 1
            return "should not run"

        return DowngradingAdapter(
            name="managed-downgrade", capabilities=capabilities, handler=handler
        )

    store = SQLiteExecutionStore(tmp_path / "capability-downgrade.sqlite")
    registry = HarnessAdapterRegistry({"opencode": factory})
    async with AsyncMCPTestKit(
        env={}, cwd=tmp_path, store=store, adapter_registry=registry
    ) as kit:
        handle = kit.submit(_agent_spec(), human_input="managed")
        result = await handle.result(5)
    assert calls == 0
    assert capabilities.interaction.supports_elicitation is True
    assert result.trace is not None
    assert any(
        event.kind is EventKind.TURN_STATE_CHANGED
        and event.payload.get("outcome") == "failed"
        for event in result.trace.events
    )


@pytest.mark.asyncio
async def test_cancel_fails_round_and_completes_waiter(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "cancel.sqlite")
    execution_id = ExecutionId("execution-cancel")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(execution_id.root)
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    waiter = asyncio.create_task(coordinator.await_round(pending))
    await asyncio.wait_for(requested.wait(), 2)
    unsubscribe()
    await coordinator.cancel(OperationCancelled("caller stopped execution"))
    with pytest.raises(OperationCancelled):
        await asyncio.wait_for(waiter, 2)
    record = store.managed_input_store.get_round(execution_id.root, pending.round_id)
    assert record is not None
    assert record.status == "failed"
    assert record.failure_code == "cancelled"
    with pytest.raises(OperationCancelled, match="closed"):
        await coordinator.await_round(
            _pending(execution_id.root, round_id="after-cancel")
        )
    assert (
        store.managed_input_store.get_round(execution_id.root, "after-cancel") is None
    )


@pytest.mark.asyncio
async def test_resolution_failure_terminalizes_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteExecutionStore(tmp_path / "resolve-failure.sqlite")
    execution_id = ExecutionId("execution-resolve-failure")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(execution_id.root)
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    waiter = asyncio.create_task(coordinator.await_round(pending))
    await asyncio.wait_for(requested.wait(), 2)
    unsubscribe()
    respond_elicitation(
        store,
        execution_id,
        pending.round_id,
        {"address": ElicitationResponse(action="decline")},
        idempotency_key="resolve-failure-response",
    )
    coordinator.notify_response(pending.round_id)
    await waiter

    def fail_resolve(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected resolve failure")

    monkeypatch.setattr(store.managed_input_store, "resolve", fail_resolve)
    with pytest.raises(ManagedRuntimeStateError, match="resolution failed"):
        await coordinator.resolve_round(pending.round_id)
    record = store.managed_input_store.get_round(execution_id.root, pending.round_id)
    assert record is not None
    assert record.status == "failed"


@pytest.mark.asyncio
async def test_timeout_cleans_waiter_and_fails_round(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "timeout.sqlite")
    execution_id = ExecutionId("execution-timeout")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(
        execution_id.root,
    ).model_copy(
        update={"deadline": datetime.now(timezone.utc) + timedelta(seconds=0.03)}
    )
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    waiter = asyncio.create_task(coordinator.await_round(pending))
    await asyncio.wait_for(requested.wait(), 2)
    unsubscribe()
    with pytest.raises(OperationTimeout):
        await asyncio.wait_for(waiter, 2)
    record = store.managed_input_store.get_round(execution_id.root, pending.round_id)
    assert record is not None and record.status == "failed"
    assert coordinator._waiters == {}
    assert coordinator._renew_task is None


@pytest.mark.asyncio
async def test_lease_renews_until_round_is_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteExecutionStore(tmp_path / "renew.sqlite")
    execution_id = ExecutionId("execution-renew")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator._LEASE_SECONDS = 0.06
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(execution_id.root)
    requested = asyncio.Event()
    renewed = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    original_renew = store.managed_input_store.renew

    def tracking_renew(*args: object, **kwargs: object) -> object:
        result = original_renew(*args, **kwargs)
        loop.call_soon_threadsafe(renewed.set)
        return result

    monkeypatch.setattr(store.managed_input_store, "renew", tracking_renew)
    waiter = asyncio.create_task(coordinator.await_round(pending))
    await asyncio.wait_for(requested.wait(), 2)
    unsubscribe()
    respond_elicitation(
        store,
        execution_id,
        pending.round_id,
        {"address": ElicitationResponse(action="decline")},
        idempotency_key="renew-response",
    )
    coordinator.notify_response(pending.round_id)
    await waiter
    await asyncio.wait_for(renewed.wait(), 2)
    await coordinator.resolve_round(pending.round_id, operation_complete=True)
    assert coordinator._renew_task is None


@pytest.mark.parametrize("expire_before_failure", (False, True))
@pytest.mark.asyncio
async def test_renewal_failure_wakes_waiter_with_typed_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expire_before_failure: bool,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "renew-failure.sqlite")
    execution_id = ExecutionId("execution-renew-failure")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator._LEASE_SECONDS = 0.03 if expire_before_failure else 0.3
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(execution_id.root)
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )

    def fail_renew(*args: object, **kwargs: object) -> object:
        if expire_before_failure:
            with sqlite3.connect(store.database) as connection:
                connection.execute(
                    "UPDATE m3_managed_input_rounds SET lease_expires_at=? WHERE execution_id=? AND round_id=?",
                    (
                        "2000-01-01T00:00:00+00:00",
                        execution_id.root,
                        pending.round_id,
                    ),
                )
                connection.commit()
        raise RuntimeError("renewal unavailable")

    monkeypatch.setattr(store.managed_input_store, "renew", fail_renew)
    waiter = asyncio.create_task(coordinator.await_round(pending))
    await asyncio.wait_for(requested.wait(), 2)
    unsubscribe()
    with pytest.raises(ManagedInputRecoveryError, match="lease renewal failure"):
        await asyncio.wait_for(waiter, 2)
    record = store.managed_input_store.get_round(execution_id.root, pending.round_id)
    assert record is not None
    assert record.status == "failed"
    assert record.failure_code == "recovery_unavailable"
    assert coordinator._waiters == {}
    assert coordinator._renew_task is None


@pytest.mark.parametrize("takeover", (False, True))
@pytest.mark.asyncio
async def test_recovery_terminalization_retries_after_fallback_lease_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    takeover: bool,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "recovery-race.sqlite")
    execution_id = ExecutionId("execution-recovery-race")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator._LEASE_SECONDS = 0.3
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(execution_id.root)
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    waiter = asyncio.create_task(coordinator.await_round(pending))
    await asyncio.wait_for(requested.wait(), 2)
    unsubscribe()
    managed = store.managed_input_store
    record = managed.get_round(execution_id.root, pending.round_id)
    assert record is not None
    original_fail_recovery = managed.fail_recovery
    recovery_calls = 0

    def reject_first_recovery(*args: object, **kwargs: object) -> object:
        nonlocal recovery_calls
        recovery_calls += 1
        if recovery_calls == 1:
            raise ManagedInputConflict("lease is still held")
        return original_fail_recovery(*args, **kwargs)

    def race_fallback(*args: object, **kwargs: object) -> object:
        current = managed.get_round(execution_id.root, pending.round_id)
        assert current is not None
        with sqlite3.connect(store.database) as connection:
            connection.execute(
                "UPDATE m3_managed_input_rounds SET lease_expires_at=? WHERE execution_id=? AND round_id=?",
                ("2000-01-01T00:00:00+00:00", execution_id.root, pending.round_id),
            )
            connection.commit()
        if takeover:
            claimed = managed.claim_round(
                execution_id.root,
                pending.round_id,
                owner_id="replacement-worker",
                lease_seconds=30,
                expected_lease_token=current.lease_token,
                delivery_state="not_started",
            )
            assert claimed.owner_id == "replacement-worker"
        raise ManagedInputConflict("fallback lost lease race")

    monkeypatch.setattr(managed, "fail_recovery", reject_first_recovery)
    monkeypatch.setattr(managed, "fail", race_fallback)
    await coordinator._fail_recovery(
        record,
        ManagedInputRecoveryError("worker recovery is no longer safe"),
    )
    expected_error = "managed input recovery" if takeover else "worker recovery"
    with pytest.raises(ManagedInputRecoveryError, match=expected_error):
        await asyncio.wait_for(waiter, 2)
    assert recovery_calls == 2
    final = managed.get_round(execution_id.root, pending.round_id)
    assert final is not None
    if takeover:
        assert final.status == "pending"
        assert final.owner_id == "replacement-worker"
    else:
        assert final.status == "failed"
        assert final.failure_code == "recovery_unavailable"
    assert coordinator._waiters == {}
    assert coordinator._renew_task is None


@pytest.mark.asyncio
async def test_expired_round_lease_is_terminal_recovery_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteExecutionStore(tmp_path / "expired-round.sqlite")
    execution_id = ExecutionId("execution-expired-round")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator._LEASE_SECONDS = 0.03
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(execution_id.root)
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )

    def expire_then_fail(*args: object, **kwargs: object) -> object:
        with sqlite3.connect(store.database) as connection:
            connection.execute(
                "UPDATE m3_managed_input_rounds SET lease_expires_at=?",
                ("2000-01-01T00:00:00+00:00",),
            )
        raise ManagedInputConflict("round lease expired")

    monkeypatch.setattr(store.managed_input_store, "renew", expire_then_fail)
    waiter = asyncio.create_task(coordinator.await_round(pending))
    await asyncio.wait_for(requested.wait(), 2)
    unsubscribe()
    with pytest.raises(ManagedInputRecoveryError, match="cannot be resumed"):
        await asyncio.wait_for(waiter, 2)
    record = store.managed_input_store.get_round(execution_id.root, pending.round_id)
    assert record is not None
    assert record.status == "failed"
    assert record.failure_code == "recovery_unavailable"
    assert coordinator._waiters == {}
    assert coordinator._renew_task is None


@pytest.mark.asyncio
async def test_external_recovery_failure_wakes_and_cleans_active_waiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteExecutionStore(tmp_path / "external-recovery.sqlite")
    execution_id = ExecutionId("execution-external-recovery")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator._LEASE_SECONDS = 0.03
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    pending = _pending(execution_id.root)
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )

    def terminalize_then_raise(*args: object, **kwargs: object) -> object:
        record = store.managed_input_store.get_round(
            execution_id.root, pending.round_id
        )
        assert record is not None
        with sqlite3.connect(store.database) as connection:
            connection.execute(
                "UPDATE m3_managed_input_rounds SET lease_expires_at=?",
                ("2000-01-01T00:00:00+00:00",),
            )
        store.managed_input_store.fail_recovery(
            execution_id.root,
            pending.round_id,
            expected_lease_token=record.lease_token,
            message="replacement worker cannot resume interaction",
        )
        return store.managed_input_store.get_round(execution_id.root, pending.round_id)

    monkeypatch.setattr(store.managed_input_store, "renew", terminalize_then_raise)
    waiter = asyncio.create_task(coordinator.await_round(pending))
    await asyncio.wait_for(requested.wait(), 2)
    unsubscribe()
    with pytest.raises(ManagedInputRecoveryError, match="cannot be resumed"):
        await asyncio.wait_for(waiter, 2)
    assert coordinator._waiters == {}
    assert coordinator._renew_task is None


@pytest.mark.asyncio
async def test_operation_identity_allows_multi_round_and_then_sequential_operation(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "identity.sqlite")
    execution_id = ExecutionId("execution-identity")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )

    async def complete(pending: PendingElicitationRound, *, final: bool) -> None:
        requested.clear()
        waiter = asyncio.create_task(coordinator.await_round(pending))
        await asyncio.wait_for(requested.wait(), 2)
        respond_elicitation(
            store,
            execution_id,
            pending.round_id,
            {"address": ElicitationResponse(action="decline")},
            idempotency_key=f"response-{pending.round_id}",
        )
        coordinator.notify_response(pending.round_id)
        await waiter
        await coordinator.resolve_round(pending.round_id, operation_complete=final)

    first = _pending(execution_id.root, "round-1")
    second = _pending(execution_id.root, "round-2")
    third = first.model_copy(
        update={"round_id": "round-3", "server": "billing", "operation_name": "charge"}
    )
    await complete(first, final=False)
    await complete(second, final=True)
    await complete(third, final=True)
    unsubscribe()


@pytest.mark.asyncio
async def test_failed_round_clears_operation_identity_for_a_later_operation(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "failed-operation.sqlite")
    execution_id = ExecutionId("execution-failed-operation")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )

    first = _pending(execution_id.root, "failed-round")
    waiting = asyncio.create_task(
        coordinator.await_round(
            first,
            {
                "target": "shipping/book",
                "arguments": {"weight": 1},
                "meta": {"request_id": "first"},
                "task": {"id": "task-first"},
            },
        )
    )
    await asyncio.wait_for(requested.wait(), 2)
    await coordinator.fail_round(ManagedRuntimeStateError("round failed"))
    with pytest.raises(ManagedRuntimeStateError, match="round failed"):
        await waiting

    requested.clear()
    second = first.model_copy(
        update={"round_id": "later-round", "logical_operation_id": "operation-2"}
    )
    later = asyncio.create_task(
        coordinator.await_round(
            second,
            {
                "target": "billing/charge",
                "arguments": {"amount": 2},
                "meta": {"request_id": "second"},
                "task": {"id": "task-second"},
            },
        )
    )
    await asyncio.wait_for(requested.wait(), 2)
    respond_elicitation(
        store,
        execution_id,
        second.round_id,
        {"address": ElicitationResponse(action="decline")},
        idempotency_key="later-response",
    )
    coordinator.notify_response(second.round_id)
    await later
    await coordinator.resolve_round(second.round_id, operation_complete=True)
    unsubscribe()


@pytest.mark.asyncio
async def test_operation_identity_includes_exact_retry_parameters(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "operation-parameters.sqlite")
    execution_id = ExecutionId("execution-operation-parameters")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    first = _pending(execution_id.root, "parameter-round-1")
    original = {
        "target": "shipping/book",
        "arguments": {"weight": 1},
        "meta": {"request_id": "same"},
        "task": {"id": "task-same"},
        "session_id": "session-1",
        "turn_id": "turn-1",
        "requestState": "round-one",
        "inputResponses": {"address": {"action": "accept"}},
    }
    waiting = asyncio.create_task(coordinator.await_round(first, original))
    await asyncio.wait_for(requested.wait(), 2)
    record = store.managed_input_store.get_round(execution_id.root, first.round_id)
    assert record is not None
    assert record.operation_parameters == {
        "target": "shipping/book",
        "arguments": {"weight": 1},
        "meta": {"request_id": "same"},
        "task": {"id": "task-same"},
        "session_id": "session-1",
        "turn_id": "turn-1",
        "logical_operation_id": "operation-1",
        "operation_kind": "tool",
        "operation_name": "book",
    }
    respond_elicitation(
        store,
        execution_id,
        first.round_id,
        {"address": ElicitationResponse(action="decline")},
        idempotency_key="parameter-response-1",
    )
    coordinator.notify_response(first.round_id)
    await waiting
    await coordinator.resolve_round(first.round_id)

    requested.clear()
    second = first.model_copy(update={"round_id": "parameter-round-2"})
    second_waiting = asyncio.create_task(
        coordinator.await_round(
            second,
            {
                "target": "shipping/book",
                "arguments": {"weight": 1},
                "meta": {"request_id": "same"},
                "task": {"id": "task-same"},
                "session_id": "session-1",
                "turn_id": "turn-1",
                "requestState": "round-two",
                "inputResponses": {"address": {"action": "decline"}},
            },
        )
    )
    await asyncio.wait_for(requested.wait(), 2)
    respond_elicitation(
        store,
        execution_id,
        second.round_id,
        {"address": ElicitationResponse(action="decline")},
        idempotency_key="parameter-response-2",
    )
    coordinator.notify_response(second.round_id)
    await second_waiting
    await coordinator.resolve_round(second.round_id)

    requested.clear()
    third = first.model_copy(update={"round_id": "parameter-round-3"})
    with pytest.raises(ManagedRuntimeMismatch, match="operation identity"):
        await coordinator.await_round(
            third,
            {
                "target": "shipping/book",
                "arguments": {"weight": 2},
                "meta": {"request_id": "same"},
                "task": {"id": "task-same"},
                "session_id": "session-1",
                "turn_id": "turn-1",
            },
        )
    assert (
        store.managed_input_store.get_round(execution_id.root, third.round_id) is None
    )
    unsubscribe()


@pytest.mark.asyncio
async def test_ten_rounds_are_allowed_but_eleventh_is_rejected(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "ten-rounds.sqlite")
    execution_id = ExecutionId("execution-ten-rounds")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    for index in range(10):
        pending = _pending(execution_id.root, f"round-{index + 1}")
        requested.clear()
        waiter = asyncio.create_task(coordinator.await_round(pending))
        await asyncio.wait_for(requested.wait(), 2)
        respond_elicitation(
            store,
            execution_id,
            pending.round_id,
            {"address": ElicitationResponse(action="decline")},
            idempotency_key=f"ten-round-response-{index}",
        )
        coordinator.notify_response(pending.round_id)
        await waiter
        await coordinator.resolve_round(pending.round_id, operation_complete=True)
    unsubscribe()
    with pytest.raises(ElicitationRoundLimitError):
        await coordinator.await_round(_pending(execution_id.root, "round-11"))
    assert store.managed_input_store.get_round(execution_id.root, "round-11") is None


@pytest.mark.asyncio
async def test_concurrent_active_operation_is_rejected(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "concurrent-operation.sqlite")
    execution_id = ExecutionId("execution-concurrent-operation")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    first = _pending(execution_id.root, "round-1")
    second = first.model_copy(
        update={"round_id": "round-2", "logical_operation_id": "operation-2"}
    )
    requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(requested.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    first_waiter = asyncio.create_task(coordinator.await_round(first))
    await asyncio.wait_for(requested.wait(), 2)
    with pytest.raises(ManagedRuntimeMismatch, match="more than one"):
        await coordinator.await_round(second)
    respond_elicitation(
        store,
        execution_id,
        first.round_id,
        {"address": ElicitationResponse(action="decline")},
        idempotency_key="concurrent-operation-response",
    )
    coordinator.notify_response(first.round_id)
    await first_waiter
    await coordinator.resolve_round(first.round_id, operation_complete=True)
    unsubscribe()


@pytest.mark.asyncio
async def test_waiting_requires_a_running_turn(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "illegal-lifecycle.sqlite")
    execution_id = ExecutionId("execution-illegal-lifecycle")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    coordinator = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    coordinator.bind_session("session-1")
    coordinator.bind_turn("session-1", "turn-1")
    with pytest.raises(ManagedRuntimeStateError, match="active running turn"):
        await coordinator.await_round(_pending(execution_id.root))
    record = store.managed_input_store.get_round(execution_id.root, "round-1")
    assert record is not None and record.status == "failed"
