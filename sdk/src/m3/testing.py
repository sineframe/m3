"""Deterministic MCP testing utilities built on the official server API."""

from __future__ import annotations

import asyncio
import hashlib as _hashlib
import inspect
import json
import math
import sys
import threading
from collections.abc import Callable as _Callable
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from dataclasses import replace as _replace
from pathlib import Path as _Path
from typing import (
    Any as _Any,
)
from typing import (
    ParamSpec as _ParamSpec,
)
from typing import (
    TypeAlias as _TypeAlias,
)
from typing import (
    TypeVar as _TypeVar,
)
from typing import (
    cast as _cast,
)
from typing import (
    overload as _overload,
)

from anyio import EndOfStream as _EndOfStream
from mcp import types as _types
from mcp.server.lowlevel import Server as _Server
from mcp.shared.message import SessionMessage as _SessionMessage

from .trace.redaction import (
    RedactionConfig as _RedactionConfig,
)
from .trace.redaction import (
    redact_for_persistence as _redact_for_persistence,
)
from .types import (
    InProcessServer as _InProcessServer,
)
from .types import (
    StdioServer as _StdioServer,
)

_JsonValue: _TypeAlias = (
    bool | int | float | str | list["_JsonValue"] | dict[str, "_JsonValue"] | None
)
_Handler: _TypeAlias = _Callable[..., _Any]
_P = _ParamSpec("_P")
_R = _TypeVar("_R")


# The invalid-result fault is deliberately a two-sided contract violation:
# the tool advertises this schema, then returns a string where an integer is
# required.  Keeping the fixture shape stable makes failures reproducible and
# prevents test data from becoming an error-message side channel.
_INVALID_RESULT_SCHEMA: dict[str, _Any] = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}
_INVALID_RESULT_VALUE = {"value": "invalid structured result"}


class MockExpectationError(AssertionError):
    """A scripted mock interaction did not match."""


class ReplayMismatch(MockExpectationError):
    """A strict replay request differs from the recorded interaction."""


class MockProtocolError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(message)


def _request_id_key(value: _Any) -> tuple[type[_Any], _Any] | None:
    """Keep JSON-RPC integer and string IDs distinct in fault bookkeeping."""

    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    return (type(value), value)


class _FaultState:
    def __init__(self, faults: FaultInjector) -> None:
        self.faults = faults
        self.methods: dict[tuple[type[_Any], _Any], str] = {}
        self.cancellations: dict[tuple[type[_Any], _Any], asyncio.Event] = {}
        self.closed = False
        self.output: _FaultWriteStream | None = None

    def observe_request(self, message: _Any) -> None:
        request_id = _request_id_key(getattr(message, "id", None))
        if request_id is not None and isinstance(message, _types.JSONRPCRequest):
            self.methods[request_id] = message.method
            self.cancellations.setdefault(request_id, asyncio.Event())
        if (
            isinstance(message, _types.JSONRPCNotification)
            and message.method == "notifications/cancelled"
        ):
            params = message.params or {}
            request_id = (
                _request_id_key(params.get("requestId"))
                if isinstance(params, _Mapping)
                else None
            )
            if request_id is not None:
                event = self.cancellations.setdefault(request_id, asyncio.Event())
                event.set()

    def method_for(self, message: _Any) -> str | None:
        request_id = _request_id_key(getattr(message, "id", None))
        return None if request_id is None else self.methods.get(request_id)


class _FaultReadStream:
    """Transparent official stream wrapper used for request/cancel observation."""

    def __init__(self, inner: _Any, state: _FaultState) -> None:
        self._inner = inner
        self._state = state

    async def receive(self) -> _Any:
        item = await self._inner.receive()
        if isinstance(item, _SessionMessage):
            self._state.observe_request(item.message)
            method = getattr(item.message, "method", None)
            request_id = _request_id_key(getattr(item.message, "id", None))
            if (
                isinstance(item.message, _types.JSONRPCRequest)
                and isinstance(method, str)
                and method in self._state.faults.cancel_before_methods
                and self._state.output is not None
            ):
                await self._state.output.abort()
                # Keep the official dispatcher alive long enough to observe
                # the closed response stream; the request is never dispatched.
                return _SessionMessage(
                    message=_types.JSONRPCNotification(
                        jsonrpc="2.0",
                        method="notifications/cancelled",
                        params={
                            "requestId": request_id[1]
                            if request_id is not None
                            else None
                        },
                    )
                )
        return item

    def __aiter__(self) -> _Any:
        return self

    async def __anext__(self) -> _Any:
        try:
            return await self.receive()
        except _EndOfStream:
            raise StopAsyncIteration from None
        except StopAsyncIteration:
            raise

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if callable(close):
            await close()

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()

    async def __aenter__(self) -> _FaultReadStream:
        return self

    async def __aexit__(self, *_args: _Any) -> None:
        await self.aclose()


