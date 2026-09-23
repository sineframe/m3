from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from m3.agent_session import AdapterTurn
from m3.execution_trace import ExecutionTraceRecorder
from m3.harness._rpc_native import NativeRPCAdapter
from m3.harness.contracts import HarnessStartupError
from m3.harness.pi import PiHarnessAdapter
from m3.harness.pi_control import (
    CONTROL_PROTOCOL_VERSION,
    MAX_CONTROL_FRAME_BYTES,
    PiControlChannel,
    PiControlClosed,
    PiControlProtocolError,
    PiControlScope,
    validate_control_envelope,
)
from m3.harness.pi_extension.bridge import BridgeActionStatus
from m3.managed_runtime import _ManagedInputCoordinator
from m3.storage import SQLiteExecutionStore
from m3.types import EventKind, ExecutionId


async def _connect(
    channel: PiControlChannel,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection(channel.host, channel.port)
    writer.write(
        (
            json.dumps(
                {
                    "type": "hello",
                    "version": CONTROL_PROTOCOL_VERSION,
                    "session_id": channel.session_id,
                    "token": channel.token,
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
    )
    await writer.drain()
    hello = json.loads((await reader.readline()).decode())
    assert hello == {
        "type": "hello",
        "version": CONTROL_PROTOCOL_VERSION,
        "session_id": channel.session_id,
        "accepted": True,
    }
    await channel.wait_connected()
    return reader, writer


def _pending(
    channel: PiControlChannel,
    *,
    message_id: str = "pending-1",
    generation: str = "g1",
    turn_sequence: int = 2,
    logical_operation_id: str = "op-1",
    round_id: str = "r1",
    round_index: int = 0,
) -> dict[str, object]:
    return {
        "type": "pending",
        "session_id": channel.session_id,
        "message_id": message_id,
        "generation": generation,
        "turn_sequence": turn_sequence,
        "logical_operation_id": logical_operation_id,
        "round_id": round_id,
        "execution_id": "execution-1",
        "round_index": round_index,
        "round_limit": 4,
        "server": "orders",
        "operation_kind": "tool",
        "operation_name": "book",
        "request_state": "opaque-state",
        "requests": {"address": {"mode": "form", "request_key": "address"}},
        "created_at": "2026-09-22T10:00:00+00:00",
        "deadline": "2026-09-22T11:00:00+00:00",
        "operation_parameters": {"weight_kg": 2},
    }


def _terminal(
    channel: PiControlChannel, pending: dict[str, object], message_id: str
) -> dict[str, object]:
    return {
        "type": "terminal",
        "session_id": channel.session_id,
        "message_id": message_id,
        "generation": pending["generation"],
        "turn_sequence": pending["turn_sequence"],
        "execution_id": pending["execution_id"],
        "logical_operation_id": pending["logical_operation_id"],
        "round_id": pending["round_id"],
        "state": "delivered",
    }


def test_envelope_validation_rejects_unknown_fields_and_stale_scope() -> None:
    with pytest.raises(PiControlProtocolError):
        validate_control_envelope(
            {
                "type": "cancel",
                "session_id": "s",
                "message_id": "m",
                "generation": "g",
                "turn_sequence": 1,
                "logical_operation_id": "op-1",
                "round_id": "r",
                "reason": "stop",
                "unexpected": True,
            },
            session_id="s",
        )
    with pytest.raises(PiControlProtocolError):
        validate_control_envelope(
            {
                "type": "cancel",
                "session_id": "s",
                "message_id": "m",
                "generation": "old",
                "turn_sequence": 1,
                "round_id": "r",
                "reason": "stop",
            },
            session_id="s",
            scope=PiControlScope("current", 1, "execution-1", "op-1", "r"),
        )
    frames = {
        "pending": {
            "execution_id": "execution-1",
            "round_index": 0,
            "round_limit": 4,
            "logical_operation_id": "op-1",
            "server": "orders",
            "operation_kind": "tool",
            "operation_name": "book",
            "request_state": "opaque-state",
            "requests": {"address": {"mode": "form", "request_key": "address"}},
            "created_at": "2026-09-22T10:00:00+00:00",
            "deadline": "2026-09-22T11:00:00+00:00",
            "operation_parameters": {"weight_kg": 2},
        },
        "response": {"responses": {}},
        "cancel": {"reason": "stop"},
        "terminal": {"state": "delivered"},
    }
    common = {
        "session_id": "s",
        "message_id": "m",
        "generation": "g",
        "turn_sequence": 1,
        "execution_id": "execution-1",
        "logical_operation_id": "op-1",
        "round_id": "r",
    }
    for frame_type, fields in frames.items():
        for missing in fields:
            candidate = {"type": frame_type, **common, **fields}
            del candidate[missing]
            with pytest.raises(PiControlProtocolError):
                validate_control_envelope(candidate, session_id="s")
    for candidate in (
        {"type": "hello", "version": 1, "session_id": "s"},
        {"type": "close", "session_id": "s", "message_id": "m"},
    ):
        with pytest.raises(PiControlProtocolError):
            validate_control_envelope(
                candidate, session_id="s", hello=candidate["type"] == "hello"
            )
    with pytest.raises(PiControlProtocolError):
        validate_control_envelope(
            {
                "type": "pending",
                **common,
                "execution_id": "execution-1",
                "round_index": 0,
                "round_limit": 4,
                "server": "orders",
                "operation_kind": "tool",
                "operation_name": "book",
                "request_state": "opaque-state",
                "requests": {"address": {"mode": "form", "request_key": "address"}},
                "created_at": "2026-09-22T10:00:00+00:00",
                "deadline": None,
                "operation_parameters": {},
            },
            session_id="s",
            direction="parent_to_extension",
        )
    for frame_type, fields in {
        "response": {"responses": {}},
        "cancel": {"reason": "stop"},
        "terminal": {"state": "delivered"},
    }.items():
        with pytest.raises(PiControlProtocolError):
            validate_control_envelope(
                {"type": frame_type, **common, **fields}, session_id="s"
            )


def test_terminal_delivery_requires_response_acceptance() -> None:
    async def scenario() -> None:
        channel = PiControlChannel()
        await channel.start()
        _reader, writer = await _connect(channel)
        writer.write((json.dumps(_pending(channel)) + "\n").encode())
        await writer.drain()
        assert (await channel.receive())["type"] == "pending"
        terminal = {
            "type": "terminal",
            "session_id": channel.session_id,
            "message_id": "terminal-before-response",
            "generation": "g1",
            "turn_sequence": 2,
            "execution_id": "execution-1",
            "logical_operation_id": "op-1",
            "round_id": "r1",
            "state": "delivered",
        }
        writer.write((json.dumps(terminal) + "\n").encode())
        await writer.drain()
        with pytest.raises(PiControlClosed):
            await channel.receive(timeout=1)
        writer.close()
        await writer.wait_closed()
        await channel.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_source", ["pi_exit", "control_disconnect"])
@pytest.mark.asyncio
async def test_pi_managed_pending_round_fails_when_peer_exits(
    failure_source: str,
) -> None:
    class ManagedRuntime:
        execution_id = "execution-1"

        def __init__(self) -> None:
            self.waiting = asyncio.Event()
            self.failed = asyncio.Event()
            self.failure: BaseException | None = None
            self.never = asyncio.Event()

        async def await_round(self, *_args: object) -> dict[str, object]:
            self.waiting.set()
            await self.never.wait()
            return {}

        async def fail_round(self, error: BaseException) -> None:
            self.failure = error
            self.failed.set()

    class Process:
        def __init__(self) -> None:
            self.exited = asyncio.Event()
            self.returncode: int | None = None

        async def wait(self) -> int:
            await self.exited.wait()
            self.returncode = 7
            return self.returncode

    adapter = PiHarnessAdapter(executable="pi")
    channel = PiControlChannel()
    await channel.start()
    _reader, writer = await _connect(channel)
    runtime = ManagedRuntime()
    process = Process()
    adapter._control_channel = channel
    adapter._managed_input_runtime = runtime
    adapter._action_generation = "g1"
    adapter._session = SimpleNamespace(turn_count=2)  # type: ignore[assignment]
    adapter._process = SimpleNamespace(  # type: ignore[assignment]
        owner=SimpleNamespace(process=process)
    )
    pending = _pending(channel)
    pending["requests"] = {
        "address": {
            "mode": "form",
            "request_key": "address",
            "message": "Enter an address",
            "requested_schema": {"type": "object"},
        }
    }
    channel.set_scope(PiControlScope("g1", 2, "execution-1", "op-1", "r1"))
    delivery = asyncio.create_task(adapter._deliver_control_pending(pending))
    try:
        await asyncio.wait_for(runtime.waiting.wait(), timeout=1)
        if failure_source == "pi_exit":
            process.exited.set()
        else:
            writer.close()
            await writer.wait_closed()
        with pytest.raises(HarnessStartupError):
            await asyncio.wait_for(delivery, timeout=1)
        assert runtime.failed.is_set()
        assert runtime.failure is not None
    finally:
        if not delivery.done():
            delivery.cancel()
        await asyncio.gather(delivery, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await channel.close()


@pytest.mark.asyncio
async def test_pi_control_disconnect_fails_durable_managed_round(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "pi-managed-input.sqlite")
    execution_id = ExecutionId("execution-pi-liveness")
    recorder = ExecutionTraceRecorder(store, execution_id)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "starting"})
    recorder.emit(
        EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "running_turn"}
    )
    runtime = _ManagedInputCoordinator(
        store.managed_input_store, execution_id.root, recorder
    )
    runtime.bind_session("session-1")
    runtime.bind_turn("session-1", "turn-2")

    adapter = PiHarnessAdapter(executable="pi")
    channel = PiControlChannel()
    await channel.start()
    _reader, writer = await _connect(channel)
    adapter._control_channel = channel
    adapter._managed_input_runtime = runtime
    adapter._action_generation = "g1"
    adapter._session = SimpleNamespace(turn_count=2)  # type: ignore[assignment]
    pending = _pending(channel)
    pending["execution_id"] = execution_id.root
    pending["deadline"] = None
    pending["requests"] = {
        "address": {
            "mode": "form",
            "request_key": "address",
            "message": "Enter an address",
            "requested_schema": {"type": "object"},
        }
    }
    channel.set_scope(PiControlScope("g1", 2, execution_id.root, "op-1", "r1"))

    request_persisted = asyncio.Event()
    loop = asyncio.get_running_loop()
    unsubscribe = store.subscribe(
        execution_id,
        lambda event: (
            loop.call_soon_threadsafe(request_persisted.set)
            if event.kind is EventKind.ELICITATION_REQUEST
            else None
        ),
    )
    delivery = asyncio.create_task(adapter._deliver_control_pending(pending))
    try:
        await asyncio.wait_for(request_persisted.wait(), timeout=2)
        record = store.managed_input_store.get_round(execution_id.root, "r1")
        assert record is not None and record.status == "pending"
        writer.close()
        await writer.wait_closed()
        with pytest.raises(HarnessStartupError):
            await asyncio.wait_for(delivery, timeout=2)
        record = store.managed_input_store.get_round(execution_id.root, "r1")
        assert record is not None and record.status == "failed"
    finally:
        unsubscribe()
        if not delivery.done():
            delivery.cancel()
        await asyncio.gather(delivery, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await channel.close()


@pytest.mark.asyncio
async def test_pi_liveness_watchers_stop_after_response_is_delivered() -> None:
    class ManagedRuntime:
        execution_id = "execution-1"

        def __init__(self) -> None:
            self.resolved = asyncio.Event()
            self.failed = False

        async def await_round(self, *_args: object) -> dict[str, object]:
            return {
                "address": SimpleNamespace(model_dump=lambda **_: {"action": "accept"})
            }

        async def resolve_round(
            self, _round_id: str, *, operation_complete: bool
        ) -> None:
            assert operation_complete
            self.resolved.set()

        async def fail_round(self, _error: BaseException) -> None:
            self.failed = True

    class Process:
        def __init__(self) -> None:
            self.exited = asyncio.Event()

        async def wait(self) -> int:
            await self.exited.wait()
            return 0

    adapter = PiHarnessAdapter(executable="pi")
    channel = PiControlChannel()
    await channel.start()
    extension_reader, extension_writer = await _connect(channel)
    runtime = ManagedRuntime()
    process = Process()
    adapter._control_channel = channel
    adapter._managed_input_runtime = runtime
    adapter._action_generation = "g1"
    adapter._session = SimpleNamespace(turn_count=2)  # type: ignore[assignment]
    adapter._process = SimpleNamespace(  # type: ignore[assignment]
        owner=SimpleNamespace(process=process)
    )
    pending = _pending(channel)
    pending["requests"] = {
        "address": {
            "mode": "form",
            "request_key": "address",
            "message": "Enter an address",
            "requested_schema": {"type": "object"},
        }
    }
    channel.set_scope(PiControlScope("g1", 2, "execution-1", "op-1", "r1"))

    delivery = asyncio.create_task(adapter._deliver_control_pending(pending))
    try:
        response = json.loads(await asyncio.wait_for(extension_reader.readline(), 1))
        assert response["type"] == "response"
        extension_writer.write(
            (json.dumps(_terminal(channel, pending, "terminal-1")) + "\n").encode()
        )
        await extension_writer.drain()
        await asyncio.wait_for(delivery, timeout=1)
        assert runtime.resolved.is_set()
        process.exited.set()
        extension_writer.close()
        await extension_writer.wait_closed()
        await asyncio.sleep(0)
        assert not runtime.failed
    finally:
        if not delivery.done():
            delivery.cancel()
        await asyncio.gather(delivery, return_exceptions=True)
        extension_writer.close()
        await extension_writer.wait_closed()
        await channel.close()


def test_cancellation_closes_active_scope_and_rejects_late_response() -> None:
    async def scenario() -> None:
        channel = PiControlChannel()
        await channel.start()
        reader, writer = await _connect(channel)
        writer.write((json.dumps(_pending(channel)) + "\n").encode())
        await writer.drain()
        assert (await channel.receive())["type"] == "pending"
        await channel.send_cancel(
            generation="g1",
            turn_sequence=2,
            execution_id="execution-1",
            logical_operation_id="op-1",
            round_id="r1",
            reason="user_cancelled",
        )
        assert json.loads((await reader.readline()).decode())["type"] == "cancel"
        with pytest.raises(PiControlProtocolError):
            await channel.send_response(
                generation="g1",
                turn_sequence=2,
                execution_id="execution-1",
                logical_operation_id="op-1",
                round_id="r1",
                responses={"address": {"action": "accept"}},
            )
        next_operation = _pending(
            channel,
            message_id="pending-next-operation",
            logical_operation_id="op-2",
            round_id="r2",
            round_index=0,
        )
        writer.write((json.dumps(next_operation) + "\n").encode())
        await writer.drain()
        assert (await channel.receive())["logical_operation_id"] == "op-2"
        writer.close()
        await writer.wait_closed()
        await channel.close()

    asyncio.run(scenario())


def test_envelope_validation_rejects_invalid_request_and_response_keys() -> None:
    common = {
        "session_id": "s",
        "message_id": "m",
        "generation": "g",
        "turn_sequence": 1,
        "round_id": "r",
    }
    for frame_type, field in (("pending", "requests"), ("response", "responses")):
        with pytest.raises(PiControlProtocolError):
            validate_control_envelope(
                {
                    "type": frame_type,
                    **common,
                    "round_index": 0,
                    field: {"": "bad"},
                }
                if frame_type == "pending"
                else {"type": frame_type, **common, field: {"x" * 257: "bad"}},
                session_id="s",
            )


def test_pending_preserves_opaque_state_and_operation_identity_fields() -> None:
    frame = _pending(PiControlChannel(), logical_operation_id="operation-unfamiliar")
    frame["request_state"] = ""
    validated = validate_control_envelope(frame, session_id=frame["session_id"])
    assert validated["request_state"] == ""
    assert validated["operation_parameters"] == {"weight_kg": 2}
    assert validated["logical_operation_id"] == "operation-unfamiliar"


def test_pending_timestamp_and_operation_kind_are_strict() -> None:
    frame = _pending(PiControlChannel())
    frame["operation_kind"] = "unknown"
    with pytest.raises(PiControlProtocolError):
        validate_control_envelope(frame, session_id=frame["session_id"])
    frame = _pending(PiControlChannel())
    frame["created_at"] = "2026-09-22T10:00:00"
    with pytest.raises(PiControlProtocolError):
        validate_control_envelope(frame, session_id=frame["session_id"])


def test_parent_direction_rejects_delivery_terminal() -> None:
    frame = _pending(PiControlChannel())
    terminal = {
        "type": "terminal",
        "session_id": frame["session_id"],
        "message_id": "terminal",
        "generation": frame["generation"],
        "turn_sequence": frame["turn_sequence"],
        "execution_id": frame["execution_id"],
        "logical_operation_id": frame["logical_operation_id"],
        "round_id": frame["round_id"],
        "state": "delivered",
    }
    with pytest.raises(PiControlProtocolError):
        validate_control_envelope(
            terminal,
            session_id=frame["session_id"],
            scope=PiControlScope("g1", 2, "execution-1", "op-1", "r1"),
            direction="parent_to_extension",
        )


def test_channel_constructor_bounds_timeout_and_queue() -> None:
    for timeout in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            PiControlChannel(timeout=timeout)
    for queue_size in (0, -1, 33, True):
        with pytest.raises(ValueError):
            PiControlChannel(queue_size=queue_size)


def test_envelope_validation_rejects_oversized_frame() -> None:
    with pytest.raises(PiControlProtocolError):
        from m3.harness.pi_control import _encode

        _encode(
            {
                "type": "close",
                "session_id": "s",
                "message_id": "m",
                "reason": "x" * MAX_CONTROL_FRAME_BYTES,
            }
        )


def test_authenticated_pending_response_terminal_round_trip() -> None:
    async def scenario() -> None:
        channel = PiControlChannel()
        await channel.start()
        reader, writer = await _connect(channel)
        pending_frame = _pending(channel)
        writer.write((json.dumps(pending_frame, separators=(",", ":")) + "\n").encode())
        await writer.drain()
        pending = await channel.receive()
        assert pending["type"] == "pending"
        assert pending["round_id"] == "r1"
        await channel.send_response(
            generation="g1",
            turn_sequence=2,
            execution_id="execution-1",
            logical_operation_id="op-1",
            round_id="r1",
            responses={"address": {"action": "accept"}},
        )
        response = json.loads((await reader.readline()).decode())
        assert response["type"] == "response"
        next_pending = {
            **pending_frame,
            "message_id": "pending-2",
            "round_id": "r2",
            "round_index": 1,
        }
        writer.write((json.dumps(next_pending, separators=(",", ":")) + "\n").encode())
        await writer.drain()
        assert (await channel.receive())["round_id"] == "r2"
        await channel.send_response(
            generation="g1",
            turn_sequence=2,
            execution_id="execution-1",
            logical_operation_id="op-1",
            round_id="r2",
            responses={"address": {"action": "accept"}},
        )
        assert json.loads((await reader.readline()).decode())["round_id"] == "r2"
        terminal = {
            "type": "terminal",
            "session_id": channel.session_id,
            "message_id": "terminal-1",
            "generation": "g1",
            "turn_sequence": 2,
            "execution_id": "execution-1",
            "logical_operation_id": "op-1",
            "round_id": "r2",
            "state": "delivered",
        }
        writer.write((json.dumps(terminal, separators=(",", ":")) + "\n").encode())
        await writer.drain()
        assert (await channel.receive())["type"] == "terminal"
        sequential = _pending(
            channel,
            message_id="pending-3",
            logical_operation_id="op-2",
            round_id="r3",
            round_index=0,
        )
        writer.write((json.dumps(sequential, separators=(",", ":")) + "\n").encode())
        await writer.drain()
        assert (await channel.receive())["logical_operation_id"] == "op-2"
        stale = _pending(
            channel,
            message_id="stale-old-operation",
            logical_operation_id="op-1",
            round_id="r-old",
            round_index=0,
        )
        writer.write((json.dumps(stale, separators=(",", ":")) + "\n").encode())
        await writer.drain()
        with pytest.raises(PiControlClosed):
            await channel.receive(timeout=1)
        await channel.close()
        writer.close()
        await writer.wait_closed()

    asyncio.run(scenario())


def test_pi_managed_control_delivery_continues_across_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeActionChannel:
        def __init__(self) -> None:
            self.generation = ""
            self.turn_sequence = 0

        def write_context(
            self, *, generation: str, turn_sequence: int, **_: object
        ) -> None:
            self.generation = generation
            self.turn_sequence = turn_sequence

        def write_status(self, _status: BridgeActionStatus) -> None:
            return None

        def read_status(self) -> BridgeActionStatus:
            return BridgeActionStatus(self.generation, self.turn_sequence, "completed")

        def clear(self) -> None:
            return None

    class FakeManagedRuntime:
        execution_id = "execution-1"

        def __init__(self) -> None:
            self.terminal_received = asyncio.Event()

        async def await_round(
            self, _pending: object, _operation_parameters: object
        ) -> dict[str, object]:
            return {
                "address": SimpleNamespace(model_dump=lambda **_: {"action": "accept"})
            }

        async def resolve_round(
            self, _round_id: str, *, operation_complete: bool
        ) -> None:
            assert operation_complete
            self.terminal_received.set()

    class FakeProcess:
        def __init__(self) -> None:
            self.ready = asyncio.Event()

        async def next(self, _timeout: float | None) -> dict[str, str]:
            await self.ready.wait()
            return {"type": "agent_settled"}

    async def fake_send(
        self: NativeRPCAdapter, *_args: object, **_kwargs: object
    ) -> AdapterTurn:
        adapter = self
        assert isinstance(adapter, PiHarnessAdapter)
        assert isinstance(adapter._control_channel, PiControlChannel)
        control = adapter._control_channel
        action = adapter._action_channel
        assert isinstance(action, FakeActionChannel)
        runtime = adapter._managed_input_runtime
        assert isinstance(runtime, FakeManagedRuntime)
        runtime.terminal_received.clear()
        adapter._session.turn_count += 1
        turn = adapter._session.turn_count
        pending = _pending(
            control,
            message_id=f"pending-{turn}",
            generation=action.generation,
            turn_sequence=action.turn_sequence,
            logical_operation_id=f"op-{turn}",
            round_id=f"round-{turn}",
        )
        pending["requests"] = {
            "address": {
                "mode": "form",
                "request_key": "address",
                "message": "Enter an address",
                "requested_schema": {"type": "object"},
            }
        }
        process = FakeProcess()
        next_frame = asyncio.create_task(adapter.next_frame(process, 1.0))
        extension_writer = writers[0]
        extension_reader = readers[0]
        extension_writer.write((json.dumps(pending) + "\n").encode())
        await extension_writer.drain()
        response_line = await asyncio.wait_for(extension_reader.readline(), timeout=1)
        if not response_line:
            await next_frame
        response = json.loads(response_line.decode())
        assert response["type"] == "response"
        assert response["generation"] == action.generation
        extension_writer.write(
            (
                json.dumps(_terminal(control, pending, f"terminal-{turn}")) + "\n"
            ).encode()
        )
        await extension_writer.drain()
        await asyncio.wait_for(runtime.terminal_received.wait(), timeout=1)
        process.ready.set()
        assert await asyncio.wait_for(next_frame, timeout=1) == {
            "type": "agent_settled"
        }
        return AdapterTurn()

    async def scenario() -> None:
        adapter = PiHarnessAdapter(executable="pi")
        control = PiControlChannel()
        await control.start()
        reader, writer = await _connect(control)
        readers.append(reader)
        writers.append(writer)
        adapter._control_channel = control
        adapter._action_channel = FakeActionChannel()  # type: ignore[assignment]
        session = SimpleNamespace(turn_count=0)
        adapter._session = session  # type: ignore[assignment]
        adapter._managed_input_runtime = FakeManagedRuntime()  # type: ignore[assignment]
        monkeypatch.setattr(NativeRPCAdapter, "send", fake_send)
        try:
            await adapter.send("first")
            await adapter.send("second")
            assert session.turn_count == 2
        finally:
            writer.close()
            await writer.wait_closed()
            await control.close()

    readers: list[asyncio.StreamReader] = []
    writers: list[asyncio.StreamWriter] = []
    asyncio.run(scenario())


def test_authenticated_channel_replay_cache_allows_long_action() -> None:
    async def scenario() -> None:
        channel = PiControlChannel()
        await channel.start()
        reader, writer = await _connect(channel)
        last_terminal: dict[str, object] | None = None
        for index in range(130):
            pending = _pending(
                channel,
                message_id=f"pending-{index}",
                logical_operation_id=f"op-{index}",
                round_id=f"r-{index}",
            )
            writer.write((json.dumps(pending) + "\n").encode())
            await writer.drain()
            assert (await channel.receive())["round_id"] == f"r-{index}"
            response = {
                "type": "response",
                "session_id": channel.session_id,
                "message_id": f"response-{index}",
                "generation": "g1",
                "turn_sequence": 2,
                "execution_id": "execution-1",
                "logical_operation_id": f"op-{index}",
                "round_id": f"r-{index}",
                "responses": {"address": {"action": "accept"}},
            }
            await channel.send(response)
            assert json.loads((await reader.readline()).decode())["type"] == "response"
            terminal = _terminal(channel, pending, f"terminal-{index}")
            last_terminal = terminal
            writer.write((json.dumps(terminal) + "\n").encode())
            await writer.drain()
            assert (await channel.receive())["type"] == "terminal"
        assert last_terminal is not None
        writer.write((json.dumps(last_terminal) + "\n").encode())
        await writer.drain()
        with pytest.raises(PiControlClosed):
            await channel.receive(timeout=1)
        await channel.close()

    asyncio.run(scenario())


def test_bad_auth_and_duplicate_message_close_connection() -> None:
    async def scenario() -> None:
        channel = PiControlChannel()
        await channel.start()
        reader, writer = await asyncio.open_connection(channel.host, channel.port)
        writer.write(
            (
                json.dumps(
                    {
                        "type": "hello",
                        "version": CONTROL_PROTOCOL_VERSION,
                        "session_id": channel.session_id,
                        "token": "wrong",
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        assert await reader.readline() == b""
        writer.close()
        await writer.wait_closed()

        _reader, writer = await _connect(channel)
        frame = {
            **_pending(channel, message_id="same"),
            "generation": "g",
            "turn_sequence": 1,
        }
        encoded = (json.dumps(frame, separators=(",", ":")) + "\n").encode()
        writer.write(encoded + encoded)
        await writer.drain()
        await asyncio.sleep(0.05)
        assert (await channel.receive())["message_id"] == "same"
        with pytest.raises(PiControlClosed):
            await channel.receive(timeout=1)
        writer.close()
        await writer.wait_closed()
        await channel.close()

    asyncio.run(scenario())


def test_eof_is_visible_and_no_files_are_used() -> None:
    async def scenario() -> None:
        channel = PiControlChannel()
        await channel.start()
        _reader, writer = await _connect(channel)
        writer.close()
        await writer.wait_closed()
        with pytest.raises(PiControlClosed):
            await channel.receive(timeout=1)
        assert channel.environment["M3_PI_CONTROL_HOST"] == "127.0.0.1"
        assert "M3_PI_CONTROL_TOKEN" in channel.environment
        await channel.close()

    asyncio.run(scenario())


def test_unterminated_oversized_frame_and_full_queue_are_terminal() -> None:
    async def scenario() -> None:
        channel = PiControlChannel(timeout=0.02, queue_size=1)
        await channel.start()
        _reader, writer = await _connect(channel)
        writer.write(b"{" + b"x" * (MAX_CONTROL_FRAME_BYTES + 1024))
        await writer.drain()
        with pytest.raises(PiControlClosed):
            await channel.receive(timeout=1)
        writer.close()
        await writer.wait_closed()
        await channel.close()

        channel = PiControlChannel(timeout=0.02, queue_size=1)
        await channel.start()
        _reader, writer = await _connect(channel)
        common = {
            "type": "pending",
            "session_id": channel.session_id,
            "generation": "g",
            "turn_sequence": 1,
            "execution_id": "execution-1",
            "logical_operation_id": "op-1",
            "round_id": "r",
            "round_index": 0,
            "round_limit": 4,
            "server": "orders",
            "operation_kind": "tool",
            "operation_name": "book",
            "request_state": "opaque-state",
            "requests": {"address": {"mode": "form", "request_key": "address"}},
            "created_at": "2026-09-22T10:00:00+00:00",
            "deadline": None,
            "operation_parameters": {},
        }
        first = {**common, "message_id": "first"}
        second = {**common, "message_id": "second"}
        writer.write((json.dumps(first) + "\n" + json.dumps(second) + "\n").encode())
        await writer.drain()
        await asyncio.sleep(0.05)
        assert (await channel.receive())["message_id"] == "first"
        with pytest.raises(PiControlClosed):
            await channel.receive(timeout=1)
        writer.close()
        await writer.wait_closed()
        await channel.close()

    asyncio.run(scenario())
