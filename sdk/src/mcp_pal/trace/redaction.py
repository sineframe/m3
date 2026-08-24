"""Fail-closed redaction for values crossing an observation boundary.

The trace pipeline has several observation boundaries (persistence, artifact
export, logs, API responses, and the UI).  This module deliberately has one
strict implementation for all of them.  A value that cannot be inspected or
made JSON-safe raises :class:`RedactionError`; callers must not recover by
storing the original value.

``redact`` remains a tuple-returning compatibility API for existing capture
code.  The only object that may hold an original value is
``InProcessAssertionView``.  It cannot be serialized or pickled and is never
returned by a redacted projection.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import PurePath
from types import MappingProxyType
from typing import Any, Final, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

REDACTED: Final[str] = "[REDACTED]"

_DEFAULT_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "authorization", "proxy_authorization", "cookie", "set_cookie",
        "x_api_key", "api_key", "apikey", "access_token", "refresh_token",
        "id_token", "auth_token", "bearer_token", "token", "secret",
        "password", "passwd", "credential", "credentials", "private_key",
        "client_secret", "session_key",
    }
)
_SENSITIVE_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(^|[_-])(authorization|proxy[_-]?authorization|cookie|set[_-]?cookie|"
    r"x[_-]?api[_-]?key|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|auth[_-]?token|bearer[_-]?token|token|secret|password|"
    r"passwd|credential|private[_-]?key|client[_-]?secret)([_-]|$)",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(^|[_-])(access[_-]?token|auth|authorization|api[_-]?key|apikey|"
    r"bearer|code|client[_-]?secret|credential|key|password|secret|"
    r"signature|sig|token)([_-]|$)",
    re.IGNORECASE,
)
_URL_PREFIX: Final[tuple[str, ...]] = ("http://", "https://")
_MAX_NESTING_DEPTH: Final[int] = 64
_SAFE_PATH_COMPONENTS: Final[frozenset[str]] = frozenset(
    {"payload", "metadata", "headers", "raw_event", "params", "url", "message", "args", "details"}
)
_SAFE_ERROR_REASONS: Final[frozenset[str]] = frozenset(
    {
        "value could not be safely redacted",
        "malformed URL",
        "object representation failed",
        "non-finite number",
        "exception message failed",
        "model serialization failed",
        "mapping key collision after redaction",
        "set projection is not deterministic",
        "opaque binary cannot be safely redacted",
        "unsupported value type",
        "maximum nesting depth exceeded",
        "cyclic value cannot be safely redacted",
        "value traversal failed",
        "artifact value is not bytes",
        "artifact bytes could not be read",
        "projection is not JSON serializable",
    }
)


class RedactionError(RuntimeError):
    """Raised when a value cannot be safely projected.

    The message contains only a stable reason.  The structural path remains
    available as typed metadata but is deliberately omitted from the message
    because a hostile mapping key could itself contain a secret.
    """

    def __init__(self, path: str, reason: str = "value could not be safely redacted") -> None:
        self.path = _sanitize_error_path(path)
        # Reasons are part of the public exception and callers may pass an
        # arbitrary hostile value.  Keep only the implementation's stable,
        # value-free vocabulary; unknown reasons collapse to the generic one.
        self.reason = reason if isinstance(reason, str) and reason in _SAFE_ERROR_REASONS else "value could not be safely redacted"
        # Keep the public exception message value-free even when a caller
        # supplied an unsafe path or a hostile mapping key.
        super().__init__(f"redaction failed: {self.reason}")


def _sanitize_error_path(path: Any) -> str:
    """Keep only safe structural path fragments in public error metadata."""

    if not isinstance(path, str):
        return "$"
    if path == "$":
        return path
    output = "$"
    index = 1
    while index < len(path):
        if path[index] == "[":
            close = path.find("]", index + 1)
            if close > index + 1 and path[index + 1 : close].isdigit():
                output += f"[{path[index + 1 : close]}]"
                index = close + 1
                continue
            output += "[]"
            index += 1
            continue
        if path[index] == ".":
            end = index + 1
            while end < len(path) and path[end] not in ".[":
                end += 1
            component = path[index + 1 : end]
            output += f".{component}" if component in _SAFE_PATH_COMPONENTS else ".[key]"
            index = end
            continue
        index += 1
    return output


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _clean_values(values: Iterable[str]) -> frozenset[str]:
    cleaned: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("redaction values must be strings")
        if value:
            cleaned.add(value)
    return frozenset(cleaned)


@dataclass(frozen=True, slots=True)
class RedactionConfig:
    """Explicit inputs to the redaction contract.

    ``credential_file_contents`` contains content already supplied by the
    caller.  Redaction never opens paths or reads the ambient filesystem.
    ``include_environment`` is true only for the default configuration; when
    the compatibility caller supplies ``secrets=set()``, ambient values are
    intentionally disabled.
    """

    secrets: frozenset[str] = frozenset()
    sensitive_keys: frozenset[str] = frozenset()
    credential_file_contents: frozenset[str] = frozenset()
    include_environment: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "secrets", _clean_values(self.secrets))
        object.__setattr__(self, "credential_file_contents", _clean_values(self.credential_file_contents))
        object.__setattr__(self, "sensitive_keys", frozenset(_normalize_key(key) for key in self.sensitive_keys if key))
        if not isinstance(self.include_environment, bool):
            raise TypeError("include_environment must be a bool")

    @classmethod
    def from_environment(
        cls,
        *,
        secrets: Iterable[str] = (),
        sensitive_keys: Iterable[str] = (),
        credential_file_contents: Iterable[str] = (),
    ) -> "RedactionConfig":
        """Build a config from explicit values and sensitive environment keys."""

        ambient = {
            value for key, value in os.environ.items()
            if value and _is_sensitive_key(key, frozenset())
        }
        return cls(
            secrets=frozenset(ambient).union(_clean_values(secrets)),
            sensitive_keys=frozenset(sensitive_keys),
            credential_file_contents=frozenset(credential_file_contents),
            include_environment=False,
        )


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Typed form of a redacted projection and changed paths."""

    value: Any
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class ArtifactBytesResult:
    """Redacted bytes plus non-sensitive replacement metadata.

    This result is intentionally separate from JSON/API projections: opaque
    binary artifacts may be preserved byte-for-byte when no configured secret
    sequence occurs.  Its repr and metadata expose lengths/counts only.
    """

    data: bytes
    redacted_count: int
    original_length: int

    @property
    def value(self) -> bytes:
        """Compatibility alias for the redacted bytes."""

        return self.data

    @property
    def metadata(self) -> Mapping[str, int]:
        return MappingProxyType(
            {
                "original_length": self.original_length,
                "output_length": len(self.data),
                "redacted_count": self.redacted_count,
            }
        )

    def __repr__(self) -> str:
        return (
            "ArtifactBytesResult("
            f"original_length={self.original_length}, output_length={len(self.data)}, "
            f"redacted_count={self.redacted_count})"
        )