class _FaultWriteStream:
    """Apply response-frame faults without replacing MCP's dispatcher."""

    def __init__(self, inner: _Any, state: _FaultState) -> None:
        self._inner = inner
        self._state = state
        self._pending: _SessionMessage | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def _send_inner(self, item: _Any) -> None:
        await self._inner.send(item)

    async def _flush_later(self) -> None:
        await asyncio.sleep(self._state.faults.reorder_window)
        async with self._lock:
            item, self._pending = self._pending, None
            if item is not None:
                await self._send_inner(item)

    async def _send_response(self, item: _SessionMessage, method: str | None) -> None:
        message = item.message
        is_response = isinstance(message, (_types.JSONRPCResponse, _types.JSONRPCError))
        if not is_response or method is None:
            await self._send_inner(item)
            return
        if (
            method in self._state.faults.malformed_methods
            or method in self._state.faults.partial_methods
        ):
            # The official in-process contract transports SessionMessage
            # objects, not bytes.  Inject an exception item and close the
            # stream, which exercises the client's malformed/partial-frame
            # termination path without fabricating JSON text.
            await self._send_inner(ValueError("injected malformed MCP frame"))
            self._state.closed = True
            close = getattr(self._inner, "aclose", None)
            if callable(close):
                await close()
            return
        if method in self._state.faults.disconnect_methods:
            self._state.closed = True
            close = getattr(self._inner, "aclose", None)
            if callable(close):
                await close()
            return
        race_gate = self._state.faults.race_gates.get(method)
        if race_gate is not None:
            request_id = _request_id_key(getattr(message, "id", None))
            cancelled = (
                self._state.cancellations.get(request_id)
                if request_id is not None
                else None
            )
            if cancelled is not None:
                cancelled_task = asyncio.create_task(cancelled.wait())
                opened_task = asyncio.create_task(race_gate.wait_async())
                done, pending = await asyncio.wait(
                    (cancelled_task, opened_task), return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if cancelled_task in done and cancelled.is_set():
                    return
        if method in self._state.faults.protocol_errors:
            code, error_message = self._state.faults.protocol_errors[method]
            item = _SessionMessage(
                message=_types.JSONRPCError(
                    jsonrpc="2.0",
                    id=getattr(message, "id", None),
                    error=_types.ErrorData(code=code, message=error_message),
                ),
                metadata=item.metadata,
            )
        if method in self._state.faults.invalid_result_methods and isinstance(
            message, _types.JSONRPCResponse
        ):
            # The official server validates outputSchema before producing its
            # response.  Apply this fault at the decoded transport boundary,
            # after that validation, so clients receive a real response whose
            # structuredContent violates the valid advertised schema.
            result = dict(message.result)
            result["structuredContent"] = dict(_INVALID_RESULT_VALUE)
            item = _SessionMessage(
                message=_types.JSONRPCResponse(
                    jsonrpc="2.0", id=message.id, result=result
                ),
                metadata=item.metadata,
            )
        if method in self._state.faults.reordered_methods:
            if self._pending is None:
                self._pending = item
                self._flush_task = asyncio.create_task(self._flush_later())
                return
            first, self._pending = self._pending, None
            if self._flush_task is not None:
                self._flush_task.cancel()
            await self._send_inner(item)
            await self._send_inner(first)
            return
        await self._send_inner(item)
        if method in self._state.faults.duplicate_id_methods:
            await self._send_inner(item)

    async def send(self, item: _SessionMessage) -> None:
        async with self._lock:
            await self._send_response(item, self._state.method_for(item.message))

    async def aclose(self) -> None:
        async with self._lock:
            if self._flush_task is not None:
                self._flush_task.cancel()
            if self._pending is not None:
                await self._send_inner(self._pending)
                self._pending = None
            close = getattr(self._inner, "aclose", None)
            if callable(close):
                await close()

    async def abort(self) -> None:
        self._state.closed = True
        close = getattr(self._inner, "aclose", None)
        if callable(close):
            await close()

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()

    async def __aenter__(self) -> _FaultWriteStream:
        return self

    async def __aexit__(self, *_args: _Any) -> None:
        await self.aclose()


class _FaultingServer:
    """Proxy an official low-level Server while preserving its protocol engine."""

    def __init__(self, server: _Server, faults: FaultInjector) -> None:
        self._server = server
        self._faults = faults

    def create_initialization_options(self) -> _Any:
        return self._server.create_initialization_options()

    async def run(
        self,
        read_stream: _Any,
        write_stream: _Any,
        options: _Any,
        *,
        raise_exceptions: bool = False,
    ) -> None:
        state = _FaultState(self._faults)
        output = _FaultWriteStream(write_stream, state)
        state.output = output
        await self._server.run(
            _FaultReadStream(read_stream, state),
            output,
            options,
            raise_exceptions=raise_exceptions,
        )


class ArtifactIntegrityError(ValueError):
    """A recorded artifact does not match its content-addressed metadata."""


@_dataclass(frozen=True, slots=True)
class RecordedArtifact:
    """Content-addressed artifact metadata carried by a recording."""

    artifact_id: str
    sha256: str
    size_bytes: int
    media_type: str | None = None

    @classmethod
    def from_bytes(
        cls, artifact_id: str, content: bytes, *, media_type: str | None = None
    ) -> RecordedArtifact:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("artifact id must be a non-empty string")
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        return cls(
            artifact_id, _hashlib.sha256(content).hexdigest(), len(content), media_type
        )

    def validate(self, content: bytes) -> None:
        if not isinstance(content, bytes):
            raise ArtifactIntegrityError("artifact content is not bytes")
        digest = _hashlib.sha256(content).hexdigest()
        if len(content) != self.size_bytes or digest != self.sha256:
            raise ArtifactIntegrityError("recorded artifact integrity check failed")

    def model(self) -> dict[str, _JsonValue]:
        value: dict[str, _JsonValue] = {
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }
        if self.media_type is not None:
            value["media_type"] = self.media_type
        return value

    def __repr__(self) -> str:
        return (
            "RecordedArtifact("
            f"sha256={self.sha256!r}, size_bytes={self.size_bytes}, media_type={self.media_type!r})"
        )


@_dataclass(frozen=True, slots=True, repr=False)
class RedactionBinding:
    """Serializable redaction provenance without secret values."""

    source: str = "explicit"
    version: str = "1"
    wildcard_paths: tuple[str, ...] = ()
    secret_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source or not self.version:
            raise ValueError("redaction binding source and version are required")
        object.__setattr__(self, "wildcard_paths", tuple(self.wildcard_paths))
        object.__setattr__(self, "secret_references", tuple(self.secret_references))

    def model(self) -> dict[str, _JsonValue]:
        return {
            "source": self.source,
            "version": self.version,
            "wildcard_paths": list(self.wildcard_paths),
            "references": list(self.secret_references),
        }

    def __repr__(self) -> str:
        return (
            "RedactionBinding("
            f"source={self.source!r}, version={self.version!r}, "
            f"wildcard_paths={len(self.wildcard_paths)}, "
            f"secret_references={len(self.secret_references)})"
        )


class _RedactionRuntime:
    """Opaque in-process config holder excluded from JSON and safe reprs."""

    __slots__ = ("bound", "config")

    def __init__(
        self, config: _RedactionConfig | None = None, *, bound: bool = False
    ) -> None:
        self.config = config or _RedactionConfig(include_environment=False)
        self.bound = bound

    def __repr__(self) -> str:
        return "_RedactionRuntime(<opaque>)"


def _json_safe(
    value: _Any, *, _active: set[int] | None = None, _depth: int = 0
) -> _JsonValue:
    """Convert only bounded, ordinary values; never expose hostile failures.

    Recordings are persisted and replayed as data.  In particular, this helper
    must not invoke an object's ``repr`` or propagate an exception raised by a
    custom mapping/model with its value-bearing message.
    """
    if _depth > 64:
        raise TypeError("mock value nesting exceeds the recording limit")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("mock values must contain finite JSON numbers")
        return value
    active = _active if _active is not None else set()
    container = isinstance(value, (_Mapping, list, tuple))
    marker = id(value) if container else None
    if marker is not None:
        if marker in active:
            raise TypeError("mock value contains a cycle")
        active.add(marker)
    try:
        if isinstance(value, _Mapping):
            result: dict[str, _JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("mock mapping keys must be strings")
                if key in result:
                    raise TypeError("mock mapping contains duplicate keys")
                result[key] = _json_safe(item, _active=active, _depth=_depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            if len(value) > 100_000:
                raise TypeError("mock sequence exceeds the recording limit")
            return [
                _json_safe(item, _active=active, _depth=_depth + 1) for item in value
            ]
        try:
            model_dump = value.model_dump
            if not callable(model_dump):
                raise TypeError(
                    "mock values must be JSON-compatible or Pydantic models"
                )
            dumped = model_dump(mode="json", by_alias=True)
        except BaseException:
            raise TypeError("mock model could not be serialized safely") from None
        return _json_safe(dumped, _active=active, _depth=_depth + 1)
    except BaseException:
        raise TypeError("mock value could not be serialized safely") from None
    finally:
        if marker is not None:
            active.discard(marker)


def _normalize(value: _Any) -> _JsonValue:
    safe = _json_safe(value)
    if isinstance(safe, list):
        return [_normalize(item) for item in safe]
    if isinstance(safe, dict):
        return {
            key: _normalize(item)
            for key, item in safe.items()
            if key not in {"id", "requestId", "timestamp", "createdAt", "updatedAt"}
        }
    return safe


def _subset(expected: _Any, actual: _Any) -> bool:
    if isinstance(expected, _Mapping):
        return isinstance(actual, _Mapping) and all(
            key in actual and _subset(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(_subset(item, candidate) for candidate in actual) for item in expected
        )
    return bool(expected == actual)


def _page_params(params: _Any) -> dict[str, _JsonValue]:
    cursor = getattr(params, "cursor", None)
    return {} if cursor is None else {"cursor": str(cursor)}


class Gate:
    """Deterministic async gate for delay and hang tests."""

    def __init__(self, *, open: bool = False) -> None:
        self._event = threading.Event()
        if open:
            self._event.set()

    @property
    def is_open(self) -> bool:
        return self._event.is_set()

    def open(self) -> None:
        self._event.set()

    release = open

    def close(self) -> None:
        self._event.clear()

    async def wait(self, *, poll_seconds: float = 0.001) -> None:
        while not self._event.is_set():
            await asyncio.sleep(poll_seconds)

    async def wait_async(self, *, poll_seconds: float = 0.001) -> None:
        """Alias with an explicit name for composing deterministic races."""

        await self.wait(poll_seconds=poll_seconds)


class VirtualClock:
    """A process-local virtual clock with awaitable deterministic sleeps."""

    def __init__(self, start: float = 0.0) -> None:
        self._value = float(start)
        self._condition = asyncio.Condition()

    @property
    def now(self) -> float:
        return self._value

    async def sleep(self, duration: float) -> None:
        if duration < 0:
            raise ValueError("clock duration must not be negative")
        target = self._value + duration
        async with self._condition:
            await self._condition.wait_for(lambda: self._value >= target)

    async def advance(self, duration: float) -> float:
        if duration < 0:
            raise ValueError("clock duration must not be negative")
        async with self._condition:
            self._value += duration
            self._condition.notify_all()
            return self._value


@_dataclass(frozen=True, slots=True)
class RecordedInteraction:
    method: str
    params: _Mapping[str, _JsonValue]
    response: _JsonValue | None = None
    error: _Mapping[str, _JsonValue] | None = None
    sequence: int = 0
    provenance: tuple[str, ...] = ()

    def model(self) -> dict[str, _JsonValue]:
        value: dict[str, _JsonValue] = {
            "method": self.method,
            "params": dict(self.params),
            "sequence": self.sequence,
            "provenance": list(self.provenance),
        }
        if self.response is not None:
            value["response"] = self.response
        if self.error is not None:
            value["error"] = dict(self.error)
        return value

    def __repr__(self) -> str:
        return (
            "RecordedInteraction("
            f"method={self.method!r}, sequence={self.sequence}, "
            f"has_response={self.response is not None}, has_error={self.error is not None})"
        )


@_dataclass(frozen=True, slots=True)
class Recording:
    interactions: tuple[RecordedInteraction, ...]
    redaction_bound: bool
    provenance: tuple[str, ...] = ()
    server_name: str = "mock-mcp"
    server_version: str = "1"
    initialization: _Mapping[str, _JsonValue] = _field(default_factory=dict)
    artifacts: tuple[RecordedArtifact, ...] = ()
    redaction_bindings: tuple[RedactionBinding, ...] = _field(
        default_factory=lambda: (RedactionBinding(source="legacy-unverified"),)
    )
    _runtime: _RedactionRuntime = _field(
        default_factory=_RedactionRuntime, repr=False, compare=False
    )

    def __repr__(self) -> str:
        return (
            "Recording("
            f"interactions={len(self.interactions)}, redaction_bound={self.redaction_bound}, "
            f"artifacts={len(self.artifacts)}, provenance={len(self.provenance)})"
        )

    def with_redaction_config(self, config: _RedactionConfig) -> Recording:
        """Bind an explicit in-process redaction config without serializing secrets."""
        if not isinstance(config, _RedactionConfig):
            raise TypeError("recording redaction config is invalid")
        projected = self.model_dump(config=config)
        safe = type(self).from_json(
            json.dumps(projected, sort_keys=True, separators=(",", ":"))
        )
        return _replace(safe, _runtime=_RedactionRuntime(config, bound=True))

    def validate_artifacts(self, contents: _Mapping[str, bytes]) -> None:
        """Validate every supplied artifact against the recorded digest/length."""
        if not isinstance(contents, _Mapping):
            raise ArtifactIntegrityError("artifact contents are not a mapping")
        expected = {artifact.artifact_id: artifact for artifact in self.artifacts}
        if len(expected) != len(self.artifacts) or set(contents) != set(expected):
            raise ArtifactIntegrityError(
                "recorded artifact set does not match supplied contents"
            )
        for artifact_id, artifact in expected.items():
            try:
                artifact.validate(contents[artifact_id])
            except (ArtifactIntegrityError, KeyError):
                raise ArtifactIntegrityError(
                    "recorded artifact integrity check failed"
                ) from None

    def model_dump(
        self, *, config: _RedactionConfig | None = None
    ) -> dict[str, _JsonValue]:
        if config is None and self.redaction_bound and not self._runtime.bound:
            raise ValueError(
                "recording requires an explicit redaction config before serialization"
            )
        raw: dict[str, _JsonValue] = {
            "schema": "m3.mock_recording.v1",
            "redaction_bound": self.redaction_bound,
            "provenance": list(self.provenance),
            "server": {"name": self.server_name, "version": self.server_version},
            "initialization": dict(self.initialization),
            "artifacts": [artifact.model() for artifact in self.artifacts],
            "interactions": [item.model() for item in self.interactions],
            "redaction_bindings": [
                binding.model() for binding in self.redaction_bindings
            ],
        }
        projected = _redact_for_persistence(
            raw, config=config or self._runtime.config, path="$.recording"
        )
        if not isinstance(projected, dict):
            raise ValueError("recording projection is invalid")
        return _cast(dict[str, _JsonValue], projected)

    def to_json(self, *, config: _RedactionConfig | None = None) -> str:
        return json.dumps(
            self.model_dump(config=config), sort_keys=True, separators=(",", ":")
        )

    def redacted(self, *, config: _RedactionConfig | None = None) -> Recording:
        """Return a detached recording whose persisted values are redacted."""
        projected = self.model_dump(config=config)
        return type(self).from_json(
            json.dumps(projected, sort_keys=True, separators=(",", ":"))
        )

    @classmethod
    def from_json(cls, value: str) -> Recording:
        if not isinstance(value, str) or len(value) > 16 * 1024 * 1024:
            raise ValueError("recording JSON is invalid or too large")

        def reject_constant(_constant: str) -> None:
            raise ValueError("recording JSON contains a non-finite number")

        try:
            document = json.loads(value, parse_constant=reject_constant)
        except (TypeError, ValueError, RecursionError, OverflowError):
            raise ValueError("recording is not valid JSON") from None
        if (
            not isinstance(document, _Mapping)
            or document.get("schema") != "m3.mock_recording.v1"
        ):
            raise ValueError("recording schema is unsupported")
        if set(document) - {
            "schema",
            "redaction_bound",
            "provenance",
            "server",
            "initialization",
            "artifacts",
            "interactions",
            "redaction_bindings",
        }:
            raise ValueError("recording contains unknown fields")
        if not isinstance(document.get("redaction_bound"), bool):
            raise ValueError("recording redaction binding is invalid")
        raw_provenance = document.get("provenance", [])
        if not isinstance(raw_provenance, list) or not all(
            isinstance(part, str) for part in raw_provenance
        ):
            raise ValueError("recording provenance is invalid")
        raw_server = document.get("server", {"name": "mock-mcp", "version": "1"})
        if not isinstance(raw_server, _Mapping) or set(raw_server) - {
            "name",
            "version",
        }:
            raise ValueError("recording server identity is invalid")
        server_name = raw_server.get("name")
        server_version = raw_server.get("version")
        if (
            not isinstance(server_name, str)
            or not isinstance(server_version, str)
            or not server_name
            or not server_version
        ):
            raise ValueError("recording server identity is invalid")
        raw_bindings = document.get("redaction_bindings")
        if raw_bindings is None:
            if document["redaction_bound"]:
                raise ValueError("recording redaction bindings are missing")
            raw_bindings = []
        if not isinstance(raw_bindings, list) or len(raw_bindings) > 256:
            raise ValueError("recording redaction bindings are invalid")
        bindings: list[RedactionBinding] = []
        for raw_binding in raw_bindings:
            if not isinstance(raw_binding, _Mapping) or set(raw_binding) - {
                "source",
                "version",
                "wildcard_paths",
                "references",
            }:
                raise ValueError("recording redaction binding is invalid")
            source = raw_binding.get("source")
            version = raw_binding.get("version")
            wildcard_paths = raw_binding.get("wildcard_paths", [])
            secret_references = raw_binding.get("references", [])
            if (
                not isinstance(source, str)
                or not source
                or not isinstance(version, str)
                or not version
                or not isinstance(wildcard_paths, list)
                or not all(isinstance(item, str) for item in wildcard_paths)
                or not isinstance(secret_references, list)
                or not all(isinstance(item, str) for item in secret_references)
            ):
                raise ValueError("recording redaction binding is invalid")
            bindings.append(
                RedactionBinding(
                    source, version, tuple(wildcard_paths), tuple(secret_references)
                )
            )

        def safe_document(raw: _Any) -> _JsonValue:
            try:
                return _json_safe(raw)
            except BaseException:
                raise ValueError("recording payload is invalid") from None

        raw_initialization = document.get("initialization", {})
        initialization = safe_document(raw_initialization)
        if not isinstance(initialization, dict):
            raise ValueError("recording initialization is invalid")
        raw_artifacts = document.get("artifacts", [])
        if not isinstance(raw_artifacts, list) or len(raw_artifacts) > 100_000:
            raise ValueError("recording artifacts are invalid")
        artifacts: list[RecordedArtifact] = []
        artifact_ids: set[str] = set()
        for raw_artifact in raw_artifacts:
            if not isinstance(raw_artifact, _Mapping) or set(raw_artifact) - {
                "artifact_id",
                "sha256",
                "size_bytes",
                "media_type",
            }:
                raise ValueError("recording artifact is invalid")
            artifact_id = raw_artifact.get("artifact_id")
            sha256 = raw_artifact.get("sha256")
            size_bytes = raw_artifact.get("size_bytes")
            media_type = raw_artifact.get("media_type")
            if (
                not isinstance(artifact_id, str)
                or not artifact_id
                or not isinstance(sha256, str)
                or len(sha256) != 64
                or any(character not in "0123456789abcdef" for character in sha256)
                or isinstance(size_bytes, bool)
                or not isinstance(size_bytes, int)
                or size_bytes < 0
                or (media_type is not None and not isinstance(media_type, str))
            ):
                raise ValueError("recording artifact is invalid")
            if artifact_id in artifact_ids:
                raise ValueError("recording artifact is invalid")
            artifact_ids.add(artifact_id)
            artifacts.append(
                RecordedArtifact(artifact_id, sha256, size_bytes, media_type)
            )
        raw_items = document.get("interactions")
        if not isinstance(raw_items, list) or len(raw_items) > 100_000:
            raise ValueError("recording interactions are invalid")
        interactions: list[RecordedInteraction] = []
        sequences: set[int] = set()
        for item in raw_items:
            if not isinstance(item, _Mapping):
                raise ValueError("recording interaction is invalid")
            if set(item) - {
                "method",
                "params",
                "response",
                "error",
                "sequence",
                "provenance",
            }:
                raise ValueError("recording interaction contains unknown fields")
            if not isinstance(item.get("method"), str) or not isinstance(
                item.get("params"), _Mapping
            ):
                raise ValueError("recording interaction is invalid")
            params = safe_document(dict(item["params"]))
            if not isinstance(params, dict):
                raise ValueError("recording interaction parameters are invalid")
            raw_response = item.get("response")
            response = None if raw_response is None else safe_document(raw_response)
            raw_error = item.get("error")
            if raw_error is not None and not isinstance(raw_error, _Mapping):
                raise ValueError("recording interaction error is invalid")
            error = None if raw_error is None else safe_document(dict(raw_error))
            if error is not None and not isinstance(error, dict):
                raise ValueError("recording interaction error is invalid")
            raw_sequence = item.get("sequence", len(interactions))
            if (
                isinstance(raw_sequence, bool)
                or not isinstance(raw_sequence, int)
                or raw_sequence != len(interactions)
            ):
                raise ValueError("recording interaction sequence is invalid")
            if raw_sequence in sequences:
                raise ValueError("recording interaction sequence is invalid")
            sequences.add(raw_sequence)
            interaction_provenance = item.get("provenance", [])
            if not isinstance(interaction_provenance, list) or not all(
                isinstance(part, str) for part in interaction_provenance
            ):
                raise ValueError("recording interaction provenance is invalid")
            interactions.append(
                RecordedInteraction(
                    method=item["method"],
                    params=_cast(_Mapping[str, _JsonValue], params),
                    response=response,
                    error=_cast(_Mapping[str, _JsonValue] | None, error),
                    sequence=raw_sequence,
                    provenance=tuple(interaction_provenance),
                )
            )
        return cls(
            interactions=tuple(interactions),
            redaction_bound=document["redaction_bound"],
            provenance=tuple(raw_provenance),
            server_name=server_name,
            server_version=server_version,
            initialization=_cast(_Mapping[str, _JsonValue], initialization),
            artifacts=tuple(artifacts),
            redaction_bindings=tuple(bindings),
        )


@_dataclass(slots=True)
class FaultInjector:
    """Deterministic faults for decoded in-process or literal stdio fixtures.

    ``in_process()`` applies faults after the official transport has decoded
    newline frames into ``SessionMessage`` objects. Use ``stdio_server()`` for
    literal malformed/partial bytes and process-exit faults.
    """

    delays: dict[str, float] = _field(default_factory=dict)
    gates: dict[str, Gate] = _field(default_factory=dict)
    disconnect_methods: set[str] = _field(default_factory=set)
    malformed_methods: set[str] = _field(default_factory=set)
    partial_methods: set[str] = _field(default_factory=set)
    invalid_schema_methods: set[str] = _field(default_factory=set)
    invalid_result_methods: set[str] = _field(default_factory=set)
    protocol_errors: dict[str, tuple[int, str]] = _field(default_factory=dict)
    oversized_methods: dict[str, int] = _field(default_factory=dict)
    reordered_methods: set[str] = _field(default_factory=set)
    duplicate_id_methods: set[str] = _field(default_factory=set)
    cancel_before_methods: set[str] = _field(default_factory=set)
    process_crash_methods: set[str] = _field(default_factory=set)
    race_gates: dict[str, Gate] = _field(default_factory=dict)
    reorder_window: float = 0.01
    _http_close: _Callable[[], None] | None = _field(
        default=None, init=False, repr=False
    )

    def delay(self, method: str, seconds: float) -> FaultInjector:
        if seconds < 0:
            raise ValueError("fault delay must not be negative")
        self.delays[method] = seconds
        return self

    def hang(self, method: str, gate: Gate | None = None) -> Gate:
        selected = gate or Gate()
        self.gates[method] = selected
        return selected

    def disconnect(self, method: str) -> FaultInjector:
        self.disconnect_methods.add(method)
        return self

    def malformed(self, method: str) -> FaultInjector:
        self.malformed_methods.add(method)
        return self

    def partial_frame(self, method: str) -> FaultInjector:
        self.partial_methods.add(method)
        return self

    def invalid_schema(self, method: str) -> FaultInjector:
        self.invalid_schema_methods.add(method)
        return self

    def invalid_result(self, method: str) -> FaultInjector:
        """Advertise an output schema and return a violating structured result.

        This is intentionally separate from :meth:`invalid_schema`: the
        latter corrupts the advertised input schema itself, while this fault
        keeps the advertised schema valid and corrupts the subsequent result.
        ``method`` is normally ``tools/call``.
        """

        self.invalid_result_methods.add(method)
        return self

    def duplicate_id(self, method: str) -> FaultInjector:
        self.duplicate_id_methods.add(method)
        return self

    def reorder(self, method: str) -> FaultInjector:
        self.reordered_methods.add(method)
        return self

    def oversized(self, method: str, size: int) -> FaultInjector:
        if size < 1 or size > 64 * 1024 * 1024:
            raise ValueError("oversized payload size must be between 1 and 67108864")
        self.oversized_methods[method] = size
        return self

    def cancel_before_dispatch(self, method: str) -> FaultInjector:
        self.cancel_before_methods.add(method)
        return self

    def process_crash(self, method: str) -> FaultInjector:
        """Exit the dedicated stdio fixture with a non-zero status."""

        self.process_crash_methods.add(method)
        return self

    def cancellation_race(self, method: str) -> FaultInjector:
        self.race_gates[method] = Gate()
        return self

    def race_gate(self, method: str) -> Gate:
        """Return the gate deciding whether a raced response is released."""

        return self.race_gates.setdefault(method, Gate())

    def protocol_error(
        self, method: str, code: int = -32000, message: str = "mock protocol error"
    ) -> FaultInjector:
        if not isinstance(code, int) or not isinstance(message, str) or not message:
            raise ValueError("fault protocol errors require a code and message")
        self.protocol_errors[method] = (code, message)
        return self

    def stdio_server(self, *, name: str = "wire-fault") -> _StdioServer:
        """Return a safe stdio fixture that applies literal wire faults.

        The fixture uses only the package's own module and a bounded JSON
        configuration. It is intentionally separate from ``in_process()``:
        stdio is the transport where newline framing and process exit are
        observable as bytes and exit status.
        """

        configuration = {
            "delays": self.delays,
            "malformed_methods": sorted(self.malformed_methods),
            "partial_methods": sorted(self.partial_methods),
            "invalid_result_methods": sorted(self.invalid_result_methods),
            "disconnect_methods": sorted(self.disconnect_methods),
            "duplicate_id_methods": sorted(self.duplicate_id_methods),
            "reordered_methods": sorted(self.reordered_methods),
            "cancel_before_methods": sorted(self.cancel_before_methods),
            "process_crash_methods": sorted(self.process_crash_methods),
            "race_methods": sorted(self.race_gates),
            "oversized_methods": self.oversized_methods,
            "protocol_errors": {
                method: {"code": code, "message": message}
                for method, (code, message) in self.protocol_errors.items()
            },
        }
        source_root = str(_Path(__file__).resolve().parents[1])
        environment = {
            "M3_WIRE_FAULTS": json.dumps(
                configuration, sort_keys=True, separators=(",", ":")
            ),
            "PYTHONPATH": source_root,
        }
        return _StdioServer(
            name=name,
            command=sys.executable,
            args=("-m", "m3.testing_wire"),
            environment=environment,
            cwd=source_root,
        )

    def close_fixture(self) -> None:
        close, self._http_close = self._http_close, None
        if close is not None:
            close()

    async def before(self, method: str) -> None:
        if method in self.delays:
            await asyncio.sleep(self.delays[method])
        if method in self.gates:
            await self.gates[method].wait()
        if method in self.cancel_before_methods:
            raise asyncio.CancelledError

    def response(self, method: str, value: _Any) -> _Any:
        """Apply deterministic response-shape faults after handler output."""

        if method in self.oversized_methods:
            size = self.oversized_methods[method]
            if isinstance(value, _types.CallToolResult):
                value = value.model_copy(
                    update={
                        "content": [*value.content, _types.TextContent(text="x" * size)]
                    }
                )
            elif isinstance(value, _types.ReadResourceResult):
                value = value.model_copy(
                    update={
                        "contents": [
                            *value.contents,
                            _types.TextResourceContents(
                                uri="fault://oversized",
                                mime_type="text/plain",
                                text="x" * size,
                            ),
                        ]
                    }
                )
        return value


@_dataclass(slots=True)
class ExpectedCall:
    method: str
    matcher: _Mapping[str, _Any] = _field(default_factory=dict)
    response: _Any = None
    optional_call: bool = False
    minimum: int = 1
    maximum: int | None = 1
    subset_match: bool = False
    unordered_group: str | None = None
    fallback_call: bool = False
    calls: int = 0

    def optional(self) -> ExpectedCall:
        self.optional_call, self.minimum = True, 0
        return self

    def repeated(self, minimum: int = 0, maximum: int | None = None) -> ExpectedCall:
        if minimum < 0 or (maximum is not None and maximum < minimum):
            raise ValueError("invalid expectation repetition bounds")
        self.minimum, self.maximum = minimum, maximum
        return self

    def subset(self) -> ExpectedCall:
        self.subset_match = True
        return self

    def unordered(self, group: str = "default") -> ExpectedCall:
        self.unordered_group = group
        return self

    def fallback(self) -> ExpectedCall:
        self.fallback_call = True
        return self

    def returns(self, value: _Any) -> ExpectedCall:
        self.response = value
        return self


class MockMCPServer:
    """Decorator-defined and stateful official in-process MCP server."""

    _CONTROL_METHODS = frozenset({"initialize", "notifications/initialized", "ping"})

    def __init__(
        self,
        name: str = "mock-mcp",
        *,
        version: str = "1",
        strict: bool = True,
        redaction_config: _RedactionConfig | None = None,
        faults: FaultInjector | None = None,
        state: _Mapping[str, _Any] | None = None,
    ) -> None:
        self.name, self.version, self.strict = name, version, strict
        self.state: dict[str, _Any] = dict(state or {})
        self.faults = faults or FaultInjector()
        self._redaction_config = redaction_config or _RedactionConfig.from_environment()
        self._tools: dict[str, tuple[_types.Tool, _Handler | None]] = {}
        self._resources: dict[str, tuple[_types.Resource, _Handler | None]] = {}
        self._resource_templates: dict[str, _types.ResourceTemplate] = {}
        self._prompts: dict[str, tuple[_types.Prompt, _Handler | None]] = {}
        self._expectations: list[ExpectedCall] = []
        self._recording: list[RecordedInteraction] = []
        self._sequence = 0
        self._closed = False

    @_overload
    def tool(
        self,
        name: str | None = None,
        *,
        description: str | None = None,
        input_schema: _Mapping[str, _Any] | None = None,
        output_schema: _Mapping[str, _Any] | None = None,
    ) -> _Callable[[_Callable[_P, _R]], _Callable[_P, _R]]: ...

    @_overload
    def tool(
        self,
        name: _Callable[_P, _R],
        *,
        description: str | None = None,
        input_schema: _Mapping[str, _Any] | None = None,
        output_schema: _Mapping[str, _Any] | None = None,
    ) -> _Callable[_P, _R]: ...

    def tool(
        self,
        name: str | _Callable[..., _Any] | None = None,
        *,
        description: str | None = None,
        input_schema: _Mapping[str, _Any] | None = None,
        output_schema: _Mapping[str, _Any] | None = None,
    ) -> _Any:
        def register(function: _Callable[_P, _R]) -> _Callable[_P, _R]:
            selected = name if isinstance(name, str) else function.__name__
            self._tools[selected] = (
                _types.Tool(
                    name=selected,
                    description=description,
                    input_schema=dict(input_schema or {"type": "object"}),
                    output_schema=dict(output_schema)
                    if output_schema is not None
                    else None,
                ),
                function,
            )
            return function

        if callable(name) and not isinstance(name, str):
            return register(name)
        return register

    def resource(
        self, uri: str, *, name: str | None = None, mime_type: str | None = None
    ) -> _Callable[[_Callable[_P, _R]], _Callable[_P, _R]]:
        def register(function: _Callable[_P, _R]) -> _Callable[_P, _R]:
            self._resources[uri] = (
                _types.Resource(name=name or uri, uri=uri, mime_type=mime_type),
                function,
            )
            return function

        return register

    def resource_template(
        self,
        uri_template: str,
        *,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
    ) -> _types.ResourceTemplate:
        """Register a resource template exposed by ``resources/templates/list``."""
        selected = name or uri_template
        template = _types.ResourceTemplate(
            name=selected,
            uri_template=uri_template,
            description=description,
            mime_type=mime_type,
        )
        self._resource_templates[selected] = template
        return template

    @_overload
    def prompt(
        self, name: str | None = None, *, description: str | None = None
    ) -> _Callable[[_Callable[_P, _R]], _Callable[_P, _R]]: ...

    @_overload
    def prompt(
        self, name: _Callable[_P, _R], *, description: str | None = None
    ) -> _Callable[_P, _R]: ...

    def prompt(
        self,
        name: str | _Callable[..., _Any] | None = None,
        *,
        description: str | None = None,
    ) -> _Any:
        def register(function: _Callable[_P, _R]) -> _Callable[_P, _R]:
            selected = name if isinstance(name, str) else function.__name__
            self._prompts[selected] = (
                _types.Prompt(name=selected, description=description),
                function,
            )
            return function

        if callable(name) and not isinstance(name, str):
            return register(name)
        return register

    def expect(
        self,
        method: str,
        *,
        optional: bool = False,
        repeat: int | tuple[int, int | None] | None = None,
        subset: bool = False,
        unordered: str | None = None,
        fallback: bool = False,
        **matcher: _Any,
    ) -> ExpectedCall:
        expected = ExpectedCall(
            method,
            matcher,
            subset_match=subset,
            unordered_group=unordered,
            fallback_call=fallback,
        )
        if optional:
            expected.optional()
        if repeat is not None:
            expected.repeated(repeat, repeat) if isinstance(
                repeat, int
            ) else expected.repeated(*repeat)
        self._expectations.append(expected)
        return expected

    expect_call = expect

    def expect_tool_call(
        self, name: str, arguments: _Mapping[str, _Any] | None = None, **options: _Any
    ) -> ExpectedCall:
        return self.expect(
            "tools/call", tool=name, arguments=dict(arguments or {}), **options
        )

    def _matching(
        self, method: str, params: _Mapping[str, _Any]
    ) -> ExpectedCall | None:
        candidates = [
            item
            for item in self._expectations
            if item.method == method
            and (item.maximum is None or item.calls < item.maximum)
        ]
        fallback: ExpectedCall | None = next(
            (item for item in candidates if item.fallback_call), None
        )
        index = 0
        while index < len(candidates):
            item = candidates[index]
            if item.unordered_group is not None:
                group = [
                    peer
                    for peer in candidates
                    if peer.unordered_group == item.unordered_group
                ]
                for peer in group:
                    actual = {key: params.get(key) for key in peer.matcher}
                    matched = (
                        _subset(peer.matcher, actual)
                        if peer.subset_match
                        else _normalize(peer.matcher) == _normalize(actual)
                    )
                    if matched:
                        return peer
                if all(
                    peer.optional_call or peer.minimum == 0 or peer.fallback_call
                    for peer in group
                ):
                    index += 1
                    continue
                if fallback is not None:
                    return fallback
                raise MockExpectationError(
                    f"unexpected order or parameters for {method}"
                )
            actual = {key: params.get(key) for key in item.matcher}
            matched = (
                _subset(item.matcher, actual)
                if item.subset_match
                else _normalize(item.matcher) == _normalize(actual)
            )
            if matched:
                return item
            if item.fallback_call:
                index += 1
                continue
            if item.optional_call or item.minimum == 0:
                index += 1
                continue
            if fallback is not None:
                return fallback
            raise MockExpectationError(f"unexpected order or parameters for {method}")
        return fallback

    def _expect_call(
        self, method: str, params: _Mapping[str, _Any]
    ) -> ExpectedCall | None:
        expected = self._matching(method, params)
        known = {
            "tools/list",
            "resources/list",
            "resources/read",
            "prompts/list",
            "prompts/get",
            "resources/templates/list",
            "resources/subscribe",
            "resources/unsubscribe",
            "completion/complete",
            "logging/setLevel",
        }
        registered_tool = (
            method == "tools/call"
            and isinstance(params.get("tool"), str)
            and params["tool"] in self._tools
        )
        if (
            expected is None
            and self.strict
            and not registered_tool
            and method not in self._CONTROL_METHODS
            and method not in known
        ):
            raise MockExpectationError(f"unexpected MCP call: {method}")
        if expected is not None:
            expected.calls += 1
        return expected

    async def _invoke(self, function: _Handler | None, value: _Any) -> _Any:
        if function is None:
            return None
        result = function(value)
        return await result if inspect.isawaitable(result) else result

    async def _record(
        self, method: str, params: _Mapping[str, _Any], response: _Any = None
    ) -> None:
        safe_params = _cast(
            _Mapping[str, _JsonValue],
            _redact_for_persistence(
                dict(params), config=self._redaction_config, path="$.params"
            ),
        )
        safe_response = (
            None
            if response is None
            else _redact_for_persistence(
                response, config=self._redaction_config, path="$.response"
            )
        )
        self._recording.append(
            RecordedInteraction(
                method,
                safe_params,
                _json_safe(safe_response),
                sequence=self._sequence,
                provenance=("official_mcp_server", "redacted_persistence"),
            )
        )
        self._sequence += 1

    async def _list_tools(
        self, _context: _Any, _params: _Any
    ) -> _types.ListToolsResult:
        request = _page_params(_params)
        expected = self._expect_call("tools/list", request)
        tools = [item[0] for item in self._tools.values()]
        if "tools/list" in self.faults.invalid_schema_methods:
            tools = [
                tool.model_copy(
                    update={"input_schema": {"type": "fault-invalid-schema"}}
                )
                for tool in tools
            ]
        if "tools/call" in self.faults.invalid_result_methods:
            tools = [
                tool.model_copy(update={"output_schema": dict(_INVALID_RESULT_SCHEMA)})
                for tool in tools
            ]
        result = (
            expected.response
            if expected is not None and expected.response is not None
            else _types.ListToolsResult(tools=tools)
        )
        result = _cast(
            _types.ListToolsResult, self.faults.response("tools/list", result)
        )
        await self._record("tools/list", request, result)
        return result

    async def _call_tool(
        self, _context: _Any, params: _types.CallToolRequestParams
    ) -> _types.CallToolResult:
        arguments = dict(params.arguments or {})
        expected = self._expect_call(
            "tools/call", {"tool": params.name, "arguments": arguments}
        )
        await self.faults.before("tools/call")
        definition = self._tools.get(params.name)
        if expected is not None and expected.response is not None:
            result = expected.response
        else:
            if definition is None:
                raise MockExpectationError(f"unknown mock tool: {params.name}")
            result = await self._invoke(definition[1], arguments)
        if isinstance(result, str):
            result = _types.CallToolResult(content=[_types.TextContent(text=result)])
        elif isinstance(result, _Mapping):
            result = _types.CallToolResult(content=[], structured_content=dict(result))
        if result is None:
            result = _types.CallToolResult(content=[])
        if not isinstance(result, _types.CallToolResult):
            raise TypeError(
                "mock tool handlers must return CallToolResult, text, or a mapping"
            )
        result = _cast(
            _types.CallToolResult, self.faults.response("tools/call", result)
        )
        await self._record(
            "tools/call", {"tool": params.name, "arguments": arguments}, result
        )
        return result

    async def _list_resources(
        self, _context: _Any, _params: _Any
    ) -> _types.ListResourcesResult:
        request = _page_params(_params)
        expected = self._expect_call("resources/list", request)
        result = (
            expected.response
            if expected is not None and expected.response is not None
            else _types.ListResourcesResult(
                resources=[item[0] for item in self._resources.values()]
            )
        )
        result = _cast(
            _types.ListResourcesResult, self.faults.response("resources/list", result)
        )
        await self._record("resources/list", request, result)
        return result

    async def _read_resource(
        self, _context: _Any, params: _types.ReadResourceRequestParams
    ) -> _types.ReadResourceResult:
        expected = self._expect_call("resources/read", {"uri": params.uri})
        await self.faults.before("resources/read")
        definition = self._resources.get(params.uri)
        if definition is None:
            raise MockExpectationError("unknown mock resource")
        result = (
            expected.response
            if expected is not None and expected.response is not None
            else await self._invoke(definition[1], params.uri)
        )
        if not isinstance(result, _types.ReadResourceResult):
            result = _types.ReadResourceResult(
                contents=[
                    _types.TextResourceContents(
                        uri=params.uri, mime_type="text/plain", text=str(result or "")
                    )
                ]
            )
        result = _cast(
            _types.ReadResourceResult, self.faults.response("resources/read", result)
        )
        await self._record("resources/read", {"uri": params.uri}, result)
        return result

    async def _list_prompts(
        self, _context: _Any, _params: _Any
    ) -> _types.ListPromptsResult:
        request = _page_params(_params)
        expected = self._expect_call("prompts/list", request)
        result = (
            expected.response
            if expected is not None and expected.response is not None
            else _types.ListPromptsResult(
                prompts=[item[0] for item in self._prompts.values()]
            )
        )
        result = _cast(
            _types.ListPromptsResult, self.faults.response("prompts/list", result)
        )
        await self._record("prompts/list", request, result)
        return result

    async def _get_prompt(
        self, _context: _Any, params: _types.GetPromptRequestParams
    ) -> _types.GetPromptResult:
        expected = self._expect_call(
            "prompts/get",
            {"name": params.name, "arguments": dict(params.arguments or {})},
        )
        await self.faults.before("prompts/get")
        definition = self._prompts.get(params.name)
        if definition is None:
            raise MockExpectationError("unknown mock prompt")
        result = (
            expected.response
            if expected is not None and expected.response is not None
            else await self._invoke(definition[1], dict(params.arguments or {}))
        )
        if not isinstance(result, _types.GetPromptResult):
            result = _types.GetPromptResult(
                messages=[
                    _types.PromptMessage(
                        role="user", content=_types.TextContent(text=str(result or ""))
                    )
                ]
            )
        result = _cast(
            _types.GetPromptResult, self.faults.response("prompts/get", result)
        )
        await self._record(
            "prompts/get",
            {"name": params.name, "arguments": dict(params.arguments or {})},
            result,
        )
        return result

    async def _list_resource_templates(
        self, _context: _Any, _params: _Any
    ) -> _types.ListResourceTemplatesResult:
        request = _page_params(_params)
        expected = self._expect_call("resources/templates/list", request)
        result = (
            expected.response
            if expected is not None and expected.response is not None
            else _types.ListResourceTemplatesResult(
                resource_templates=list(self._resource_templates.values())
            )
        )
        result = _cast(
            _types.ListResourceTemplatesResult,
            self.faults.response("resources/templates/list", result),
        )
        await self._record("resources/templates/list", request, result)
        return result

    async def _subscribe_resource(
        self, _context: _Any, params: _types.SubscribeRequestParams
    ) -> _types.EmptyResult:
        self._expect_call("resources/subscribe", {"uri": params.uri})
        result = _types.EmptyResult()
        await self._record("resources/subscribe", {"uri": params.uri}, result)
        return result

    async def _unsubscribe_resource(
        self, _context: _Any, params: _types.UnsubscribeRequestParams
    ) -> _types.EmptyResult:
        self._expect_call("resources/unsubscribe", {"uri": params.uri})
        result = _types.EmptyResult()
        await self._record("resources/unsubscribe", {"uri": params.uri}, result)
        return result

    async def _completion(
        self, _context: _Any, params: _types.CompleteRequestParams
    ) -> _types.CompleteResult:
        request = {
            "ref": _json_safe(params.ref),
            "argument": _json_safe(params.argument),
        }
        self._expect_call("completion/complete", request)
        result = _types.CompleteResult(completion=_types.Completion(values=[]))
        await self._record("completion/complete", request, result)
        return result

    async def _set_logging_level(
        self, _context: _Any, params: _types.SetLevelRequestParams
    ) -> _types.EmptyResult:
        request = {"level": params.level}
        self._expect_call("logging/setLevel", request)
        result = _types.EmptyResult()
        await self._record("logging/setLevel", request, result)
        return result

    async def _ping(self, _context: _Any, _params: _Any) -> _types.EmptyResult:
        self._expect_call("ping", {})
        result = _types.EmptyResult()
        await self._record("ping", {}, result)
        return result

    async def _progress(
        self, _context: _Any, params: _types.ProgressNotificationParams
    ) -> None:
        await self._record(
            "notifications/progress", _cast(_Mapping[str, _Any], _json_safe(params)), {}
        )

    async def _roots_list_changed(self, _context: _Any, _params: _Any) -> None:
        await self._record("notifications/roots/list_changed", {}, {})

    def server(self) -> _Server:
        return _Server(
            self.name,
            version=self.version,
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
            on_list_resources=self._list_resources,
            on_list_resource_templates=self._list_resource_templates,
            on_read_resource=self._read_resource,
            on_subscribe_resource=self._subscribe_resource,
            on_unsubscribe_resource=self._unsubscribe_resource,
            on_list_prompts=self._list_prompts,
            on_get_prompt=self._get_prompt,
            on_completion=self._completion,
            on_set_logging_level=self._set_logging_level,
            on_ping=self._ping,
            on_progress=self._progress,
            on_roots_list_changed=self._roots_list_changed,
        )

    def in_process(self, *, name: str | None = None) -> _InProcessServer:
        """Return a decoded ``SessionMessage`` in-process fixture."""

        def factory() -> _FaultingServer:
            return _FaultingServer(self.server(), self.faults)

        return _InProcessServer(name=name or self.name, factory=factory)

    def verify(self) -> None:
        missing = [
            item.method for item in self._expectations if item.calls < item.minimum
        ]
        if missing:
            raise MockExpectationError(
                "missing expected MCP calls: " + ", ".join(missing)
            )

    def recording(self) -> Recording:
        self.verify()
        initialization = _cast(
            _Mapping[str, _JsonValue],
            _redact_for_persistence(
                {
                    "serverInfo": {"name": self.name, "version": self.version},
                    "protocolVersion": "2025-11-25",
                },
                config=self._redaction_config,
                path="$.initialization",
            ),
        )
        return Recording(
            tuple(self._recording),
            redaction_bound=True,
            provenance=("official_mcp_server", "normalized_decoded_messages"),
            server_name=self.name,
            server_version=self.version,
            initialization=initialization,
            redaction_bindings=(
                RedactionBinding(source="mock_mcp_server", version="1"),
            ),
            _runtime=_RedactionRuntime(self._redaction_config, bound=True),
        )

    def close(self) -> None:
        if not self._closed:
            self.verify()
            self._closed = True


class ReplayServer(MockMCPServer):
    """Strict JSON-only replay matcher."""

    def __init__(
        self,
        recording: Recording,
        *,
        strict: bool = True,
        redaction_config: _RedactionConfig | None = None,
        artifacts: _Mapping[str, bytes] | None = None,
        expected_server: tuple[str, str] | None = None,
    ) -> None:
        if not recording.redaction_bound:
            raise ValueError(
                "replay requires a recording with an explicit redaction binding"
            )
        selected_config = redaction_config or recording._runtime.config
        safe_recording = recording.redacted(config=selected_config)
        if expected_server is not None and expected_server != (
            safe_recording.server_name,
            safe_recording.server_version,
        ):
            raise ReplayMismatch("replay server identity differs from the recording")
        if artifacts is not None:
            safe_recording.validate_artifacts(artifacts)
        super().__init__(
            name=safe_recording.server_name,
            version=safe_recording.server_version,
            strict=strict,
            redaction_config=selected_config,
        )
        self._replay = safe_recording.interactions
        self._recording_provenance = safe_recording.provenance
        self._replay_index = 0
        self._relaxed = not strict
        self._relaxations: list[str] = []
        for interaction in self._replay:
            raw_name = interaction.params.get("tool")
            if interaction.method == "tools/call" and isinstance(raw_name, str):
                name = raw_name
                self._tools.setdefault(
                    name,
                    (_types.Tool(name=name, input_schema={"type": "object"}), None),
                )

    @staticmethod
    def _decoded(
        interaction: RecordedInteraction, model: _Any, *, fallback: _Any = None
    ) -> _Any:
        if interaction.error is not None:
            raise MockProtocolError(-32000, "recorded protocol error")
        if interaction.response is None:
            if fallback is not None:
                return fallback
            raise ReplayMismatch("recorded response is missing")
        try:
            return model.model_validate(interaction.response)
        except BaseException:
            raise ReplayMismatch("recorded response is invalid") from None

    def match(self, method: str, params: _Mapping[str, _Any]) -> RecordedInteraction:
        actual = _normalize(
            _redact_for_persistence(
                dict(params), config=self._redaction_config, path="$.replay.params"
            )
        )
        if self._replay_index >= len(self._replay):
            if self._relaxed:
                self._relaxations.append("relaxed_replay")
                return RecordedInteraction(
                    method,
                    _cast(_Mapping[str, _JsonValue], actual),
                    provenance=("relaxed_replay",),
                )
            raise ReplayMismatch("replay has no remaining interaction")
        expected = self._replay[self._replay_index]
        if expected.method != method or _normalize(expected.params) != actual:
            if not self._relaxed:
                raise ReplayMismatch(
                    "replay request differs in method, arguments, or ordering"
                )
            self._relaxations.append("relaxed_replay")
            return RecordedInteraction(
                method,
                _cast(_Mapping[str, _JsonValue], actual),
                provenance=("relaxed_replay",),
            )
        self._replay_index += 1
        return expected

    def verify_replay(self) -> None:
        if self._replay_index != len(self._replay) and not self._relaxed:
            raise ReplayMismatch("replay ended before all interactions were consumed")

    @property
    def provenance(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self._recording_provenance, *self._relaxations)))

    async def _call_tool(
        self, _context: _Any, params: _types.CallToolRequestParams
    ) -> _types.CallToolResult:
        interaction = self.match(
            "tools/call",
            {"tool": params.name, "arguments": dict(params.arguments or {})},
        )
        return _cast(
            _types.CallToolResult,
            self._decoded(
                interaction,
                _types.CallToolResult,
                fallback=_types.CallToolResult(content=[]),
            ),
        )

    async def _list_tools(
        self, _context: _Any, _params: _Any
    ) -> _types.ListToolsResult:
        return _cast(
            _types.ListToolsResult,
            self._decoded(
                self.match("tools/list", _page_params(_params)),
                _types.ListToolsResult,
                fallback=_types.ListToolsResult(tools=[]),
            ),
        )

    async def _list_resources(
        self, _context: _Any, _params: _Any
    ) -> _types.ListResourcesResult:
        return _cast(
            _types.ListResourcesResult,
            self._decoded(
                self.match("resources/list", _page_params(_params)),
                _types.ListResourcesResult,
                fallback=_types.ListResourcesResult(resources=[]),
            ),
        )

    async def _list_resource_templates(
        self, _context: _Any, _params: _Any
    ) -> _types.ListResourceTemplatesResult:
        return _cast(
            _types.ListResourceTemplatesResult,
            self._decoded(
                self.match("resources/templates/list", _page_params(_params)),
                _types.ListResourceTemplatesResult,
                fallback=_types.ListResourceTemplatesResult(resource_templates=[]),
            ),
        )

    async def _read_resource(
        self, _context: _Any, params: _types.ReadResourceRequestParams
    ) -> _types.ReadResourceResult:
        return _cast(
            _types.ReadResourceResult,
            self._decoded(
                self.match("resources/read", {"uri": params.uri}),
                _types.ReadResourceResult,
                fallback=_types.ReadResourceResult(contents=[]),
            ),
        )

    async def _list_prompts(
        self, _context: _Any, _params: _Any
    ) -> _types.ListPromptsResult:
        return _cast(
            _types.ListPromptsResult,
            self._decoded(
                self.match("prompts/list", _page_params(_params)),
                _types.ListPromptsResult,
                fallback=_types.ListPromptsResult(prompts=[]),
            ),
        )

    async def _get_prompt(
        self, _context: _Any, params: _types.GetPromptRequestParams
    ) -> _types.GetPromptResult:
        request = {"name": params.name, "arguments": dict(params.arguments or {})}
        return _cast(
            _types.GetPromptResult,
            self._decoded(
                self.match("prompts/get", request),
                _types.GetPromptResult,
                fallback=_types.GetPromptResult(messages=[]),
            ),
        )

    async def _subscribe_resource(
        self, _context: _Any, params: _types.SubscribeRequestParams
    ) -> _types.EmptyResult:
        return _cast(
            _types.EmptyResult,
            self._decoded(
                self.match("resources/subscribe", {"uri": params.uri}),
                _types.EmptyResult,
                fallback=_types.EmptyResult(),
            ),
        )

    async def _unsubscribe_resource(
        self, _context: _Any, params: _types.UnsubscribeRequestParams
    ) -> _types.EmptyResult:
        return _cast(
            _types.EmptyResult,
            self._decoded(
                self.match("resources/unsubscribe", {"uri": params.uri}),
                _types.EmptyResult,
                fallback=_types.EmptyResult(),
            ),
        )

    async def _completion(
        self, _context: _Any, params: _types.CompleteRequestParams
    ) -> _types.CompleteResult:
        request = {
            "ref": _json_safe(params.ref),
            "argument": _json_safe(params.argument),
        }
        return _cast(
            _types.CompleteResult,
            self._decoded(
                self.match("completion/complete", request),
                _types.CompleteResult,
                fallback=_types.CompleteResult(completion=_types.Completion(values=[])),
            ),
        )

    async def _set_logging_level(
        self, _context: _Any, params: _types.SetLevelRequestParams
    ) -> _types.EmptyResult:
        interaction = self.match("logging/setLevel", {"level": params.level})
        return _cast(
            _types.EmptyResult,
            self._decoded(
                interaction, _types.EmptyResult, fallback=_types.EmptyResult()
            ),
        )

    async def _ping(self, _context: _Any, _params: _Any) -> _types.EmptyResult:
        interaction = self.match("ping", {})
        return _cast(
            _types.EmptyResult,
            self._decoded(
                interaction, _types.EmptyResult, fallback=_types.EmptyResult()
            ),
        )

    async def _progress(
        self, _context: _Any, params: _types.ProgressNotificationParams
    ) -> None:
        self.match(
            "notifications/progress", _cast(_Mapping[str, _Any], _json_safe(params))
        )

    async def _roots_list_changed(self, _context: _Any, _params: _Any) -> None:
        self.match("notifications/roots/list_changed", {})


__all__ = [
    "ArtifactIntegrityError",
    "ExpectedCall",
    "FaultInjector",
    "Gate",
    "MockExpectationError",
    "MockMCPServer",
    "MockProtocolError",
    "RecordedArtifact",
    "RecordedInteraction",
    "Recording",
    "RedactionBinding",
    "ReplayMismatch",
    "ReplayServer",
    "VirtualClock",
]
