from __future__ import annotations

import asyncio
import threading
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


class _Rendezvous:
    """Releases waiting turns only once ``expected`` turns are in flight at once."""

    def __init__(self, expected: int) -> None:
        self._expected = expected
        self._arrived = 0
        self._all_arrived = asyncio.Event()

    async def wait(self) -> None:
        self._arrived += 1
        if self._arrived == self._expected:
            self._all_arrived.set()
        await asyncio.wait_for(self._all_arrived.wait(), timeout=20)


class _RendezvousHarness(HarnessAdapter):
    def __init__(self, calls: list[str], rendezvous: _Rendezvous) -> None:
        self._calls = calls
        self._rendezvous = rendezvous

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
        await self._rendezvous.wait()
        self._calls.append("send")
        return TurnResponse(content=(TextContent(text="ok"),))

    async def close(self) -> None:
        return None


def _agent_spec(text: str = "hello") -> AgentSpec:
    return AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="unused", command="echo")),),
        harness=ACPAgent(model="fixture"),
        message=UserMessage(content=(TextContent(text=text),)),
    )


def test_private_queue_kits_run_concurrently_and_only_their_own_work(tmp_path):
    async def run() -> list[ExecutionOutcome]:
        path = tmp_path / "private.sqlite"
        calls_a: list[str] = []
        calls_b: list[str] = []
        # Every turn blocks until all eight are in flight, so this only
        # completes if both kits run all four of their executions at once.
        # A wall-clock bound would also measure per-event SQLite commits.
        rendezvous = _Rendezvous(expected=8)
        kits = []
        for queue, calls in (("pytest-a", calls_a), ("pytest-b", calls_b)):
            registry = HarnessAdapterRegistry(
                {
                    "acp": lambda _harness, calls=calls: _RendezvousHarness(
                        calls, rendezvous
                    )
                }
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
            handles = [kit.submit(_agent_spec()) for kit in kits for _ in range(4)]
            results = await asyncio.gather(*(h.result(timeout=30) for h in handles))
        finally:
            for kit in kits:
                await kit.aclose()
        assert len(calls_a) == 4
        assert len(calls_b) == 4
        return [r.snapshot.outcome for r in results]

    outcomes = asyncio.run(run())
    assert outcomes == [ExecutionOutcome.COMPLETED] * 8


class _GatedHarness(HarnessAdapter):
    """Blocks turns whose message is "block" until the test releases them."""

    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self._started = started
        self._release = release

    async def start(self, _spec: AgentSpec) -> None:
        return None

    async def send(
        self,
        message: UserMessage,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TurnResponse | AdapterTurn:
        del timeout, metadata
        if message.content[0].text == "block":
            self._started.set()
            await asyncio.to_thread(self._release.wait, 30)
        return TurnResponse(content=(TextContent(text="ok"),))

    async def close(self) -> None:
        return None


def test_submission_racing_idle_worker_exit_is_still_claimed(tmp_path, monkeypatch):
    from m3.services.persistent import SQLiteStoreWorker

    long_started = threading.Event()
    release_long = threading.Event()
    armed = threading.Event()
    idle_paused = threading.Event()
    resume_idle = threading.Event()
    original_run_once = SQLiteStoreWorker.run_once

    def run_once(self):
        claimed = original_run_once(self)
        # Armed only once the primary worker is busy with the long execution,
        # so the first empty claim after that comes from the extra worker that
        # ran the short one. Hold it between its empty claim and its exit
        # decision.
        if not claimed and armed.is_set() and not idle_paused.is_set():
            idle_paused.set()
            resume_idle.wait(30)
        return claimed

    monkeypatch.setattr(SQLiteStoreWorker, "run_once", run_once)

    async def run() -> ExecutionOutcome:
        kit = AsyncMCPTestKit(
            env={},
            cwd="/tmp/m3-no-project",
            adapter_registry=HarnessAdapterRegistry(
                {"acp": lambda _h: _GatedHarness(long_started, release_long)}
            ),
            store=SQLiteExecutionStore(
                tmp_path / "race.sqlite", execution_queue="pytest-race"
            ),
        )
        try:
            long = kit.submit(_agent_spec("block"))
            assert await asyncio.to_thread(long_started.wait, 10)
            armed.set()
            await kit.submit(_agent_spec()).result(timeout=10)
            assert await asyncio.to_thread(idle_paused.wait, 10)
            # Lands while the idle extra worker is still counted as alive.
            late = kit.submit(_agent_spec())
            resume_idle.set()
            result = await late.result(timeout=10)
            release_long.set()
            await long.result(timeout=10)
            return result.snapshot.outcome
        finally:
            resume_idle.set()
            release_long.set()
            await kit.aclose()

    assert asyncio.run(run()) is ExecutionOutcome.COMPLETED
