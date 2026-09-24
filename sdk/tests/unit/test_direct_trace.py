"""Contracts for normalized direct MCP stream tracing."""

from __future__ import annotations

from typing import Any, cast

import pytest
from anyio import EndOfStream
from mcp import types
from mcp.shared.message import SessionMessage
from mcp_types import ErrorData, JSONRPCError, JSONRPCRequest, JSONRPCResponse

from m3.direct_client import AsyncDirectClient
from m3.direct_trace import DirectTraceBridge
from m3.errors import TransportError
from m3.events import EventFactory, EventSequence
from m3.execution_trace import ExecutionTraceRecorder, TraceRecorderError
from m3.storage import InMemoryExecutionStore
from m3.types import (
    Event,
    EventId,
    EventKind,
    ExecutionId,
    ExecutionOutcome,
    ExecutionStatus,
    TransportKind,
)


def _correlation(event: Any) -> Any:
    assert event.correlation is not None
    return event.correlation


class _Read:
    def __init__(self, values: list[Any]) -> None:
        self.values = values
        self.closed = False

    async def receive(self) -> Any:
        if not self.values:
            raise EOFError
        return self.values.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class _Write:
    def __init__(self) -> None:
        self.values: list[Any] = []
        self.closed = False

    async def send(self, item: Any, /) -> None:
        self.values.append(item)

    async def aclose(self) -> None:
        self.closed = True


def _request(identifier: int | str, method: str = "tools/call") -> SessionMessage:
    return SessionMessage(
        message=JSONRPCRequest(
            jsonrpc="2.0",
            id=identifier,
            method=method,
            params={"token": "trace-secret", "nested": {"ok": True}},
        )
    )


def _response(identifier: int | str) -> SessionMessage:
    return SessionMessage(
        message=JSONRPCResponse(
            jsonrpc="2.0",
            id=identifier,
            result={"content": [{"type": "text", "text": "done"}]},
        )
    )


@pytest.mark.asyncio
async def test_stream_wrappers_preserve_values_and_correlate_typed_ids() -> None:
    bridge = DirectTraceBridge(
        execution_id="execution-test", connection_id="connection-test"
    )
    read = _Read([_response("7"), _response(7)])
    write = _Write()
    observed_read, observed_write = bridge.wrap_streams(read, write)

    integer_request = _request(7)
    string_request = _request("7")
    await observed_write.send(integer_request)
    await observed_write.send(string_request)
    assert write.values == [integer_request, string_request]
    # The first response is string-typed and must match only request "7".
    first_response = await observed_read.receive()
    second_response = await observed_read.receive()
    assert first_response.message.id == "7"
    assert second_response.message.id == 7
    events = bridge.trace.events
    requests = [
        event for event in events if event.kind is EventKind.TOOL_CALL_REQUESTED
    ]
    responses = [
        event for event in events if event.kind is EventKind.TOOL_RESULT_RECEIVED
    ]
    assert [_correlation(event).jsonrpc_id for event in requests] == [7, "7"]
    assert [_correlation(event).request_sequence for event in requests] == [1, 2]
    assert [_correlation(event).request_sequence for event in responses] == [2, 1]
    assert [type(_correlation(event).jsonrpc_id) for event in responses] == [str, int]
    assert all("trace-secret" not in repr(event.payload) for event in events)
    assert bridge.trace.limitations == ("capture_incomplete",)


@pytest.mark.asyncio
async def test_resolved_secret_canary_is_atomic_and_redacts_echo_and_error_trace() -> (
    None
):
    bridge = DirectTraceBridge(
        execution_id="execution-secret", connection_id="connection-secret"
    )
    bridge.bind_secret_values(("overlap", "overlap-secret"))
    _, write = bridge.wrap_streams(_Read([]), _Write())

    request = SessionMessage(
        message=JSONRPCRequest(
            jsonrpc="2.0",
            id=1,
            method="tools/call",
            params={"echo": "overlap-secret"},
        )
    )
    error = SessionMessage(
        message=JSONRPCError(
            jsonrpc="2.0",
            id=1,
            error=ErrorData(code=-32000, message="provider rejected overlap-secret"),
        )
    )
    await write.send(request)
    bridge.observe(error, "inbound")

    rendered = repr(bridge.trace.model_dump(mode="json"))
    assert "overlap-secret" not in rendered
    assert "overlap" not in rendered
    assert any(event.kind is EventKind.MCP_ERROR for event in bridge.trace.events)


