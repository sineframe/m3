"""Frozen, serializable domain values for the M3 public SDK.

This module intentionally contains contracts and value objects only.  It does
not import application settings, FastAPI, Streamlit, pytest, SQLAlchemy, or
any process/transport implementation.
"""

from __future__ import annotations

from collections.abc import Iterator as _Iterator
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Sequence
from datetime import datetime as _datetime
from datetime import timezone as _timezone
from enum import Enum as _Enum
from typing import (
    Any as _Any,
)
from typing import (
    Literal as _Literal,
)

from pydantic import (
    BaseModel as _BaseModel,
)
from pydantic import (
    ConfigDict as _ConfigDict,
)
from pydantic import (
    Field as _Field,
)
from pydantic import (
    RootModel as _RootModel,
)
from pydantic import (
    field_serializer as _field_serializer,
)
from pydantic import (
    field_validator as _field_validator,
)
from pydantic import (
    model_validator as _model_validator,
)


def _utc_now() -> _datetime:
    return _datetime.now(_timezone.utc)


EVENT_SCHEMA_ID = "m3.event"
EVENT_SCHEMA_VERSION = "0.2"


class _FrozenMapping(_Mapping[_Any, _Any]):
    """Tuple-backed immutable mapping with no mutable dict base to bypass."""

    __slots__ = ("_items",)
    _items: tuple[tuple[_Any, _Any], ...]

    def __init__(self, values: _Mapping[_Any, _Any] | None = None) -> None:
        object.__setattr__(self, "_items", tuple((values or {}).items()))

    def __setattr__(self, name: str, value: _Any) -> None:
        raise TypeError("frozen mapping is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("frozen mapping is immutable")

    def __getitem__(self, key: _Any) -> _Any:
        for item_key, item_value in self._items:
            if item_key == key:
                return item_value
        raise KeyError(key)

    def __iter__(self) -> _Iterator[_Any]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


def _deep_thaw(value: _Any) -> _Any:
    """Return JSON-compatible containers for Pydantic's serializer."""

    if isinstance(value, _Mapping):
        return {_deep_thaw(key): _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [_deep_thaw(item) for item in value]
    return value


def _json_safe(value: _Any) -> bool:
    """Return whether a public value can be represented by JSON serialization."""

    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, _datetime):
        return True
    if isinstance(value, _Enum):
        return _json_safe(value.value)
    if isinstance(value, _BaseModel):
        for field_name, field_info in type(value).model_fields.items():
            if field_info.exclude:
                continue
            if not _json_safe(getattr(value, field_name)):
                return False
        return True
    if isinstance(value, _Mapping):
        return all(
            isinstance(key, str) and _json_safe(item) for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_json_safe(item) for item in value)
    if isinstance(value, _Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return all(_json_safe(item) for item in value)
    return False


def _deep_freeze(value: _Any) -> _Any:
    """Recursively freeze containers while retaining JSON-compatible shapes."""

    if isinstance(value, _BaseModel):
        return value
    if isinstance(value, _Mapping):
        return _FrozenMapping(
            {_deep_freeze(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, _Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        # JSON has no set type.  Use a deterministic immutable tuple so the
        # value remains round-trippable without exposing a mutable list.
        items = (_deep_freeze(item) for item in value)
        return tuple(sorted(items, key=lambda item: (type(item).__name__, repr(item))))
    return value


class FrozenModel(_BaseModel):
    """Base configuration shared by public value objects."""

    model_config = _ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    @_model_validator(mode="after")
    def _freeze_nested_values(self) -> FrozenModel:
        for field_name, value in self.__dict__.items():
            field_info = type(self).model_fields.get(field_name)
            if field_info is None or not field_info.exclude:
                if not _json_safe(value):
                    raise ValueError(
                        f"{field_name} contains a non-JSON-serializable value"
                    )
            object.__setattr__(self, field_name, _deep_freeze(value))
        return self

    @_field_serializer("*", check_fields=False)
    def _serialize_nested_values(self, value: _Any) -> _Any:
        return _deep_thaw(value)


class Identifier(_RootModel[str]):
    """Non-empty stable identifier used in serialized SDK values."""

    model_config = _ConfigDict(frozen=True, str_strip_whitespace=True)

    @_field_validator("root")
    @classmethod
    def _valid_identifier(cls, value: str) -> str:
        if not value or len(value) > 256:
            raise ValueError("identifier must contain 1-256 characters")
        return value


class ExecutionId(Identifier):
    pass


class SessionId(Identifier):
    pass


class TurnId(Identifier):
    pass


class ServerId(Identifier):
    pass


class ServerProfileId(Identifier):
    pass


class HarnessId(Identifier):
    pass


class HarnessProfileId(Identifier):
    pass


class RevisionId(Identifier):
    pass


class ConnectionId(Identifier):
    pass


class EventId(Identifier):
    pass


class ArtifactId(Identifier):
    pass


class TraceId(Identifier):
    pass


class EvaluationId(Identifier):
    pass


class RunId(Identifier):
    """Stable identity for one coordinated test/evaluation run."""

    pass


class ProjectId(Identifier):
    """Stable identity for a repository project."""

    @_field_validator("root")
    @classmethod
    def _valid_project_uuid(cls, value: str) -> str:
        import uuid

        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("project id must be a UUID") from exc
        return str(parsed)


class SuiteId(_RootModel[int]):
    """Database integer identity for a logical test suite."""

    model_config = _ConfigDict(frozen=True)

    @_field_validator("root")
    @classmethod
    def _valid_suite_id(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("suite id must be a positive integer")
        return value


class Metadata(FrozenModel):
    """Non-secret descriptive metadata carried by public values."""

    name: str | None = _Field(default=None, max_length=256)
    description: str | None = _Field(default=None, max_length=4096)
    labels: _Mapping[str, str] = _Field(default_factory=dict)


class TransportKind(str, _Enum):
    IN_PROCESS = "in_process"
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


class TrustLevel(str, _Enum):
    UNTRUSTED = "untrusted"
    PUBLIC = "public"
    TRUSTED_PRIVATE = "trusted_private"
    SDK_LOOPBACK = "sdk_loopback"


class ExecutionStatus(str, _Enum):
    CREATED = "created"
    QUEUED = "queued"
    STARTING = "starting"
    IDLE = "idle"
    RUNNING_TURN = "running_turn"
    CLOSING = "closing"
    FINISHED = "finished"


class ExecutionOutcome(str, _Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class TurnStatus(str, _Enum):
    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"


class TurnOutcome(str, _Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class EvaluationStatus(str, _Enum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"
    NOT_RUN = "not_run"


class CapabilityStatus(str, _Enum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


class ActivityHealth(str, _Enum):
    NO_CALLS = "no_calls"
    ALL_SUCCEEDED = "all_succeeded"
    ALL_FAILED = "all_failed"
    MIXED = "mixed"


class ArtifactPolicy(str, _Enum):
    FAILED = "failed"
    ALWAYS = "always"
    NEVER = "never"


class WorkspaceKind(str, _Enum):
    TEMPORARY = "temporary"
    COPY = "copy"
    GIT_WORKTREE = "git_worktree"
    READ_ONLY = "read_only"
    IN_PLACE = "in_place"


class ErrorCode(str, _Enum):
    INVALID_ARGUMENT = "invalid_argument"
    INVALID_TRANSITION = "invalid_transition"
    PROTOCOL_ERROR = "protocol_error"
    TRANSPORT_ERROR = "transport_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SESSION_STILL_OPEN = "session_still_open"
    SESSION_BUSY = "session_busy"
    UNSUPPORTED = "unsupported"
    CLEANUP_FAILED = "cleanup_failed"


class SecretReference(FrozenModel):
    """Reference to a secret; resolved values are deliberately not modelled."""

    source: _Literal["environment", "provider"]
    name: str = _Field(min_length=1, max_length=256)


class ErrorInfo(FrozenModel):
    code: ErrorCode
    message: str = _Field(min_length=1, max_length=4096)
    retryable: bool = False
    details: _Mapping[str, _Any] = _Field(default_factory=dict)


class ProtocolConstraint(FrozenModel):
    revision: str | None = _Field(default=None, min_length=1, max_length=128)
    transport: TransportKind | None = None


class RevisionSelection(FrozenModel):
    """Explicit profile revision request: resolve ``latest`` before execution."""

    mode: _Literal["latest", "pinned"]
    revision_id: RevisionId | None = None
    revision_number: int | None = _Field(default=None, ge=1)

    @_model_validator(mode="after")
    def _validate_selection(self) -> RevisionSelection:
        has_id = self.revision_id is not None
        has_number = self.revision_number is not None
        if self.mode == "pinned" and not (has_id and has_number):
            raise ValueError(
                "pinned revision selection requires revision_id and revision_number"
            )
        if self.mode == "latest" and (has_id or has_number):
            raise ValueError(
                "latest revision selection cannot include an immutable revision"
            )
        return self


class TextContent(FrozenModel):
    kind: _Literal["text"] = "text"
    text: str


class FileContent(FrozenModel):
    kind: _Literal["file"] = "file"
    path: str = _Field(min_length=1)
    media_type: str | None = None

    @_field_validator("path")
    @classmethod
    def _path_is_relative_or_explicit(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("path cannot contain NUL")
        return value


class ImageContent(FrozenModel):
    kind: _Literal["image"] = "image"
    media_type: str
    data: str | None = None
    uri: str | None = None

    @_model_validator(mode="after")
    def _one_source(self) -> ImageContent:
        if (self.data is None) == (self.uri is None):
            raise ValueError("image requires exactly one of data or uri")
        return self


class AudioContent(FrozenModel):
    kind: _Literal["audio"] = "audio"
    media_type: str
    data: str | None = None
    uri: str | None = None

    @_model_validator(mode="after")
    def _one_source(self) -> AudioContent:
        if (self.data is None) == (self.uri is None):
            raise ValueError("audio requires exactly one of data or uri")
        return self


class ResourceLink(FrozenModel):
    kind: _Literal["resource_link"] = "resource_link"
    uri: str = _Field(min_length=1)
    name: str | None = None
    description: str | None = None
    media_type: str | None = None


class OpaqueContent(FrozenModel):
    kind: _Literal["opaque"] = "opaque"
    provider: str = _Field(min_length=1, max_length=128)
    payload: _Mapping[str, _Any]


__all__ = [
    "EVENT_SCHEMA_ID",
    "EVENT_SCHEMA_VERSION",
    "ActivityHealth",
    "ArtifactId",
    "ArtifactPolicy",
    "AudioContent",
    "CapabilityStatus",
    "ConnectionId",
    "ErrorCode",
    "ErrorInfo",
    "EvaluationId",
    "EvaluationStatus",
    "EventId",
    "ExecutionId",
    "ExecutionOutcome",
    "ExecutionStatus",
    "FileContent",
    "FrozenModel",
    "HarnessId",
    "HarnessProfileId",
    "Identifier",
    "ImageContent",
    "Metadata",
    "OpaqueContent",
    "ProtocolConstraint",
    "ResourceLink",
    "RevisionId",
    "RevisionSelection",
    "RunId",
    "SecretReference",
    "ServerId",
    "ServerProfileId",
    "SessionId",
    "TextContent",
    "TraceId",
    "TransportKind",
    "TrustLevel",
    "TurnId",
    "TurnOutcome",
    "TurnStatus",
    "WorkspaceKind",
]