class InProcessAssertionView:
    """Explicit, non-serializable access to a value for an assertion only."""

    __slots__ = ("_value", "__weakref__")
    _value: Any

    def __init__(self, value: Any) -> None:
        object.__setattr__(self, "_value", value)

    @property
    def value(self) -> Any:
        """Return the original value; callers must keep it in-process."""

        return self._value

    def __repr__(self) -> str:
        return "InProcessAssertionView(<non-serializable>)"

    def __str__(self) -> str:
        return self.__repr__()

    def __getstate__(self) -> None:
        raise TypeError("InProcessAssertionView is not serializable")

    def __reduce__(self) -> Any:
        raise TypeError("InProcessAssertionView is not serializable")


def assertion_view(value: Any) -> InProcessAssertionView:
    """Wrap an original value for an explicitly in-process assertion."""

    return InProcessAssertionView(value)


def _is_sensitive_key(key: str, configured: frozenset[str]) -> bool:
    normalized = _normalize_key(key)
    return normalized in _DEFAULT_SENSITIVE_KEYS or normalized in configured or bool(_SENSITIVE_KEY_PATTERN.search(key))


def _is_header_pair_name(key: str, configured: frozenset[str]) -> bool:
    normalized = _normalize_key(key)
    if normalized in _DEFAULT_SENSITIVE_KEYS or normalized in configured:
        return True
    return normalized.startswith("x_") and any(part in normalized for part in ("auth", "token", "key", "secret"))


