"""Transparent stable tracing for official MCP session streams.

The official ``ClientSession`` remains the protocol engine.  This module only
wraps its read/write stream objects and observes already-decoded
``SessionMessage`` values.  It deliberately does not claim raw-wire capture:
the resulting trace is normalized evidence and remains partial until a later
wire-capture layer is attached.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from threading import Lock, RLock
from types import TracebackType
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from anyio import EndOfStream
from mcp.shared.message import SessionMessage
from mcp_types import (
    JSONRPCError,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)

from .events import EventFactory, EventSequence
from .execution_trace import ExecutionTraceRecorder, TraceRecorderError
from .storage import ExecutionStore, InMemoryExecutionStore
from .trace.redaction import RedactionConfig
from .types import (
    ConnectionId,
    Event,
    EventDirection,
    EventKind,
    EventOrigin,
    EventSource,
    ExecutionId,
    ExecutionOutcome,
    LifecyclePhase,
    ProjectId,
    RequestLink,
    TraceId,
    TraceResult,
    TransportKind,
)


class _ReadStream(Protocol):
    async def receive(self) -> Any: ...

    async def aclose(self) -> None: ...


class _WriteStream(Protocol):
    async def send(self, item: Any, /) -> None: ...

    async def aclose(self) -> None: ...


Direction = Literal["inbound", "outbound"]

# A shared EventFactory can serve several connection bridges.  Serializing
# factory reservation through one lock also makes a failed recorder commit
# rollback-safe across those bridges.
_FACTORY_LOCKS: dict[int, RLock] = {}
_FACTORY_LOCKS_GUARD = Lock()


def _factory_lock(factory: EventFactory) -> RLock:
    key = id(factory)
    with _FACTORY_LOCKS_GUARD:
        lock = _FACTORY_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _FACTORY_LOCKS[key] = lock
        return lock


def _id_key(value: Any) -> tuple[type[Any], Any] | None:
    """Keep integer JSON-RPC ID 7 distinct from string ID ``"7"``."""

    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    return type(value), value


def _message(value: Any) -> Any:
    if isinstance(value, SessionMessage):
        return value.message
    return value


def _dump_message(value: Any) -> dict[str, Any]:
    if isinstance(
        value, (JSONRPCRequest, JSONRPCResponse, JSONRPCError, JSONRPCNotification)
    ):
        dumped = value.model_dump(mode="json", by_alias=True)
        return dumped
    return {"stream_value": "unsupported_message"}


def _semantic_fields(value: Any) -> dict[str, Any]:
    dumped = _dump_message(value)
    return {
        key: item
        for key, item in dumped.items()
        if key not in {"jsonrpc", "id", "method"}
    }


def _method(value: Any) -> str | None:
    method = getattr(value, "method", None)
    return method if isinstance(method, str) else None


def _phase(method: str | None) -> LifecyclePhase:
    if method == "initialize":
        return LifecyclePhase.INITIALIZATION
    if method == "tools/call" or method in {
        "resources/read",
        "resources/list",
        "resources/templates/list",
        "resources/subscribe",
        "resources/unsubscribe",
        "prompts/get",
        "prompts/list",
        "completion/complete",
    }:
        return LifecyclePhase.MCP_CALL
    return LifecyclePhase.IDLE


def _request_kind(method: str | None) -> EventKind:
    if method == "sampling/createMessage":
        return EventKind.SAMPLING_REQUEST
    if method == "elicitation/create":
        return EventKind.ELICITATION_REQUEST
    if method == "tools/call":
        return EventKind.TOOL_CALL_REQUESTED
    if method == "notifications/progress":
        return EventKind.MCP_PROGRESS
    if method == "notifications/cancelled":
        return EventKind.MCP_CANCELLATION_COMPLETED
    return EventKind.MCP_REQUEST


def _notification_kind(method: str | None) -> EventKind:
    if method == "notifications/progress":
        return EventKind.MCP_PROGRESS
    if method == "notifications/cancelled":
        return EventKind.MCP_CANCELLATION_COMPLETED
    return EventKind.MCP_NOTIFICATION


class DirectTraceBridge:
    """Observe one MCP connection and build an immutable stable trace.

    A bridge may be constructed with a shared :class:`EventFactory` when an
    execution owns multiple connections.  Request counters are then scoped by
    the connection identity while execution event sequences remain global.
    """

    def __init__(
        self,
        *,
        store: ExecutionStore | None = None,
        execution_id: ExecutionId | str | None = None,
        connection_id: ConnectionId | str | None = None,
        trace_id: TraceId | str | None = None,
        event_factory: EventFactory | None = None,
        recorder: ExecutionTraceRecorder | None = None,
        server_binding: str | None = None,
        server_bindings: Sequence[Mapping[str, Any]] = (),
        run_id: str | None = None,
        suite_name: str | None = None,
        project_id: ProjectId | str | None = None,
        redaction_config: RedactionConfig | None = None,
    ) -> None:
        self._store = store if store is not None else InMemoryExecutionStore()
        self._execution_id = (
            execution_id
            if isinstance(execution_id, ExecutionId)
            else ExecutionId(execution_id or f"execution-{uuid4().hex}")
        )
        self._connection_id = (
            connection_id
            if isinstance(connection_id, ConnectionId)
            else ConnectionId(connection_id or f"connection-{uuid4().hex}")
        )
        self._server_binding = server_binding
        self._server_bindings = tuple(dict(item) for item in server_bindings)
        self._redaction_config = (
            redaction_config
            if redaction_config is not None
            else RedactionConfig.from_environment()
        )
        self._factory = event_factory or EventFactory(
            self._execution_id,
            allocator=EventSequence(start=1),
            source="mcp_pal.direct.stream",
        )
        if self._factory.execution_id != self._execution_id:
            raise ValueError("event factory belongs to another execution")
        if self._factory.next_sequence < 1:
            raise ValueError(
                "event factory sequence must reserve 0 for execution.created"
            )
        self._recorder = recorder or ExecutionTraceRecorder(
            self._store,
            self._execution_id,
            trace_id=trace_id,
            run_id=run_id,
            suite_name=suite_name,
            project_id=project_id,
            server_bindings=self._server_bindings,
            redaction_config=self._redaction_config,
        )
        if self._recorder.execution_id != self._execution_id:
            raise ValueError("recorder belongs to another execution")
        self._pending: dict[
            tuple[type[Any], Any, EventDirection], tuple[int, str | None, EventKind]
        ] = {}
        self._lock = Lock()
        self._factory_guard = _factory_lock(self._factory)
        self._final: TraceResult | None = None
        self._normalized_only = True
        self._transport_connected = False

    def bind_secret_values(self, values: Iterable[str]) -> None:
        """Bind resolved transport canaries before observing server messages."""

        # Transport observers deliver one resolved value at a time, while the
        # public helper also accepts a collection for callers binding several
        # values together.  Treat a string atomically; iterating it would
        # install every character as a canary and redact trace identities.
        candidates = (values,) if isinstance(values, str) else tuple(values)
        if any(not isinstance(value, str) or not value for value in candidates):
            raise ValueError("resolved secret values must be non-empty strings")
        with self._lock:
            self._redaction_config = RedactionConfig(
                secrets=self._redaction_config.secrets.union(candidates),
                sensitive_keys=self._redaction_config.sensitive_keys,
                credential_file_contents=self._redaction_config.credential_file_contents,
                include_environment=False,
            )
            self._recorder.bind_redaction_config(self._redaction_config)

    def clear_secret_values(self) -> None:
        """Drop resolved canaries while retaining only redacted trace events."""

        with self._lock:
            self._redaction_config = RedactionConfig(
                sensitive_keys=self._redaction_config.sensitive_keys,
                include_environment=False,
            )
            self._recorder.bind_redaction_config(
                self._redaction_config, allow_after_events=True
            )
            self._pending.clear()

    @property
    def execution_id(self) -> ExecutionId:
        return self._execution_id

    @property
    def connection_id(self) -> ConnectionId:
        return self._connection_id

    @property
    def _execution_store(self) -> ExecutionStore:
        return self._store

    @property
    def trace_id(self) -> TraceId:
        return self._recorder.trace_id

    @property
    def final_trace(self) -> TraceResult | None:
        with self._lock:
            return self._final.model_copy() if self._final is not None else None

    @property
    def trace(self) -> TraceResult:
        """Return a safe live projection, marked partial until finalized."""

        with self._lock:
            if self._final is not None:
                return self._final.model_copy()
            events = self._recorder.events()
            return TraceResult(
                trace_id=self.trace_id,
                execution_id=self._execution_id,
                completeness="partial",
                highest_sequence=events[-1].sequence if events else 0,
                events=events,
                limitations=("capture_incomplete",),
            )

    def partial_evidence(self) -> Mapping[str, Any]:
        """Return bounded trace identity suitable for typed failure details."""

        current = self.trace
        return {
            "trace_id": self.trace_id.root,
            "execution_id": self._execution_id.root,
            "connection_id": self._connection_id.root,
            "highest_sequence": current.highest_sequence,
            "completeness": current.completeness,
            "limitations": current.limitations,
            "evidence_mode": "normalized_session_messages",
        }

    def wrap_streams(
        self, read_stream: _ReadStream, write_stream: _WriteStream
    ) -> tuple[_ReadStream, _WriteStream]:
        """Return transparent observers preserving the official stream API."""

        return _ObservedReadStream(read_stream, self), _ObservedWriteStream(
            write_stream, self
        )

    def record_transport_connected(self, transport: TransportKind) -> None:
        """Record successful transport setup once, before MCP initialization."""

        with self._lock:
            if self._final is not None or self._transport_connected:
                return
            with self._factory_guard:
                event = self._factory.create(
                    EventKind.TRANSPORT_CONNECTED,
                    connection_id=self._connection_id,
                    server_binding=self._server_binding,
                    lifecycle_phase=LifecyclePhase.STARTUP,
                    payload={
                        "configured_transport": transport.value,
                        "instrumented_transport": transport.value,
                    },
                    provenance=EventSource(
                        origin=EventOrigin.NORMALIZED,
                        source="mcp_pal.direct.transport",
                    ),
                )
                try:
                    self._recorder.record(event)
                except Exception:
                    self._rollback_reservation(event)
                    raise
            self._transport_connected = True

    def observe(self, value: Any, direction: Direction) -> None:
        """Observe one decoded stream item without changing stream semantics."""

        with self._lock:
            if self._final is not None:
                return
        try:
            message = _message(value)
            if isinstance(message, JSONRPCRequest):
                self._observe_request(message, direction)
            elif isinstance(message, JSONRPCNotification):
                self._observe_notification(message, direction)
            elif isinstance(message, JSONRPCResponse):
                self._observe_response(message, direction, is_error=False)
            elif isinstance(message, JSONRPCError):
                self._observe_response(message, direction, is_error=True)
            else:
                self._record_diagnostic()
        except Exception:
            # A malformed provider value must never break the official
            # stream.  The diagnostic itself contains no provider payload.
            try:
                self._record_diagnostic()
            except Exception:
                return

    def _correlation_direction(self, direction: Direction) -> EventDirection:
        return (
            EventDirection.CLIENT_TO_SERVER
            if direction == "outbound"
            else EventDirection.SERVER_TO_CLIENT
        )

    def _observe_request(self, message: JSONRPCRequest, direction: Direction) -> None:
        method = _method(message)
        event_kind = _request_kind(method)
        event_direction = self._correlation_direction(direction)
        event_sequence: int | None = None
        if direction == "outbound":
            event = self._create(
                event_kind,
                method=method,
                jsonrpc_id=message.id,
                direction=event_direction,
                phase=_phase(method),
                payload_extra=_semantic_fields(message),
            )
            if event is None:
                return
            event_sequence = (
                event.correlation.request_sequence
                if event.correlation is not None
                else None
            )
        else:
            with self._factory_guard:
                event_sequence = self._factory.next_request_sequence(
                    self._connection_id
                )
                event = self._create(
                    event_kind,
                    method=method,
                    jsonrpc_id=message.id,
                    direction=event_direction,
                    request_sequence=event_sequence,
                    phase=_phase(method),
                    payload_extra=_semantic_fields(message),
                )
                if event is None:
                    request_allocator = self._factory._request_allocator
                    request_allocator.rollback(self._connection_id, event_sequence)
                    return
        key = _id_key(message.id)
        if key is not None and event_sequence is not None:
            pending_key = (key[0], key[1], event_direction)
            with self._lock:
                if self._final is None:
                    self._pending[pending_key] = (event_sequence, method, event_kind)
        if method == "notifications/cancelled":
            return

    def _observe_notification(
        self, message: JSONRPCNotification, direction: Direction
    ) -> None:
        method = _method(message)
        self._create(
            _notification_kind(method),
            method=method,
            jsonrpc_id=None,
            direction=self._correlation_direction(direction),
            phase=_phase(method),
            payload_extra=_semantic_fields(message),
        )

    def _observe_response(
        self,
        message: JSONRPCResponse | JSONRPCError,
        direction: Direction,
        *,
        is_error: bool,
    ) -> None:
        key = _id_key(message.id)
        pending: tuple[int, str | None, EventKind] | None = None
        if key is not None:
            response_direction = self._correlation_direction(direction)
            request_direction = (
                EventDirection.SERVER_TO_CLIENT
                if response_direction is EventDirection.CLIENT_TO_SERVER
                else EventDirection.CLIENT_TO_SERVER
            )
            pending_key = (key[0], key[1], request_direction)
            with self._lock:
                pending = self._pending.pop(pending_key, None)
        method = pending[1] if pending is not None else None
        request_kind = pending[2] if pending is not None else EventKind.MCP_REQUEST
        if is_error:
            kind = EventKind.MCP_ERROR
        elif request_kind is EventKind.SAMPLING_REQUEST:
            kind = EventKind.SAMPLING_RESPONSE
        elif request_kind is EventKind.ELICITATION_REQUEST:
            kind = EventKind.ELICITATION_RESPONSE
        elif request_kind is EventKind.TOOL_CALL_REQUESTED:
            kind = EventKind.TOOL_RESULT_RECEIVED
        else:
            kind = EventKind.MCP_RESPONSE
        self._create(
            kind,
            method=method,
            jsonrpc_id=message.id,
            direction=self._correlation_direction(direction),
            request_sequence=pending[0] if pending is not None else None,
            phase=_phase(method),
            payload_extra=_semantic_fields(message),
        )
        if method == "initialize" and not is_error:
            self._create(
                EventKind.MCP_INITIALIZED,
                method=method,
                jsonrpc_id=message.id,
                direction=self._correlation_direction(direction),
                request_sequence=pending[0] if pending is not None else None,
                phase=LifecyclePhase.INITIALIZATION,
                payload_extra={},
            )

    def _create(
        self,
        kind: EventKind,
        *,
        method: str | None,
        jsonrpc_id: int | str | None,
        direction: EventDirection,
        request_sequence: int | None = None,
        phase: LifecyclePhase,
        payload_extra: Mapping[str, Any] | None = None,
    ) -> Event | None:
        message_payload: dict[str, Any] = {
            "evidence_mode": "normalized_session_message"
        }
        if method is not None:
            message_payload["method"] = method
        if payload_extra:
            message_payload.update(payload_extra)
        correlation = RequestLink(
            jsonrpc_id=jsonrpc_id,
            direction=direction,
            request_sequence=request_sequence,
        )
        with self._lock:
            if self._final is not None:
                return None
            # Hold the shared factory guard across construction and recorder
            # commit.  If commit rejects (for example, a terminal race), the
            # sequence and auto-assigned request counter are returned before
            # any later producer can reserve them.
            with self._factory_guard:
                event = self._factory.create(
                    kind,
                    connection_id=self._connection_id,
                    server_binding=self._server_binding,
                    correlation=correlation,
                    lifecycle_phase=phase,
                    payload=message_payload,
                    provenance=EventSource(
                        origin=EventOrigin.NORMALIZED,
                        source="mcp_pal.direct.stream",
                    ),
                )
                try:
                    self._recorder.record(event)
                except Exception:
                    self._rollback_reservation(event)
                    return None
                return event

    def _rollback_reservation(self, event: Event) -> None:
        """Return reservations made by a factory event rejected by storage."""

        allocator = self._factory._allocator
        allocator.rollback(event.sequence)
        correlation = event.correlation
        if (
            correlation is not None
            and correlation.request_sequence is not None
            and event.connection_id is not None
            and correlation.direction
            in {EventDirection.CLIENT_TO_SERVER, EventDirection.SDK_TO_HARNESS}
        ):
            request_allocator = self._factory._request_allocator
            request_allocator.rollback(
                event.connection_id, correlation.request_sequence
            )

    def _record_diagnostic(self) -> None:
        self._create(
            EventKind.DIAGNOSTIC,
            method=None,
            jsonrpc_id=None,
            direction=EventDirection.INTERNAL,
            phase=LifecyclePhase.UNKNOWN,
        )

    def finalize(
        self,
        outcome: ExecutionOutcome,
        *,
        cleanup_succeeded: bool = True,
        persistence_succeeded: bool = True,
        limitations: tuple[str, ...] = (),
        direct_result: Mapping[str, Any] | None = None,
    ) -> TraceResult:
        with self._lock:
            if self._final is not None:
                return self._final.model_copy()
            allowed = {
                "cleanup_failed",
                "persistence_failed",
                "capture_incomplete",
                "partial_trace",
            }
            merged_values: list[str] = []
            for item in limitations:
                if not isinstance(item, str) or not item.strip() or item not in allowed:
                    raise TraceRecorderError("execution limitation is invalid")
                if item not in merged_values:
                    merged_values.append(item)
            if "capture_incomplete" not in merged_values:
                merged_values.append("capture_incomplete")
            merged = tuple(merged_values)
            if not cleanup_succeeded and "cleanup_failed" not in merged:
                merged = (*merged, "cleanup_failed")
            if not persistence_succeeded and "persistence_failed" not in merged:
                merged = (*merged, "persistence_failed")
            terminal_payload: dict[str, Any] = {
                "outcome": outcome.value,
                "completeness": "partial",
                "limitations": list(merged),
            }
            if direct_result is not None:
                terminal_payload["direct_result"] = dict(direct_result)
            terminal = self._recorder.emit(
                EventKind.EXECUTION_FINISHED,
                payload=terminal_payload,
            )
            self._final = TraceResult(
                trace_id=self.trace_id,
                execution_id=self._execution_id,
                completeness="partial",
                highest_sequence=terminal.sequence,
                events=self._recorder.events(),
                limitations=merged,
            )
            return self._final.model_copy()


class _ObservedReadStream:
    def __init__(self, stream: _ReadStream, bridge: DirectTraceBridge) -> None:
        self._stream = stream
        self._bridge = bridge

    async def receive(self) -> Any:
        value = await self._stream.receive()
        self._bridge.observe(value, "inbound")
        return value

    async def aclose(self) -> None:
        await self._stream.aclose()

    def __aiter__(self) -> _ObservedReadStream:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.receive()
        except (EndOfStream, StopAsyncIteration, EOFError):
            raise StopAsyncIteration from None

    async def __aenter__(self) -> _ObservedReadStream:
        enter = getattr(self._stream, "__aenter__", None)
        if callable(enter):
            await enter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        exit_method = getattr(self._stream, "__aexit__", None)
        if callable(exit_method):
            return cast(bool | None, await exit_method(exc_type, exc_value, traceback))
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class _ObservedWriteStream:
    def __init__(self, stream: _WriteStream, bridge: DirectTraceBridge) -> None:
        self._stream = stream
        self._bridge = bridge

    async def send(self, item: Any, /) -> None:
        await self._stream.send(item)
        self._bridge.observe(item, "outbound")

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def __aenter__(self) -> _ObservedWriteStream:
        enter = getattr(self._stream, "__aenter__", None)
        if callable(enter):
            await enter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        exit_method = getattr(self._stream, "__aexit__", None)
        if callable(exit_method):
            return cast(bool | None, await exit_method(exc_type, exc_value, traceback))
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


__all__ = ["DirectTraceBridge"]
