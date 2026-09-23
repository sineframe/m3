"""Authenticated, session-scoped control transport for the Pi extension.

The native Pi process owns the extension's standard input and output, so those
streams are reserved for Pi's RPC protocol.  This module provides the private
M3 control path that the parent adapter and bundled extension can use while a
tool invocation is waiting for managed input.  It deliberately contains no
elicitation or retry policy; those remain owned by the bridge/coordinator.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import math
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

CONTROL_PROTOCOL_VERSION = 1
MAX_CONTROL_FRAME_BYTES = 64 * 1024
MAX_CONTROL_QUEUE = 32
CONTROL_TIMEOUT_SECONDS = 5.0
_MAX_RECEIVED_IDS = 128
_MAX_ID_LENGTH = 128
_MAX_NAME_LENGTH = 256
_MAX_REASON_LENGTH = 512
_MAX_OPAQUE_STATE_LENGTH = MAX_CONTROL_FRAME_BYTES
_MAX_RESPONSE_KEY_LENGTH = 256
MAX_CONTROL_ROUND_LIMIT = 1024
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_MESSAGE_TYPES = frozenset({"pending", "response", "cancel", "terminal", "close"})
_TERMINAL_STATES = frozenset({"delivered", "failed", "cancelled"})
_OPERATION_KINDS = frozenset({"tool", "prompt", "resource"})
# A delivered terminal is an extension/bridge acknowledgement: the parent
# never manufactures delivery success from its own socket drain.
_PARENT_TO_EXTENSION = frozenset({"response", "cancel", "close"})
_EXTENSION_TO_PARENT = frozenset({"pending", "cancel", "terminal", "close"})


class PiControlError(RuntimeError):
    """Base error for the private control channel."""


class PiControlProtocolError(PiControlError):
    """The peer sent an invalid or stale control envelope."""


class PiControlClosed(PiControlError):
    """The control channel is not connected or has been closed."""


@dataclass(frozen=True)
class PiControlScope:
    generation: str
    turn_sequence: int
    execution_id: str
    logical_operation_id: str
    round_id: str


def _text(value: Any, name: str, *, maximum: int = _MAX_ID_LENGTH) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise PiControlProtocolError(f"invalid control {name}")
    return value


def _opaque(
    value: Any, name: str, *, maximum: int = _MAX_OPAQUE_STATE_LENGTH
) -> str | None:
    if value is not None and (not isinstance(value, str) or len(value) > maximum):
        raise PiControlProtocolError(f"invalid control {name}")
    return value


def _sequence(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PiControlProtocolError("invalid control turn sequence")
    return int(value)


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PiControlProtocolError(f"invalid control {name}")
    for key in value:
        if not isinstance(key, str) or not key or len(key) > _MAX_RESPONSE_KEY_LENGTH:
            raise PiControlProtocolError(f"invalid control {name}")
    return value


def _scope_from(value: Mapping[str, Any]) -> PiControlScope:
    return PiControlScope(
        _text(value.get("generation"), "generation"),
        _sequence(value.get("turn_sequence")),
        _text(value.get("execution_id"), "execution id"),
        _text(value.get("logical_operation_id"), "logical operation id"),
        _text(value.get("round_id"), "round id"),
    )


def _timestamp(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    timestamp = _text(value, name, maximum=64)
    if not _TIMESTAMP.fullmatch(timestamp):
        raise PiControlProtocolError(f"invalid control {name}")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise PiControlProtocolError(f"invalid control {name}") from error
    if parsed.tzinfo is None:
        raise PiControlProtocolError(f"invalid control {name}")
    return timestamp


def validate_control_envelope(
    value: Any,
    *,
    session_id: str,
    scope: PiControlScope | None = None,
    hello: bool = False,
    direction: Literal["parent_to_extension", "extension_to_parent"] | None = None,
) -> dict[str, Any]:
    """Validate and return a bounded control envelope.

    Validation intentionally rejects unknown fields.  This keeps accidental
    provider content from becoming an undocumented control surface and makes
    protocol evolution explicit.
    """

    if not isinstance(value, Mapping):
        raise PiControlProtocolError("invalid control envelope")
    frame = dict(value)
    frame_type = frame.get("type")
    if frame_type == "hello":
        allowed = {"type", "version", "session_id", "token"}
        if not hello or set(frame) != allowed:
            raise PiControlProtocolError("invalid control hello")
        version = frame.get("version")
        if version != CONTROL_PROTOCOL_VERSION:
            raise PiControlProtocolError("unsupported control protocol")
        if _text(frame.get("session_id"), "session id") != session_id:
            raise PiControlProtocolError("control session mismatch")
        _text(frame.get("token"), "token", maximum=256)
        return frame

    if frame_type not in _MESSAGE_TYPES:
        raise PiControlProtocolError("unknown control message")
    if direction == "parent_to_extension" and frame_type not in _PARENT_TO_EXTENSION:
        raise PiControlProtocolError("control message is not valid in this direction")
    if direction == "extension_to_parent" and frame_type not in _EXTENSION_TO_PARENT:
        raise PiControlProtocolError("control message is not valid in this direction")
    if (
        "session_id" not in frame
        or _text(frame.get("session_id"), "session id") != session_id
    ):
        raise PiControlProtocolError("control session mismatch")
    if frame_type == "close":
        allowed = {"type", "session_id", "message_id", "reason"}
        if set(frame) != allowed:
            raise PiControlProtocolError("invalid control close")
        _text(frame.get("message_id"), "message id")
        _text(frame.get("reason"), "reason", maximum=_MAX_REASON_LENGTH)
        return frame

    if scope is None and frame_type != "pending":
        raise PiControlProtocolError("scoped control message has no active scope")

    common = {
        "type",
        "session_id",
        "message_id",
        "generation",
        "turn_sequence",
        "execution_id",
        "logical_operation_id",
        "round_id",
    }
    if frame_type == "pending":
        required = common | {
            "round_index",
            "round_limit",
            "server",
            "operation_kind",
            "operation_name",
            "request_state",
            "requests",
            "created_at",
            "deadline",
            "operation_parameters",
        }
        optional: set[str] = set()
    elif frame_type == "response":
        required = common | {"responses"}
        optional = {"response_idempotency_key"}
    elif frame_type == "cancel":
        required = common | {"reason"}
        optional = set()
    elif frame_type == "terminal":
        required = common | {"state"}
        optional = {"error_code"}
    if not required.issubset(frame) or set(frame) - required - optional:
        raise PiControlProtocolError("invalid control envelope")
    _text(frame.get("message_id"), "message id")
    current = _scope_from(frame)
    if scope is not None and current != scope:
        raise PiControlProtocolError("stale control scope")
    if frame_type == "pending":
        index = frame.get("round_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise PiControlProtocolError("invalid control round index")
        limit = frame.get("round_limit")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 0 < limit <= MAX_CONTROL_ROUND_LIMIT
        ):
            raise PiControlProtocolError("invalid control round limit")
        if index >= limit:
            raise PiControlProtocolError("control round index exceeds limit")
        _text(frame.get("execution_id"), "execution id")
        _text(frame.get("server"), "server", maximum=_MAX_NAME_LENGTH)
        if frame.get("operation_kind") not in _OPERATION_KINDS:
            raise PiControlProtocolError("invalid control operation kind")
        _text(frame.get("operation_name"), "operation name", maximum=_MAX_NAME_LENGTH)
        _opaque(frame.get("request_state"), "request state")
        requests = _mapping(frame.get("requests"), "requests")
        if not requests:
            raise PiControlProtocolError("control pending requests cannot be empty")
        _timestamp(frame.get("created_at"), "created at")
        _timestamp(frame.get("deadline"), "deadline", nullable=True)
        _mapping(frame.get("operation_parameters"), "operation parameters")
    elif frame_type == "response":
        _mapping(frame.get("responses"), "responses")
        key = frame.get("response_idempotency_key")
        if key is not None:
            _text(key, "response idempotency key")
    elif frame_type == "cancel":
        _text(frame.get("reason"), "reason", maximum=_MAX_REASON_LENGTH)
    else:
        state = frame.get("state")
        if state not in _TERMINAL_STATES:
            raise PiControlProtocolError("invalid control terminal state")
        error_code = frame.get("error_code")
        if error_code is not None:
            _text(error_code, "error code", maximum=_MAX_REASON_LENGTH)
    return frame


def _encode(frame: Mapping[str, Any]) -> bytes:
    try:
        encoded = (
            json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise PiControlProtocolError(
            "control envelope is not JSON serializable"
        ) from error
    if len(encoded) > MAX_CONTROL_FRAME_BYTES:
        raise PiControlProtocolError("control envelope is too large")
    return encoded


@dataclass
class _Connection:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    task: asyncio.Task[None]
    authenticated: bool = False


class PiControlChannel:
    """One authenticated loopback connection for one Pi adapter session."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        timeout: float = CONTROL_TIMEOUT_SECONDS,
        queue_size: int = MAX_CONTROL_QUEUE,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("Pi control channel must bind to IPv4 loopback")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Pi control timeout must be positive and finite")
        if (
            isinstance(queue_size, bool)
            or not isinstance(queue_size, int)
            or not 0 < queue_size <= MAX_CONTROL_QUEUE
        ):
            raise ValueError("Pi control queue size is out of bounds")
        self.host = host
        self.timeout = timeout
        self.session_id = uuid4().hex
        self.token = secrets.token_urlsafe(32)
        self._server: asyncio.AbstractServer | None = None
        self._connection: _Connection | None = None
        self._connection_seen = False
        self._connected = asyncio.Event()
        self._disconnected = asyncio.Event()
        self._closed = False
        self._scope: PiControlScope | None = None
        self._round_index: int | None = None
        self._response_sent = False
        self._operation_terminal = False
        self._received_ids: dict[str, None] = {}
        self._incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=queue_size
        )
        self._write_lock = asyncio.Lock()

    @property
    def port(self) -> int:
        sockets = getattr(self._server, "sockets", None)
        if sockets is None or not sockets:
            raise PiControlClosed("control channel is not started")
        address = sockets[0].getsockname()
        return int(address[1])

    @property
    def environment(self) -> dict[str, str]:
        return {
            "M3_PI_CONTROL_HOST": self.host,
            "M3_PI_CONTROL_PORT": str(self.port),
            "M3_PI_CONTROL_SESSION": self.session_id,
            "M3_PI_CONTROL_TOKEN": self.token,
        }

    @property
    def scope(self) -> PiControlScope | None:
        return self._scope

    def set_scope(
        self, scope: PiControlScope | None, *, round_index: int | None = None
    ) -> None:
        self._scope = scope
        self._round_index = round_index if scope is not None else None
        self._response_sent = False
        self._operation_terminal = False

    async def start(self) -> None:
        if self._server is not None:
            raise PiControlError("control channel is already started")
        self._server = await asyncio.start_server(self._accept, self.host, 0)

    async def wait_connected(self, timeout: float | None = None) -> None:
        if self._closed:
            raise PiControlClosed("control channel is closed")
        try:
            await asyncio.wait_for(self._connected.wait(), timeout or self.timeout)
        except asyncio.TimeoutError:
            raise PiControlClosed("control extension did not connect") from None

    async def wait_disconnected(self) -> None:
        """Wait until the authenticated extension connection is lost."""

        await self._disconnected.wait()
        raise PiControlClosed("control extension disconnected")

    async def receive(self, timeout: float | None = None) -> dict[str, Any]:
        if not self._incoming.empty():
            frame = self._incoming.get_nowait()
            if frame is None:
                raise PiControlClosed("control channel is closed")
            return frame
        if self._disconnected.is_set():
            raise PiControlClosed("control channel is closed")
        receive_task = asyncio.create_task(self._incoming.get())
        disconnected_task = asyncio.create_task(self._disconnected.wait())
        try:
            done, _ = await asyncio.wait(
                (receive_task, disconnected_task),
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise asyncio.TimeoutError
            if receive_task in done:
                frame = receive_task.result()
                if frame is None:
                    raise PiControlClosed("control channel is closed")
                return frame
            raise PiControlClosed("control channel is closed")
        finally:
            for task in (receive_task, disconnected_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                receive_task, disconnected_task, return_exceptions=True
            )

    async def send_response(
        self,
        *,
        generation: str,
        turn_sequence: int,
        execution_id: str,
        logical_operation_id: str,
        round_id: str,
        responses: Mapping[str, Any],
        response_idempotency_key: str | None = None,
    ) -> None:
        if self._response_sent:
            raise PiControlProtocolError(
                "response has already been sent for this round"
            )
        self._require_scope(
            PiControlScope(
                generation, turn_sequence, execution_id, logical_operation_id, round_id
            )
        )
        frame: dict[str, Any] = {
            "type": "response",
            "session_id": self.session_id,
            "message_id": uuid4().hex,
            "generation": generation,
            "turn_sequence": turn_sequence,
            "execution_id": execution_id,
            "logical_operation_id": logical_operation_id,
            "round_id": round_id,
            "responses": dict(responses),
        }
        if response_idempotency_key is not None:
            frame["response_idempotency_key"] = response_idempotency_key
        await self.send(frame)

    async def send_cancel(
        self,
        *,
        generation: str,
        turn_sequence: int,
        execution_id: str,
        logical_operation_id: str,
        round_id: str,
        reason: str,
    ) -> None:
        self._require_scope(
            PiControlScope(
                generation, turn_sequence, execution_id, logical_operation_id, round_id
            )
        )
        await self.send(
            {
                "type": "cancel",
                "session_id": self.session_id,
                "message_id": uuid4().hex,
                "generation": generation,
                "turn_sequence": turn_sequence,
                "execution_id": execution_id,
                "logical_operation_id": logical_operation_id,
                "round_id": round_id,
                "reason": reason,
            }
        )

    async def send(self, value: Mapping[str, Any]) -> None:
        if self._closed or self._connection is None:
            raise PiControlClosed("control extension is not connected")
        frame = validate_control_envelope(
            value,
            session_id=self.session_id,
            scope=self._scope,
            direction="parent_to_extension",
        )
        if frame["type"] == "response" and self._response_sent:
            raise PiControlProtocolError(
                "response has already been sent for this round"
            )
        if self._operation_terminal and frame["type"] != "close":
            raise PiControlProtocolError("control operation is terminal")
        encoded = _encode(frame)
        async with self._write_lock:
            connection = self._connection
            if connection is None or connection.writer.is_closing():
                raise PiControlClosed("control extension is not connected")
            try:
                connection.writer.write(encoded)
                await asyncio.wait_for(connection.writer.drain(), self.timeout)
                if frame["type"] == "response":
                    self._response_sent = True
                elif frame["type"] == "cancel":
                    self._clear_scope()
            except (ConnectionError, asyncio.TimeoutError) as error:
                await self._drop_connection(connection)
                raise PiControlClosed("control extension disconnected") from error

    async def close(self, reason: str = "adapter_closed") -> None:
        if self._closed:
            return
        self._closed = True
        self._disconnected.set()
        connection = self._connection
        if connection is not None and not connection.writer.is_closing():
            try:
                frame = {
                    "type": "close",
                    "session_id": self.session_id,
                    "message_id": uuid4().hex,
                    "reason": reason,
                }
                connection.writer.write(_encode(frame))
                await asyncio.wait_for(connection.writer.drain(), self.timeout)
            except (ConnectionError, asyncio.TimeoutError):
                pass
        if connection is not None:
            await self._drop_connection(connection)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        try:
            self._incoming.put_nowait(None)
        except asyncio.QueueFull:
            pass
        self.token = ""

    async def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._closed or self._connection is not None or self._connection_seen:
            writer.close()
            await writer.wait_closed()
            return
        task = asyncio.current_task()
        if task is None:
            writer.close()
            await writer.wait_closed()
            return
        connection = _Connection(reader, writer, task)
        self._connection = connection
        try:
            try:
                raw = await asyncio.wait_for(reader.readline(), self.timeout)
            except (asyncio.LimitOverrunError, ValueError) as error:
                raise PiControlProtocolError("invalid control hello") from error
            if not raw or not raw.endswith(b"\n") or len(raw) > MAX_CONTROL_FRAME_BYTES:
                raise PiControlProtocolError("invalid control hello")
            try:
                hello = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise PiControlProtocolError("invalid control hello") from error
            validate_control_envelope(hello, session_id=self.session_id, hello=True)
            supplied_token = str(hello["token"])
            if not hmac.compare_digest(supplied_token, self.token):
                raise PiControlProtocolError("control authentication failed")
            writer.write(
                _encode(
                    {
                        "type": "hello",
                        "version": CONTROL_PROTOCOL_VERSION,
                        "session_id": self.session_id,
                        "accepted": True,
                    }
                )
            )
            await asyncio.wait_for(writer.drain(), self.timeout)
            connection.authenticated = True
            self._connection_seen = True
            self._connected.set()
            while not self._closed:
                try:
                    raw = await reader.readline()
                except (asyncio.LimitOverrunError, ValueError) as error:
                    raise PiControlProtocolError("invalid control envelope") from error
                if not raw:
                    break
                if not raw.endswith(b"\n") or len(raw) > MAX_CONTROL_FRAME_BYTES:
                    raise PiControlProtocolError("control envelope is too large")
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise PiControlProtocolError("invalid control envelope") from error
                frame = validate_control_envelope(
                    value,
                    session_id=self.session_id,
                    scope=None if value.get("type") == "pending" else self._scope,
                    direction="extension_to_parent",
                )
                message_id = frame["message_id"]
                if message_id in self._received_ids:
                    raise PiControlProtocolError("duplicate control message")
                self._received_ids[message_id] = None
                if len(self._received_ids) > _MAX_RECEIVED_IDS:
                    oldest = next(iter(self._received_ids), None)
                    if oldest is not None:
                        del self._received_ids[oldest]
                if frame["type"] == "pending":
                    incoming_scope = _scope_from(frame)
                    incoming_index = frame["round_index"]
                    if self._scope is None:
                        if incoming_index != 0:
                            raise PiControlProtocolError(
                                "control round does not start at zero"
                            )
                    elif self._response_sent:
                        if (
                            self._round_index is None
                            or incoming_scope.generation != self._scope.generation
                            or incoming_scope.turn_sequence != self._scope.turn_sequence
                            or incoming_scope.execution_id != self._scope.execution_id
                            or incoming_scope.logical_operation_id
                            != self._scope.logical_operation_id
                            or incoming_scope.round_id == self._scope.round_id
                            or incoming_index != self._round_index + 1
                        ):
                            raise PiControlProtocolError(
                                "control round transition is stale"
                            )
                    elif self._operation_terminal:
                        if (
                            incoming_scope.generation != self._scope.generation
                            or incoming_scope.turn_sequence != self._scope.turn_sequence
                            or incoming_scope.execution_id != self._scope.execution_id
                            or incoming_scope.logical_operation_id
                            == self._scope.logical_operation_id
                            or incoming_index != 0
                        ):
                            raise PiControlProtocolError(
                                "control operation transition is stale"
                            )
                    else:
                        raise PiControlProtocolError(
                            "control round transition is not acknowledged"
                        )
                    self._scope = incoming_scope
                    self._round_index = incoming_index
                    self._response_sent = False
                    self._operation_terminal = False
                elif frame["type"] == "terminal":
                    # The future bridge emits delivered only after accepting
                    # the response; this transport merely orders that evidence.
                    if frame["state"] == "delivered" and not self._response_sent:
                        raise PiControlProtocolError(
                            "delivered terminal lacks response acceptance"
                        )
                    self._operation_terminal = True
                    self._response_sent = False
                elif frame["type"] == "cancel":
                    self._clear_scope()
                try:
                    await asyncio.wait_for(self._incoming.put(frame), self.timeout)
                except asyncio.TimeoutError as error:
                    raise PiControlProtocolError("control queue is full") from error
        except (PiControlError, ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            await self._drop_connection(connection, notify=connection.authenticated)

    async def _drop_connection(
        self, connection: _Connection, *, notify: bool = True
    ) -> None:
        if self._connection is not connection:
            return
        self._connection = None
        self._connected.clear()
        if connection.authenticated:
            self._disconnected.set()
        if not connection.writer.is_closing():
            connection.writer.close()
            try:
                await connection.writer.wait_closed()
            except ConnectionError:
                pass
        if notify and not self._closed:
            try:
                self._incoming.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def _require_scope(self, scope: PiControlScope) -> None:
        if self._scope != scope or self._operation_terminal:
            raise PiControlProtocolError("stale control scope")

    def _clear_scope(self) -> None:
        self._scope = None
        self._round_index = None
        self._response_sent = False
        self._operation_terminal = False


__all__ = [
    "CONTROL_PROTOCOL_VERSION",
    "MAX_CONTROL_FRAME_BYTES",
    "PiControlChannel",
    "PiControlClosed",
    "PiControlError",
    "PiControlProtocolError",
    "PiControlScope",
    "validate_control_envelope",
]