def known_secret_values() -> set[str]:
    """Return values from environment variables with credential-shaped names."""

    return {
        value for key, value in os.environ.items()
        if value and _is_sensitive_key(key, frozenset())
    }


def _replace_secrets(value: str, config: RedactionConfig, path: str, paths: list[str]) -> str:
    result = value
    secret_values = set(config.secrets).union(config.credential_file_contents)
    for secret in sorted((item for item in secret_values if item), key=lambda item: (-len(item), item)):
        if secret in result:
            result = result.replace(secret, REDACTED)
            if path not in paths:
                paths.append(path)
    return result


def _redact_url(value: str, config: RedactionConfig, path: str, paths: list[str]) -> str:
    if not value.lower().startswith(_URL_PREFIX):
        return value
    try:
        parts = urlsplit(value)
        port = f":{parts.port}" if parts.port is not None else ""  # validates port
        host = parts.hostname or ""
        if not host:
            raise ValueError("missing host")
        if parts.username is not None or parts.password is not None:
            netloc = f"{REDACTED}@{host}{port}"
            paths.append(path)
        else:
            netloc = parts.netloc
        query_items = []
        query_changed = False
        for key, item in parse_qsl(parts.query, keep_blank_values=True):
            if _SENSITIVE_QUERY_PATTERN.search(key) or _is_sensitive_key(key, config.sensitive_keys):
                query_items.append((key, REDACTED))
                query_changed = True
            else:
                query_items.append((key, item))
        query = urlencode(query_items, doseq=True)
        if query_changed and path not in paths:
            paths.append(path)
        return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    except (TypeError, ValueError):
        raise RedactionError(path, "malformed URL") from None


def _safe_repr(value: Any, config: RedactionConfig, path: str, paths: list[str]) -> str:
    try:
        rendered = repr(value)
    except BaseException:
        raise RedactionError(path, "object representation failed") from None
    return _redact_string(rendered, config, path, paths)


def _redact_string(value: str, config: RedactionConfig, path: str, paths: list[str]) -> str:
    result = _redact_url(value, config, path, paths)
    return _replace_secrets(result, config, path, paths)