@pytest.mark.asyncio
async def test_secret_binds_after_lifecycle_events_but_not_after_capture() -> None:
    # A managed execution is queued and starting before its stdio transport
    # resolves a secret; those lifecycle events must not block the bind.
    execution = ExecutionId("execution-managed-secret")
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, execution)
    for lifecycle in (ExecutionStatus.QUEUED, ExecutionStatus.STARTING):
        recorder.emit(
            EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": lifecycle.value}
        )
    bridge = DirectTraceBridge(
        store=store,
        execution_id=execution,
        connection_id="connection-managed-secret",
        event_factory=EventFactory(execution, allocator=EventSequence(start=3)),
        recorder=recorder,
    )
    bridge.bind_secret_values("canary-7f3a91")
    bridge.record_transport_connected(TransportKind.STDIO)
    _, write = bridge.wrap_streams(_Read([]), _Write())
    await write.send(
        SessionMessage(
            message=JSONRPCRequest(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params={"echo": "canary-7f3a91"},
            )
        )
    )
    assert "canary-7f3a91" not in repr(bridge.trace.model_dump(mode="json"))

    # Once a transport event exists, earlier provider data may be unredacted.
    with pytest.raises(TraceRecorderError):
        bridge.bind_secret_values("late-secret")


@pytest.mark.parametrize(
    ("payload", "fields"),
    [
        ({"lifecycle": "queued", "detail": "late-canary"}, {}),
        ({"lifecycle": "queued"}, {"server_binding": "late-canary"}),
    ],
)
def test_secret_bind_rejects_lifecycle_events_carrying_extra_values(
    payload: dict[str, str], fields: dict[str, str]
) -> None:
    # Event validation accepts extra payload keys and ancillary fields, so a
    # lifecycle-kind event may already hold the value about to be bound.
    execution = ExecutionId("execution-late-bind")
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, execution)
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload=payload, **fields)
    bridge = DirectTraceBridge(
        store=store,
        execution_id=execution,
        connection_id="connection-late-bind",
        event_factory=EventFactory(execution, allocator=EventSequence(start=2)),
        recorder=recorder,
    )
    with pytest.raises(TraceRecorderError):
        bridge.bind_secret_values("late-canary")


def test_secret_bind_rejects_lifecycle_event_whose_identity_holds_the_secret() -> None:
    # The payload and fields match the SDK lifecycle shape exactly; only the
    # caller-chosen event ID carries the value about to be bound.
    execution = ExecutionId("execution-forged-id")
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, execution)
    recorder.record(
        Event(
            event_id=EventId("late-canary"),
            execution_id=execution,
            sequence=1,
            kind=EventKind.EXECUTION_STATE_CHANGED,
            monotonic_offset_ms=0,
            payload={"lifecycle": "queued"},
        )
    )
    bridge = DirectTraceBridge(
        store=store,
        execution_id=execution,
        connection_id="connection-forged-id",
        event_factory=EventFactory(execution, allocator=EventSequence(start=2)),
        recorder=recorder,
    )
    with pytest.raises(TraceRecorderError):
        bridge.bind_secret_values("late-canary")


@pytest.mark.asyncio
async def test_same_typed_id_correlates_by_request_role_and_direction() -> None:
    bridge = DirectTraceBridge(
        execution_id="execution-duplex", connection_id="connection-duplex"
    )
    read = _Read(
        [
            _request(7, method="sampling/createMessage"),
            _response(7),
        ]
    )
    write = _Write()
    observed_read, observed_write = bridge.wrap_streams(read, write)

    # Both peers may legitimately have request ID 7 in flight at once.
    await observed_write.send(_request(7, method="tools/call"))
    await observed_read.receive()
    events = bridge.trace.events
    requests = [
        event
        for event in events
        if event.kind in {EventKind.TOOL_CALL_REQUESTED, EventKind.SAMPLING_REQUEST}
    ]
    assert len(requests) == 2
    client_request, server_request = requests
    assert client_request.correlation is not None
    assert server_request.correlation is not None
    assert client_request.correlation.direction.value == "client_to_server"
    assert server_request.correlation.direction.value == "server_to_client"

    # A server response resolves the client request; a client response
    # resolves the server request, despite the identical typed ID.
    await observed_read.receive()
    await observed_write.send(_response(7))
    responses = [
        event
        for event in bridge.trace.events
        if event.kind in {EventKind.TOOL_RESULT_RECEIVED, EventKind.SAMPLING_RESPONSE}
    ]
    assert len(responses) == 2
    assert responses[0].kind is EventKind.TOOL_RESULT_RECEIVED
    assert responses[0].correlation is not None
    assert (
        responses[0].correlation.request_sequence
        == client_request.correlation.request_sequence
    )
    assert responses[1].kind is EventKind.SAMPLING_RESPONSE
    assert responses[1].correlation is not None
    assert (
        responses[1].correlation.request_sequence
        == server_request.correlation.request_sequence
    )


@pytest.mark.asyncio
async def test_failed_recorder_commit_rolls_back_event_and_request_reservations() -> (
    None
):
    bridge = DirectTraceBridge(
        execution_id="execution-rollback", connection_id="connection-rollback"
    )
    _, write = bridge.wrap_streams(_Read([]), _Write())
    recorder = bridge._recorder
    original_record = recorder.record
    rejected = True

    def reject_once(event: Any) -> Any:
        nonlocal rejected
        if rejected:
            rejected = False
            raise RuntimeError("terminal race")
        return original_record(event)

    recorder.record = reject_once  # type: ignore[method-assign]
    await write.send(_request(1))
    await write.send(_request(2))
    requests = [
        event
        for event in bridge.trace.events
        if event.kind is EventKind.TOOL_CALL_REQUESTED
    ]
    assert len(requests) == 1
    assert requests[0].sequence == 1
    assert requests[0].correlation is not None
    assert requests[0].correlation.request_sequence == 1


