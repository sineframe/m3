from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

import pytest

from m3._types.specs import AgentSpec
from m3.agent_session import AdapterTurn, HarnessAdapter
from m3.async_api import AsyncMCPTestKit
from m3.harness import HarnessAdapterRegistry
from m3.storage import SQLiteExecutionStore
from m3.sync_api import MCPTestKit
from m3.testing import FaultInjector
from m3.types import (
    ACPAgent,
    DirectSpec,
    ExecutionOutcome,
    Ping,
    PingResult,
    ServerBinding,
    StdioServer,
    TextContent,
    TurnResponse,
    UserMessage,
)

pytestmark = pytest.mark.process_lifecycle


def _spec() -> DirectSpec:
    return DirectSpec(
        servers=(ServerBinding(server=FaultInjector().stdio_server()),),
        operation=Ping(),
    )


def test_async_persistent_kit_submits_through_owned_worker(tmp_path):
    async def run() -> None:
        store = SQLiteExecutionStore(tmp_path / "async.sqlite")
        async with AsyncMCPTestKit(store=store) as kit:
            handle = kit.submit(_spec())
            assert (await handle.snapshot()).lifecycle.value == "queued"
            result = await handle.result(timeout=10)
            assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
            assert isinstance(result.direct_result, PingResult)
            assert result.direct_result.raw is not None
            assert (
                store.get_snapshot(handle.execution_id).outcome
                is ExecutionOutcome.COMPLETED
            )
            reopened = SQLiteExecutionStore(tmp_path / "async.sqlite")
            terminal = tuple(reopened.iter_events(handle.execution_id))[-1]
            assert terminal.payload["direct_result"]["kind"] == "ping"

    asyncio.run(run())


def test_persistent_worker_can_be_owned_by_a_second_toolkit(tmp_path):
    async def run() -> None:
        path = tmp_path / "shared.sqlite"
        first_store = SQLiteExecutionStore(path)
        second_store = SQLiteExecutionStore(path)
        first = AsyncMCPTestKit(store=first_store, embedded_worker=False)
        second = AsyncMCPTestKit(store=second_store)
        try:
            handle = first.submit(_spec())
            result = await handle.result(timeout=10)
            assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
            assert (
                second_store.get_snapshot(handle.execution_id).outcome
                is ExecutionOutcome.COMPLETED
            )
        finally:
            await first.aclose()
            await second.aclose()

    asyncio.run(run())


def test_async_kit_constructed_outside_loop_starts_worker_on_enter(tmp_path):
    path = tmp_path / "entered.sqlite"
    # The second kit is deliberately constructed before asyncio.run(); its
    # worker must bind to the loop at __aenter__, not only after local submit.
    consumer = AsyncMCPTestKit(store=SQLiteExecutionStore(path))

    async def run() -> None:
        producer = SQLiteExecutionStore(path)
        source = AsyncMCPTestKit(store=producer, embedded_worker=False)
        handle = source.submit(_spec())
        try:
            async with consumer:
                result = await handle.result(timeout=10)
                assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
        finally:
            await source.aclose()

    asyncio.run(run())


def test_sync_persistent_kit_has_the_same_worker_contract(tmp_path):
    store = SQLiteExecutionStore(tmp_path / "sync.sqlite")
    with MCPTestKit(store=store) as kit:
        handle = kit.submit(_spec())
        result = handle.result()
        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert (
            store.get_snapshot(handle.execution_id).outcome
            is ExecutionOutcome.COMPLETED
        )


class _SlowHarness(HarnessAdapter):
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def start(self, _spec: AgentSpec) -> None:
        return None

    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse | AdapterTurn:
        del message, timeout, metadata
        await asyncio.sleep(1)
        self._calls.append("send")
        return TurnResponse(content=(TextContent(text="ok"),))

    async def close(self) -> None:
        return None


def _agent_spec() -> AgentSpec:
    return AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ACPAgent(model="fixture"),
        message=UserMessage(content=(TextContent(text="hello"),)),
    )


def test_private_queue_kits_run_concurrently_and_only_their_own_work(tmp_path):
    async def run() -> tuple[list[ExecutionOutcome], float]:
        path = tmp_path / "private.sqlite"
        calls_a: list[str] = []
        calls_b: list[str] = []
        kits = []
        for queue, calls in (("pytest-a", calls_a), ("pytest-b", calls_b)):
            registry = HarnessAdapterRegistry(
                {"acp": lambda _harness, calls=calls: _SlowHarness(calls)}
            )
            kits.append(
                AsyncMCPTestKit(
                    env={},
                    cwd="/tmp/m3-no-project",
                    adapter_registry=registry,
                    store=SQLiteExecutionStore(path, execution_queue=queue),
                )
            )
        try:
            started = time.monotonic()
            handles = [kit.submit(_agent_spec()) for kit in kits for _ in range(4)]
            results = await asyncio.gather(*(h.result(timeout=30) for h in handles))
            elapsed = time.monotonic() - started
        finally:
            for kit in kits:
                await kit.aclose()
        assert len(calls_a) == 4
        assert len(calls_b) == 4
        return [r.snapshot.outcome for r in results], elapsed

    outcomes, elapsed = asyncio.run(run())
    assert outcomes == [ExecutionOutcome.COMPLETED] * 8
    assert elapsed < 3
