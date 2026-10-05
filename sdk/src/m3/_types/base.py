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
from types import MappingProxyType as _MappingProxyType
from typing import (
    TYPE_CHECKING as _TYPE_CHECKING,
)
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

if _TYPE_CHECKING:
    from typing_extensions import Self as _Self


def _utc_now() -> _datetime:
    return _datetime.now(_timezone.utc)


EVENT_SCHEMA_ID = "m3.event"
EVENT_SCHEMA_VERSION = "0.2"


class _FrozenMapping(_Mapping[_Any, _Any]):
    """Tuple-backed immutable mapping with no mutable dict base to bypass."""

    __slots__ = ("_index", "_items")
    _items: tuple[tuple[_Any, _Any], ...]
    _index: _Mapping[_Any, _Any]

    def __init__(self, values: _Mapping[_Any, _Any] | None = None) -> None:
        items = tuple((values or {}).items())
        object.__setattr__(self, "_items", items)
        object.__setattr__(self, "_index", _MappingProxyType(dict(items)))

    def __setattr__(self, name: str, value: _Any) -> None:
        raise TypeError("frozen mapping is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("frozen mapping is immutable")

    def __getitem__(self, key: _Any) -> _Any:
        try:
            return self._index[key]
        except TypeError:
            raise KeyError(key) from None

    def __iter__(self) -> _Iterator[_Any]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


_NONE_TYPE = type(None)
# Exact JSON scalar types whose freeze, thaw, and JSON-safety need no walk.
# Subclasses (enum members, str subclasses) take the general path below.
_PLAIN_SCALARS = frozenset({str, int, bool, _NONE_TYPE})
_INFINITIES = (float("inf"), float("-inf"))


def _deep_thaw(value: _Any) -> _Any:
    """Return JSON-compatible containers for Pydantic's serializer."""

    kind = type(value)
    if kind in _PLAIN_SCALARS or kind is float:
        return value
    if kind is _FrozenMapping:
        return {_deep_thaw(key): _deep_thaw(item) for key, item in value._items}
    if kind is tuple:
        return [_deep_thaw(item) for item in value]
    if isinstance(value, _Mapping):
        return {_deep_thaw(key): _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [_deep_thaw(item) for item in value]
    return value


def _json_safe(value: _Any) -> bool:
    """Return whether a public value can be represented by JSON serialization."""

    kind = type(value)
    if kind in _PLAIN_SCALARS:
        return True
    if kind is float:
        return value == value and value not in _INFINITIES
    if kind is dict or kind is _FrozenMapping:
        items = value._items if kind is _FrozenMapping else value.items()
        for key, item in items:
            if not isinstance(key, str) or not _json_safe(item):
                return False
        return True
    if kind is tuple or kind is list:
        for item in value:
            if not _json_safe(item):
                return False
        return True
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, _datetime):
        return True
    if isinstance(value, _Enum):
        return _json_safe(value.value)
    if isinstance(value, FrozenModel):
        # Validated and frozen at construction (and in ``model_copy``).
        return True
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

    kind = type(value)
    if kind in _PLAIN_SCALARS or kind is float or kind is _FrozenMapping:
        return value
    if kind is dict:
        return _FrozenMapping(
            {_deep_freeze(key): _deep_freeze(item) for key, item in value.items()}
        )
    if kind is list or kind is tuple:
        return tuple([_deep_freeze(item) for item in value])
    if isinstance(value, _FrozenMapping):
        return value
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


_NOT_JSON_SAFE = object()


def _safe_freeze(value: _Any) -> _Any:
    """Return ``_deep_freeze(value)``, or ``_NOT_JSON_SAFE`` when ``_json_safe``
    would reject it, walking plain JSON containers once instead of twice."""

    kind = type(value)
    if kind in _PLAIN_SCALARS:
        return value
    if kind is float:
        return value if value == value and value not in _INFINITIES else _NOT_JSON_SAFE
    if kind is dict:
        frozen: dict[_Any, _Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                return _NOT_JSON_SAFE
            item = _safe_freeze(item)
            if item is _NOT_JSON_SAFE:
                return _NOT_JSON_SAFE
            frozen[_deep_freeze(key)] = item
        return _FrozenMapping(frozen)
    if kind is list or kind is tuple:
        items = []
        for item in value:
            item = _safe_freeze(item)
            if item is _NOT_JSON_SAFE:
                return _NOT_JSON_SAFE
            items.append(item)
        return tuple(items)
    return _deep_freeze(value) if _json_safe(value) else _NOT_JSON_SAFE


def _checked_freeze(model_type: type[_BaseModel], field_name: str, value: _Any) -> _Any:
    """Validate a field value as JSON-safe (unless excluded) and deep-freeze it."""

    field_info = model_type.__pydantic_fields__.get(field_name)
    if field_info is not None and field_info.exclude:
        return _deep_freeze(value)
    frozen = _safe_freeze(value)
    if frozen is _NOT_JSON_SAFE:
        raise ValueError(f"{field_name} contains a non-JSON-serializable value")
    return frozen


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
        model_type = type(self)
        for field_name, value in self.__dict__.items():
            kind = type(value)
            # Values that freeze to themselves and are always JSON-safe: skip
            # the call and the no-op reassignment.
            if (
                kind in _PLAIN_SCALARS
                or kind is _datetime
                or (kind is float and value == value and value not in _INFINITIES)
                or isinstance(value, FrozenModel)
            ):
                continue
            object.__setattr__(
                self, field_name, _checked_freeze(model_type, field_name, value)
            )
        return self

    def model_copy(
        self, *, update: _Mapping[str, _Any] | None = None, deep: bool = False
    ) -> _Self:
        """Copy like Pydantic, applying construction-time checks to ``update``."""

        copy = super().model_copy(update=update, deep=deep)
        if update:
            for field_name, value in update.items():
                object.__setattr__(
                    copy, field_name, _checked_freeze(type(self), field_name, value)
                )
        return copy

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
    WAITING_FOR_INPUT = "waiting_for_input"
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
    MANAGED_INPUT_RECOVERY_UNAVAILABLE = "managed_input_recovery_unavailable"


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