@pytest.mark.asyncio
async def test_observation_after_finalization_does_not_reserve_or_append() -> None:
    bridge = DirectTraceBridge(
        execution_id="execution-late", connection_id="connection-late"
    )
    _, write = bridge.wrap_streams(_Read([]), _Write())
    final = bridge.finalize(ExecutionOutcome.COMPLETED)
    await write.send(_request(1))
    assert bridge.trace == final
    assert bridge.final_trace == final


@pytest.mark.asyncio
async def test_request_sequences_are_scoped_by_connection() -> None:
    execution = ExecutionId("execution-shared")
    store = InMemoryExecutionStore()
    factory = EventFactory(
        execution,
        allocator=EventSequence(start=1),
    )
    recorder = ExecutionTraceRecorder(store, execution)
    first = DirectTraceBridge(
        store=store,
        execution_id=execution,
        connection_id="connection-a",
        event_factory=factory,
        recorder=recorder,
    )
    second = DirectTraceBridge(
        store=store,
        execution_id=execution,
        connection_id="connection-b",
        event_factory=factory,
        recorder=recorder,
    )
    _, first_write = first.wrap_streams(_Read([]), _Write())
    _, second_write = second.wrap_streams(_Read([]), _Write())

    await first_write.send(_request(7))
    await second_write.send(_request(7))
    first_event = next(
        event
        for event in first.trace.events
        if event.kind is EventKind.TOOL_CALL_REQUESTED
        and event.connection_id == first.connection_id
    )
    second_event = next(
        event
        for event in second.trace.events
        if event.kind is EventKind.TOOL_CALL_REQUESTED
        and event.connection_id == second.connection_id
    )
    assert _correlation(first_event).request_sequence == 1
    assert _correlation(second_event).request_sequence == 1
    assert first_event.connection_id != second_event.connection_id


@pytest.mark.asyncio
async def test_finalize_is_partial_when_only_normalized_messages_are_available() -> (
    None
):
    bridge = DirectTraceBridge(
        execution_id="execution-finalize", connection_id="connection-finalize"
    )
    _, write = bridge.wrap_streams(_Read([]), _Write())
    await write.send(_request(1))
    result = bridge.finalize(ExecutionOutcome.COMPLETED)
    assert result.completeness == "partial"
    assert "capture_incomplete" in result.limitations
    assert bridge.final_trace == result
    assert bridge.finalize(ExecutionOutcome.COMPLETED) == result


@pytest.mark.asyncio
async def test_direct_failure_contains_trace_identity_without_payloads() -> None:
    class BrokenSession:
        async def __aenter__(self) -> BrokenSession:
            return self

        async def __aexit__(
            self, exc_type: Any, exc_value: Any, traceback: Any
        ) -> None:
            return None

        async def initialize(self) -> types.InitializeResult:
            return types.InitializeResult(
                protocol_version="2025-11-25",
                server_info=types.Implementation(name="fixture", version="1"),
                capabilities=types.ServerCapabilities(),
            )

        async def send_ping(self, **kwargs: Any) -> types.EmptyResult:
            raise OSError("secret socket details")

    bridge = DirectTraceBridge(
        execution_id="execution-error", connection_id="connection-error"
    )
    client = AsyncDirectClient(cast(Any, BrokenSession()), trace_bridge=bridge)
    async with client:
        with pytest.raises(TransportError) as error:
            await client.ping()
    assert error.value.details["trace_evidence"]["execution_id"] == "execution-error"
    assert "secret socket details" not in repr(error.value.details)


@pytest.mark.asyncio
async def test_malformed_stream_values_become_safe_diagnostics_without_breaking_reads() -> (
    None
):
    bridge = DirectTraceBridge(
        execution_id="execution-malformed", connection_id="connection-malformed"
    )
    malformed = object()
    read = _Read([malformed])
    observed_read, _ = bridge.wrap_streams(read, _Write())
    assert await observed_read.receive() is malformed
    diagnostics = [
        event for event in bridge.trace.events if event.kind is EventKind.DIAGNOSTIC
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].payload == {
        "evidence_mode": "normalized_session_message",
    }


@pytest.mark.asyncio
async def test_observed_read_stream_maps_anyio_end_of_stream_to_iteration_end() -> None:
    class EndedRead:
        async def receive(self) -> Any:
            raise EndOfStream

        async def aclose(self) -> None:
            return None

    bridge = DirectTraceBridge(
        execution_id="execution-end", connection_id="connection-end"
    )
    observed_read, _ = bridge.wrap_streams(EndedRead(), _Write())
    with pytest.raises(StopAsyncIteration):
        await anext(observed_read)
