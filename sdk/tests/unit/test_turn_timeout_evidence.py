"""Timed-out and cancelled turns keep the observations seen before the deadline."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from m3._types.specs import AgentSpec
from m3.agent_session import AdapterTurn, AsyncAgentSession
from m3.harness._rpc_native import NativeRPCAdapter
from m3.harness.claude import ClaudeCodeHarnessAdapter
from m3.harness.contracts import HarnessTurnRequest
from m3.harness.observations import (
    HarnessObservation,
    MessageChunkObservation,
    ToolCallObservedObservation,
    TurnEvidence,
    UsageObservedObservation,
)
from m3.types import (
    ACPAgent,
    ErrorCode,
    ErrorInfo,
    ExecutionOutcome,
    ServerBinding,
    StdioServer,
    TurnOutcome,
)

FRAMES: tuple[Mapping[str, Any], ...] = (
    {"type": "message", "text": "Looking at the repo..."},
    {"type": "tool", "server": "memory", "tool": "read", "call_id": "c1"},
    {"type": "usage", "input_tokens": 1200, "output_tokens": 80},
)


def _spec() -> AgentSpec:
    return AgentSpec(
        servers=(ServerBinding(server=StdioServer(name="memory", command="echo")),),
        harness=ACPAgent(model="fixture"),
    )


class _NativeSession:
    """Stand-in for NativeRPCSession and its process, with no child process."""

    owner = None

    def __init__(self, adapter: NativeRPCAdapter) -> None:
        self._adapter = adapter
        self._turns = 0

    async def send(self, request: HarnessTurnRequest) -> Any:
        self._turns += 1
        return await self._adapter._send(request, self._turns, self)  # type: ignore[arg-type]

    async def cancel(self) -> None:
        pass

    async def close(self) -> None:
        pass


class _SlowNative(NativeRPCAdapter):
    """Streams a few frames, then waits on a long model call that never ends."""

    harness_kind = "fake-native"

    def __init__(self) -> None:
        super().__init__(executable="fixture")
        self._frames = list(FRAMES)

    async def preflight(self, launch: Any) -> Any:
        return SimpleNamespace(ready=True)

    async def open(self, launch: Any) -> None:
        self._session = _NativeSession(self)  # type: ignore[assignment]

    async def send_turn(self, process: Any, request: Any, sequence: int) -> None:
        await asyncio.sleep(0)

    async def next_frame(
        self, process: Any, timeout: float | None
    ) -> Mapping[str, Any] | None:
        if self._frames:
            await asyncio.sleep(0.02)
            return self._frames.pop(0)
        await asyncio.wait_for(asyncio.Event().wait(), timeout)
        raise AssertionError("unreachable")

    async def _cancel(self, process: Any) -> None:
        self._cancel_requested = True

    def consume_frame(
        self,
        frame: Mapping[str, Any],
        sequence: int,
        wall: datetime,
        started: float,
        observations: list[HarnessObservation],
    ) -> tuple[bool, str, list[Mapping[str, Any]]]:
        common = {
            "observation_id": f"fake-{sequence}-{len(observations)}",
            "harness_kind": self.name,
            "turn_sequence": sequence,
            "wall_time": wall,
            "monotonic_offset_ms": max(0.0, (time.monotonic() - started) * 1000),
        }
        if frame["type"] == "message":
            observations.append(
                MessageChunkObservation(**common, text=frame["text"], complete=True)
            )
        elif frame["type"] == "tool":
            observations.append(
                ToolCallObservedObservation(
                    **common,
                    server=frame["server"],
                    tool=frame["tool"],
                    call_id=frame["call_id"],
                    arguments={"key": "x"},
                )
            )
        else:
            observations.append(
                UsageObservedObservation(
                    **common,
                    input_tokens=frame["input_tokens"],
                    output_tokens=frame["output_tokens"],
                )
            )
        return False, "", []


def _usage_payloads(session: AsyncAgentSession) -> list[Mapping[str, Any]]:
    trace = session.result.trace
    assert trace is not None
    return [
        event.payload
        for event in trace.events
        if event.payload.get("category") == "usage"
    ]


def _assistant_text(session: AsyncAgentSession) -> list[str]:
    trace = session.result.trace
    assert trace is not None
    return [
        block.text  # type: ignore[union-attr]
        for message in trace.view().messages
        if message.role.value == "assistant"
        for block in message.content
    ]


def _assert_partial_trace(session: AsyncAgentSession) -> None:
    trace = session.result.trace
    assert trace is not None
    assert _assistant_text(session) == ["Looking at the repo..."]
    assert [call.tool.value for call in trace.view().tool_calls] == ["read"]
    assert "capture_incomplete" in trace.limitations
    assert [payload.get("input_tokens") for payload in _usage_payloads(session)] == [
        1200
    ]


@pytest.mark.asyncio
async def test_session_timeout_keeps_observations_seen_before_deadline() -> None:
    adapter = _SlowNative()
    async with AsyncAgentSession(_spec(), adapter) as session:
        turn = await session.send("do the task", timeout=0.3)

    assert turn.snapshot.outcome is TurnOutcome.TIMED_OUT
    assert turn.error is not None and turn.error.code is ErrorCode.TIMEOUT
    assert session.result.snapshot.outcome is ExecutionOutcome.TIMED_OUT
    _assert_partial_trace(session)


@pytest.mark.asyncio
async def test_cancelled_turn_keeps_observations_seen_before_cancel() -> None:
    """The execution deadline cancels the turn task rather than timing it out."""

    adapter = _SlowNative()
    session = AsyncAgentSession(_spec(), adapter)
    await session.__aenter__()
    task = asyncio.create_task(session.send("do the task"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await session.aclose()

    assert session.result.snapshot.outcome is ExecutionOutcome.CANCELLED
    _assert_partial_trace(session)


@pytest.mark.asyncio
async def test_partial_evidence_does_not_leak_into_a_later_turn() -> None:
    adapter = _SlowNative()
    adapter._frames = []
    task = asyncio.create_task(
        adapter._send(HarnessTurnRequest.from_message("x"), 1, _NativeSession(adapter))  # type: ignore[arg-type]
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.take_partial_turn_evidence() is not None
    assert adapter.take_partial_turn_evidence() is None


class _AdapterOwnedDeadline:
    """Adapter enforcing its own deadline and reporting a timed-out turn."""

    supported_content_kinds = frozenset({"text"})

    async def start(self, spec: AgentSpec) -> None:
        pass

    async def cancel(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send(self, message: Any, *, timeout: Any = None, metadata: Any = None):
        started = time.monotonic()
        observation = MessageChunkObservation(
            observation_id="m1",
            harness_kind="fake",
            turn_sequence=1,
            wall_time=datetime.now(timezone.utc),
            monotonic_offset_ms=0,
            text="Looking at the repo...",
            complete=True,
        )
        return AdapterTurn(
            error=ErrorInfo(code=ErrorCode.TIMEOUT, message="harness turn timed out"),
            terminal=True,
            outcome=TurnOutcome.TIMED_OUT,
            turn_evidence=TurnEvidence(
                sequence=1,
                status="timed_out",
                observations=(observation,),
                limitations=("capture_incomplete",),
                monotonic_origin=started,
            ),
        )


@pytest.mark.asyncio
async def test_adapter_reported_timeout_finishes_execution_timed_out() -> None:
    async with AsyncAgentSession(_spec(), _AdapterOwnedDeadline()) as session:
        turn = await session.send("do the task")

    assert turn.snapshot.outcome is TurnOutcome.TIMED_OUT
    assert turn.error is not None and turn.error.code is ErrorCode.TIMEOUT
    assert session.result.snapshot.outcome is ExecutionOutcome.TIMED_OUT
    assert session.result.error is not None
    assert session.result.error.code is ErrorCode.TIMEOUT
    assert _assistant_text(session) == ["Looking at the repo..."]


class _Stdin:
    def write(self, data: bytes) -> None:
        del data

    async def drain(self) -> None:
        pass


@pytest.mark.asyncio
async def test_claude_cancelled_turn_exposes_partial_evidence() -> None:
    adapter = ClaudeCodeHarnessAdapter(executable="fixture")
    output: asyncio.Queue[Any] = asyncio.Queue()
    owner = SimpleNamespace(
        process=SimpleNamespace(stdin=_Stdin(), pid=123, returncode=None),
        stderr_task=None,
    )
    adapter._owner = owner  # type: ignore[assignment]
    adapter._output = output
    for item in (
        {"type": "system", "session_id": "s1", "model": "fixture"},
        {
            "type": "assistant",
            "message": {
                "id": "msg-1",
                "role": "assistant",
                "content": [{"type": "text", "text": "Looking at the repo..."}],
                "usage": {"input_tokens": 1200, "output_tokens": 80},
            },
        },
    ):
        output.put_nowait(item)
    task = asyncio.create_task(
        adapter._send(HarnessTurnRequest.from_message("do the task"), 1)
    )
    while not output.empty():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    evidence = adapter.take_partial_turn_evidence()
    assert evidence is not None
    kinds = {item.kind for item in evidence.observations}
    assert {"message_chunk", "usage_observed"} <= kinds
    assert "capture_incomplete" in evidence.limitations
    assert adapter.take_partial_turn_evidence() is None
