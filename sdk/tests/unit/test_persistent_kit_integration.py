from __future__ import annotations

import asyncio

from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal.sync_api import MCPTestKit
from mcp_pal.testing import FaultInjector
from mcp_pal.types import DirectSpec, ExecutionOutcome, Ping, PingResult, ServerBinding


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
            assert store.get_snapshot(handle.execution_id).outcome is ExecutionOutcome.COMPLETED
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
            assert second_store.get_snapshot(handle.execution_id).outcome is ExecutionOutcome.COMPLETED
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
        assert store.get_snapshot(handle.execution_id).outcome is ExecutionOutcome.COMPLETED
