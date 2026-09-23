from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import get_ident
from typing import Any, cast

import pytest

from m3._types.specs import AgentSpec
from m3.async_api import AsyncMCPTestKit
from m3.elicitation import (
    ElicitationResponse,
    FormElicitationRequest,
    PendingElicitationRound,
    expect_form,
)
from m3.errors import (
    InvalidTransitionError,
    ManagedInputConflict,
    ManagedInputStateError,
    ManagedInputValidationError,
    ModelValidationError,
)
from m3.execution_runtime import AsyncExecutionController
from m3.managed_input_api import command_human_input
from m3.storage import InMemoryExecutionStore, SQLiteExecutionStore
from m3.sync_api import MCPTestKit
from m3.types import (
    DirectSpec,
    ExecutionId,
    ExecutionState,
    ExecutionStatus,
    OpenCode,
    Ping,
    ServerBinding,
    StdioServer,
    TextContent,
    UserMessage,
)


def _agent_spec(*, elicitation: object = None) -> AgentSpec:
    return AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="shipping", command="echo")),),
        harness=OpenCode(model="vendor/model"),
        message=UserMessage(content=(TextContent(text="book shipment"),)),
        elicitation=elicitation,
    )


def _pending(
    execution_id: str, *, round_id: str = "round-1"
) -> PendingElicitationRound:
    return PendingElicitationRound(
        round_id=round_id,
        execution_id=execution_id,
        logical_operation_id="operation-1",
        server="shipping",
        operation_kind="tool",
        operation_name="book_shipment",
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


def test_managed_policy_is_agent_only_and_rejects_predefined_plan() -> None:
    plan_kit = MCPTestKit(embedded_worker=False)
    try:
        with pytest.raises(ModelValidationError, match="predefined elicitation"):
            plan_kit.submit(
                _agent_spec(elicitation=expect_form("address").accept({})),
                human_input="managed",
            )
    finally:
        plan_kit.close()

    kit = MCPTestKit(embedded_worker=False)
    try:
        with pytest.raises(ModelValidationError, match="agent"):
            kit.submit(
                DirectSpec(
                    servers=(
                        ServerBinding(
                            server=StdioServer(name="shipping", command="echo")
                        ),
                    ),
                    operation=Ping(),
                ),
                human_input="managed",
            )
    finally:
        kit.close()


def test_old_and_new_durable_agent_payloads_have_explicit_policy(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "executions.sqlite")
    old_payload = _agent_spec().model_dump(mode="json")
    restored = AgentSpec.model_validate(old_payload)
    assert restored.model_dump(mode="json") == old_payload
    kit = MCPTestKit(store=store, embedded_worker=False)
    try:
        handle = kit.submit(_agent_spec(), human_input="managed")
        command = store.get_command(f"command-{handle.execution_id.root}")
        assert command is not None
        assert command.payload["human_input"] == "managed"
        assert old_payload.get("human_input", "fail") == "fail"
        assert command_human_input({"spec": old_payload}) == "fail"
    finally:
        kit.close()


def test_sync_submit_rejects_managed_input_without_persistent_store() -> None:
    kit = MCPTestKit(store=InMemoryExecutionStore(), embedded_worker=False)
    try:
        with pytest.raises(ModelValidationError, match="persistent"):
            kit.submit(_agent_spec(), human_input="managed")
    finally:
        kit.close()


def test_sync_handle_projects_pending_round_and_commits_keyed_response(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "executions.sqlite")
    kit = MCPTestKit(store=store, embedded_worker=False)
    try:
        spec = _agent_spec()
        handle = kit.submit(spec, human_input="managed")
        managed = store.managed_input_store
        managed.create_round(
            _pending(handle.execution_id.root),
            round_index=0,
            round_limit=10,
            owner_id="worker",
            lease_seconds=30,
        )
        projected = handle.pending_elicitation()
        persisted = managed.get_round(handle.execution_id.root, "round-1")
        assert projected is not None and persisted is not None
        assert projected.model_dump(mode="json") == persisted.pending.model_dump(
            mode="json"
        )
        handle.respond_elicitation(
            "round-1",
            {"address": ElicitationResponse(action="accept", content={"city": "Pune"})},
            idempotency_key="response-1",
        )
        assert (
            managed.get_round(handle.execution_id.root, "round-1").status
            == "response_validated"
        )
        handle.respond_elicitation(
            "round-1",
            {"address": ElicitationResponse(action="accept", content={"city": "Pune"})},
            idempotency_key="response-1",
        )
        with pytest.raises(ManagedInputConflict):
            handle.respond_elicitation(
                "round-1",
                {
                    "address": ElicitationResponse(
                        action="accept", content={"city": "Delhi"}
                    )
                },
                idempotency_key="response-1",
            )
        assert handle.pending_elicitation() is None
    finally:
        kit.close()


def test_sync_public_response_rejects_stale_round_and_failed_round(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "stale-response.sqlite")
    kit = MCPTestKit(store=store, embedded_worker=False)
    try:
        handle = kit.submit(_agent_spec(), human_input="managed")
        managed = store.managed_input_store
        record = managed.create_round(
            _pending(handle.execution_id.root),
            round_index=0,
            round_limit=10,
            owner_id="worker",
            lease_seconds=30,
        )
        with pytest.raises(ManagedInputValidationError, match="does not exist"):
            handle.respond_elicitation(
                "stale-round",
                {"address": ElicitationResponse(action="decline")},
                idempotency_key="stale-round-response",
            )

        managed.fail(
            handle.execution_id.root,
            record.round_id,
            owner_id=record.owner_id,
            lease_token=record.lease_token,
            code="managed_input_error",
            message="worker interaction failed",
        )
        with pytest.raises(ManagedInputStateError, match="cannot accept responses"):
            handle.respond_elicitation(
                record.round_id,
                {"address": ElicitationResponse(action="decline")},
                idempotency_key="late-response",
            )
    finally:
        kit.close()


@pytest.mark.asyncio
async def test_async_controller_requires_persistent_managed_store() -> None:
    class _Kit:
        _record_checks = False

    controller = AsyncExecutionController(_Kit(), store=None, worker=False)
    with pytest.raises(ModelValidationError, match="persistent"):
        controller.submit(_agent_spec(), human_input="managed")
    with pytest.raises(ModelValidationError, match=r"fail.*managed"):
        controller.submit(_agent_spec(), human_input=cast(Any, "invalid"))


def test_multiple_pending_rounds_are_a_typed_state_error(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "executions.sqlite")
    kit = MCPTestKit(store=store, embedded_worker=False)
    try:
        handle = kit.submit(_agent_spec(), human_input="managed")
        managed = store.managed_input_store
        managed.create_round(
            _pending(handle.execution_id.root),
            round_index=0,
            round_limit=10,
            owner_id="worker",
            lease_seconds=30,
        )
        managed.create_round(
            _pending(handle.execution_id.root, round_id="round-2"),
            round_index=1,
            round_limit=10,
            owner_id="worker",
            lease_seconds=30,
        )
        with pytest.raises(ManagedInputStateError, match="multiple"):
            handle.pending_elicitation()
    finally:
        kit.close()


def test_waiting_for_input_lifecycle_has_only_approved_outbound_transitions() -> None:
    state = ExecutionState(
        execution_id=ExecutionId("execution-waiting"),
        lifecycle=ExecutionStatus.RUNNING_TURN,
    ).transition(ExecutionStatus.WAITING_FOR_INPUT)
    assert state.lifecycle is ExecutionStatus.WAITING_FOR_INPUT
    with pytest.raises(InvalidTransitionError):
        state.transition(ExecutionStatus.IDLE)
    assert (
        state.transition(ExecutionStatus.RUNNING_TURN).lifecycle
        is ExecutionStatus.RUNNING_TURN
    )
    assert (
        state.transition(ExecutionStatus.CLOSING).lifecycle is ExecutionStatus.CLOSING
    )


@pytest.mark.asyncio
async def test_async_handle_has_the_same_public_managed_input_contract(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "executions.sqlite")
    kit = AsyncMCPTestKit(store=store, embedded_worker=False)
    try:
        handle = kit.submit(_agent_spec(), human_input="managed")
        store.managed_input_store.create_round(
            _pending(handle.execution_id.root),
            round_index=0,
            round_limit=10,
            owner_id="worker",
            lease_seconds=30,
        )
        projected = await handle.pending_elicitation()
        assert projected is not None
        assert not hasattr(projected, "lease_token")
        await handle.respond_elicitation(
            "round-1",
            {"address": ElicitationResponse(action="decline")},
            idempotency_key="response-async-1",
        )
        assert await handle.pending_elicitation() is None
    finally:
        await kit.aclose()


@pytest.mark.asyncio
async def test_async_public_response_validation_is_atomic_and_idempotent(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "async-response-validation.sqlite")
    kit = AsyncMCPTestKit(store=store, embedded_worker=False)
    try:
        handle = kit.submit(_agent_spec(), human_input="managed")
        store.managed_input_store.create_round(
            _pending(handle.execution_id.root),
            round_index=0,
            round_limit=10,
            owner_id="worker",
            lease_seconds=30,
        )

        with pytest.raises(ManagedInputValidationError, match="schema"):
            await handle.respond_elicitation(
                "round-1",
                {"address": ElicitationResponse(action="accept", content={"city": 42})},
                idempotency_key="invalid-response",
            )
        assert await handle.pending_elicitation() is not None

        accepted = {"address": ElicitationResponse(action="decline")}
        await handle.respond_elicitation(
            "round-1", accepted, idempotency_key="response-async"
        )
        await handle.respond_elicitation(
            "round-1", accepted, idempotency_key="response-async"
        )
        with pytest.raises(ManagedInputConflict, match="idempotency"):
            await handle.respond_elicitation(
                "round-1",
                {"address": ElicitationResponse(action="cancel")},
                idempotency_key="response-async",
            )
        assert await handle.pending_elicitation() is None
    finally:
        await kit.aclose()


@pytest.mark.asyncio
async def test_async_managed_store_calls_run_off_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteExecutionStore(tmp_path / "executions.sqlite")
    kit = AsyncMCPTestKit(store=store, embedded_worker=False)
    try:
        handle = kit.submit(_agent_spec(), human_input="managed")
        store.managed_input_store.create_round(
            _pending(handle.execution_id.root),
            round_index=0,
            round_limit=10,
            owner_id="worker",
            lease_seconds=30,
        )
        event_loop_thread = get_ident()
        observed: list[int] = []

        def read_pending(store_value: object, execution_id: object) -> object:
            del store_value, execution_id
            observed.append(get_ident())
            return _pending(handle.execution_id.root)

        monkeypatch.setattr("m3.execution_runtime._pending_elicitation", read_pending)
        await handle.pending_elicitation()
        assert observed and observed[0] != event_loop_thread

        def commit_response(*args: object, **kwargs: object) -> None:
            del args, kwargs
            observed.append(get_ident())

        monkeypatch.setattr(
            "m3.execution_runtime._respond_elicitation", commit_response
        )
        await handle.respond_elicitation(
            "round-1",
            {"address": ElicitationResponse(action="decline")},
            idempotency_key="response-off-loop",
        )
        assert observed[-1] != event_loop_thread
    finally:
        await kit.aclose()


@pytest.mark.asyncio
async def test_async_managed_submit_does_not_initialize_lazy_store(
    tmp_path: Path,
) -> None:
    database = tmp_path / "executions.sqlite"
    store = SQLiteExecutionStore(database)
    kit = AsyncMCPTestKit(store=store, embedded_worker=False)

    def has_managed_tables() -> bool:
        with sqlite3.connect(database) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    ("m3_managed_input_rounds",),
                ).fetchone()
                is not None
            )

    try:
        handle = kit.submit(_agent_spec(), human_input="managed")
        assert not has_managed_tables()
        assert await handle.pending_elicitation() is None
        assert has_managed_tables()
    finally:
        await kit.aclose()