def _walk_value(
    item: Any,
    config: RedactionConfig,
    path: str,
    paths: list[str],
    key_hint: str | None,
    active: set[int],
    depth: int,
) -> Any:
    if isinstance(item, InProcessAssertionView):
        return _redact_value(item.value, config, path, paths, key_hint, active, depth + 1)
    if key_hint and _is_sensitive_key(key_hint, config.sensitive_keys):
        paths.append(path)
        return REDACTED
    if item is None or isinstance(item, (str, bool, int)):
        return _redact_string(item, config, path, paths) if isinstance(item, str) else item
    if isinstance(item, float):
        if item != item or item in (float("inf"), float("-inf")):
            raise RedactionError(path, "non-finite number")
        return item
    if isinstance(item, (datetime, date, time)):
        return _redact_string(item.isoformat(), config, path, paths)
    if isinstance(item, Enum):
        return _redact_value(item.value, config, path, paths, key_hint, active, depth + 1)
    # Pydantic's SecretStr/SecretBytes retain their wrapped value in
    # model_dump(mode="python"). Keep it out regardless of the field name.
    if type(item).__module__.startswith("pydantic") and type(item).__name__ in {"SecretStr", "SecretBytes"}:
        paths.append(path)
        return REDACTED
    if isinstance(item, BaseException):
        try:
            message = str(item)
        except BaseException:
            raise RedactionError(path, "exception message failed") from None
        result: dict[str, Any] = {
            "type": type(item).__name__,
            "message": _redact_string(message, config, f"{path}.message", paths),
            "args": _redact_value(tuple(item.args), config, f"{path}.args", paths, None, active, depth + 1),
        }
        details = getattr(item, "details", None)
        if details is not None:
            result["details"] = _redact_value(details, config, f"{path}.details", paths, None, active, depth + 1)
        return result
    if isinstance(item, BaseModel):
        try:
            dumped = item.model_dump(mode="python")
        except BaseException:
            raise RedactionError(path, "model serialization failed") from None
        return _redact_value(dumped, config, path, paths, key_hint, active, depth + 1)
    if isinstance(item, Mapping):
        result_mapping: dict[str, Any] = {}
        projected_keys: set[str] = set()
        for raw_key, raw_value in item.items():
            if isinstance(raw_key, str):
                key = _redact_string(raw_key, config, f"{path}.<key>", paths)
            else:
                key = _safe_repr(raw_key, config, f"{path}.<key>", paths)
            if key in projected_keys:
                raise RedactionError(path, "mapping key collision after redaction")
            projected_keys.add(key)
            child_path = f"{path}.{key}"
            result_mapping[key] = _redact_value(raw_value, config, child_path, paths, key, active, depth + 1)
        return result_mapping
    if isinstance(item, list):
        return [_redact_value(value, config, f"{path}[{index}]", paths, None, active, depth + 1) for index, value in enumerate(item)]
    if isinstance(item, tuple):
        # HTTP header collections are commonly represented as a sequence of
        # ``(name, value)`` pairs rather than a mapping.
        if len(item) == 2 and isinstance(item[0], str) and _is_header_pair_name(item[0], config.sensitive_keys):
            header_name = _redact_string(item[0], config, f"{path}[0]", paths)
            paths.append(f"{path}[1]")
            return (header_name, REDACTED)
        return tuple(_redact_value(value, config, f"{path}[{index}]", paths, None, active, depth + 1) for index, value in enumerate(item))
    if isinstance(item, (set, frozenset)):
        values = [_redact_value(value, config, f"{path}[]", paths, None, active, depth + 1) for value in item]
        try:
            return sorted(values, key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        except (TypeError, ValueError):
            raise RedactionError(path, "set projection is not deterministic") from None
    if isinstance(item, (bytes, bytearray, memoryview)):
        raw = bytes(item)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Opaque bytes cannot be searched safely for configured byte
            # secrets and must never be base64-encoded into persisted output.
            raise RedactionError(path, "opaque binary cannot be safely redacted") from None
        return _redact_string(text, config, path, paths)
    if isinstance(item, PurePath):
        return _redact_string(str(item), config, path, paths)
    # Never call str/repr on arbitrary objects and return it.  That is a common
    # source of best-effort leaks from SDK and provider objects.
    raise RedactionError(path, "unsupported value type")


def _redact_value(
    item: Any,
    config: RedactionConfig,
    path: str,
    paths: list[str],
    key_hint: str | None = None,
    active: set[int] | None = None,
    depth: int = 0,
) -> Any:
    """Traverse one value and convert every hostile failure to a safe error."""

    if depth > _MAX_NESTING_DEPTH:
        raise RedactionError(path, "maximum nesting depth exceeded")
    active_ids = active if active is not None else set()
    try:
        track = isinstance(item, (InProcessAssertionView, BaseModel, Mapping, list, tuple, set, frozenset))
        identity = id(item)
        if track:
            if identity in active_ids:
                raise RedactionError(path, "cyclic value cannot be safely redacted")
            active_ids.add(identity)
        try:
            return _walk_value(item, config, path, paths, key_hint, active_ids, depth)
        finally:
            if track:
                active_ids.discard(identity)
    except RedactionError:
        raise
    except BaseException:
        # Never propagate a custom __getitem__, items(), model_dump(), args,
        # or iterator exception: its message/repr may contain a secret.
        raise RedactionError(path, "value traversal failed") from None


def redact_result(
    value: Any,
    *,
    config: RedactionConfig | None = None,
    secrets: Iterable[str] | None = None,
    path: str = "$",
) -> RedactionResult:
    """Return a typed, fail-closed redaction result."""

    if config is not None and secrets is not None:
        raise ValueError("pass config or secrets, not both")
    if config is None:
        if secrets is None:
            config = RedactionConfig.from_environment()
        else:
            config = RedactionConfig(secrets=_clean_values(secrets), include_environment=False)
    paths: list[str] = []
    redacted = _redact_value(value, config, path, paths)
    return RedactionResult(redacted, tuple(dict.fromkeys(paths)))


def redact(
    value: Any,
    *,
    secrets: set[str] | None = None,
    path: str = "$",
    config: RedactionConfig | None = None,
) -> tuple[Any, list[str]]:
    """Compatibility wrapper returning ``(value, changed_paths)``."""

    result = redact_result(value, config=config, secrets=secrets, path=path)
    return result.value, list(result.paths)


def redact_artifact_bytes(
    value: bytes | bytearray | memoryview,
    *,
    config: RedactionConfig | None = None,
    path: str = "$",
) -> ArtifactBytesResult:
    """Redact configured byte canaries while preserving other binary data.

    Secret strings are UTF-8 encoded and replaced longest-first, making
    overlapping configured values deterministic.  Unlike JSON/API projection,
    this artifact-only API permits opaque non-secret binary and returns it
    unchanged.  Non-byte inputs and hostile byte conversion fail closed.
    """

    if config is None:
        config = RedactionConfig.from_environment()
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise RedactionError(path, "artifact value is not bytes")
    try:
        original = bytes(value)
    except BaseException:
        raise RedactionError(path, "artifact bytes could not be read") from None
    output = original
    patterns = sorted(
        {
            secret.encode("utf-8")
            for secret in set(config.secrets).union(config.credential_file_contents)
            if secret
        },
        key=lambda item: (-len(item), item),
    )
    redacted_count = 0
    for pattern in patterns:
        count = output.count(pattern)
        if count:
            output = output.replace(pattern, REDACTED.encode("utf-8"))
            redacted_count += count
    return ArtifactBytesResult(output, redacted_count, len(original))


Projection = Literal["persistence", "export", "log", "api", "ui", "raw_evidence"]


def project_redacted(value: Any, *, projection: Projection, config: RedactionConfig | None = None, path: str = "$") -> Any:
    """Create any supported persisted/export/log/API/UI/raw-evidence view."""

    if projection not in {"persistence", "export", "log", "api", "ui", "raw_evidence"}:
        raise ValueError("unknown redaction projection")
    return redact_result(value, config=config, path=path).value


def redact_raw_evidence(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> Any:
    """Redact raw protocol/harness evidence before it can be persisted."""

    return project_redacted(value, projection="raw_evidence", config=config, path=path)


def redact_for_persistence(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> Any:
    return project_redacted(value, projection="persistence", config=config, path=path)


def redact_for_export(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> Any:
    return project_redacted(value, projection="export", config=config, path=path)


def redact_for_log(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> Any:
    return project_redacted(value, projection="log", config=config, path=path)


def redact_for_api(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> Any:
    return project_redacted(value, projection="api", config=config, path=path)


def redact_for_ui(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> Any:
    return project_redacted(value, projection="ui", config=config, path=path)


def serialize_redacted(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> Any:
    """Return a JSON-compatible redacted projection, never the original value."""

    projected = project_redacted(value, projection="api", config=config, path=path)
    try:
        json.dumps(projected, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise RedactionError(path, "projection is not JSON serializable") from None
    return projected


def redact_model_json(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> Any:
    """Safely project a Pydantic model without JSON-serializing it first.

    ``model_dump(mode="python")`` preserves hostile/opaque values long enough
    for the fail-closed walker to reject or redact them. JSON serialization is
    performed only after redaction by :func:`serialize_redacted`.
    """

    if isinstance(value, BaseModel):
        try:
            value = value.model_dump(mode="python")
        except BaseException:
            raise RedactionError(path, "model serialization failed") from None
    return serialize_redacted(value, config=config, path=path)


def redacted_json(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> str:
    """Serialize a redacted projection as strict JSON."""

    projected = serialize_redacted(value, config=config, path=path)
    try:
        return json.dumps(projected, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise RedactionError(path, "projection is not JSON serializable") from None


def redact_repr(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> str:
    """Return a safe repr for diagnostics without returning an original repr."""

    if config is None:
        config = RedactionConfig.from_environment()
    paths: list[str] = []
    return _safe_repr(value, config, path, paths)


__all__ = [
    "ArtifactBytesResult", "InProcessAssertionView", "Projection", "REDACTED",
    "RedactionConfig", "RedactionError", "RedactionResult", "assertion_view",
    "known_secret_values", "redact", "redact_artifact_bytes", "redact_for_api",
    "redact_for_export", "redact_for_log",
    "redact_for_persistence", "redact_for_ui", "redact_raw_evidence",
    "redact_model_json", "redact_repr", "redact_result", "redacted_json", "serialize_redacted",
    "project_redacted",
]
