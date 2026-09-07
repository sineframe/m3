"""Deterministic, secret-safe values for snapshot assertions.

The snapshot boundary is deliberately independent from pytest.  It returns
ordinary JSON-compatible dictionaries, lists, and scalar values so pytest
snapshot plugins (and other snapshot consumers) can consume the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
import re as _re
from typing import Any as _Any, Final as _Final, TypeAlias as _TypeAlias
from urllib.parse import urlsplit as _urlsplit, urlunsplit as _urlunsplit

from .trace.redaction import RedactionConfig as _RedactionConfig, redact_for_export as _redact_for_export


_DEFAULT_OMITTED_FIELDS: _Final[frozenset[str]] = frozenset(
    {
        "run_id", "execution_id", "timestamp", "timestamps", "created_at",
        "started_at", "start_time", "finished_at", "ended_at", "end_time",
        "updated_at", "duration", "duration_ms", "elapsed_ms", "cost",
        "latency", "latency_ms", "input_cost", "output_cost", "total_cost", "cost_usd", "path", "paths", "port", "ports", "session_id",
        "turn_id", "trace_id", "run_ids", "vendor_metadata", "provider_metadata",
    }
)

_SnapshotValue: _TypeAlias = None | bool | int | float | str | list["_SnapshotValue"] | dict[str, "_SnapshotValue"]


def _field_name(value: str) -> str:
    return _re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


@_dataclass(frozen=True, slots=True)
class SnapshotOptions:
    """Controls stable snapshot omission and explicit field opt-ins.

    ``include_fields`` accepts either a field name (for example
    ``"duration_ms"``) or a structural path (for example
    ``"$.trace.duration_ms"``).  A field opt-in is explicit and does not
    affect redaction.
    """

    include_fields: frozenset[str] = frozenset()
    omitted_fields: frozenset[str] = _DEFAULT_OMITTED_FIELDS

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_fields", frozenset(_field_name(item) for item in self.include_fields))
        object.__setattr__(self, "omitted_fields", frozenset(_field_name(item) for item in self.omitted_fields))


def _included(key: str, path: str, options: SnapshotOptions) -> bool:
    normalized_key = _field_name(key)
    normalized_path = _field_name(path.replace("$", ""))
    without_snapshot = normalized_path.removeprefix("snapshot_")
    return normalized_key in options.include_fields or normalized_path in options.include_fields or without_snapshot in options.include_fields


def _without_url_port(value: str, path: str, options: SnapshotOptions) -> str:
    """Remove unstable URL ports unless the caller explicitly opts them in."""

    leaf = path.rsplit(".", 1)[-1]
    if (
        "port" in options.include_fields
        or _included(leaf, path, options)
        or not value.lower().startswith(("http://", "https://"))
    ):
        return value
    try:
        parts = _urlsplit(value)
        if parts.port is None:
            return value
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        userinfo = ""
        if parts.username is not None:
            userinfo = parts.username
            if parts.password is not None:
                userinfo += f":{parts.password}"
            userinfo += "@"
        return _urlunsplit((parts.scheme, f"{userinfo}{host}", parts.path, parts.query, parts.fragment))
    except ValueError:
        # URL syntax errors are already handled by the shared redaction
        # boundary.  Keep this defensive projection value-free.
        return value


def _normalize(value: _Any, *, path: str, options: SnapshotOptions) -> _SnapshotValue | None:
    if isinstance(value, _Mapping):
        result: dict[str, _SnapshotValue] = {}
        for raw_key, raw_value in value.items():
            key = raw_key if isinstance(raw_key, str) else str(raw_key)
            child_path = f"{path}.{key}"
            if _field_name(key) in options.omitted_fields and not _included(key, child_path, options):
                continue
            normalized = _normalize(raw_value, path=child_path, options=options)
            result[key] = normalized
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [
            _normalize(item, path=f"{path}[{index}]", options=options)
            for index, item in enumerate(value)
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _without_url_port(value, path, options)
    # Redaction should have rejected this before stableization.  Keep a
    # defensive failure here so this module never silently returns an opaque
    # provider object to a snapshot plugin.
    raise TypeError("redacted snapshot contains an unsupported value")


def snapshot(
    value: _Any,
    *,
    include_fields: _Iterable[str] = (),
    omitted_fields: _Iterable[str] | None = None,
    options: SnapshotOptions | None = None,
    config: _RedactionConfig | None = None,
) -> _SnapshotValue:
    """Return a deterministic, redacted, JSON-compatible snapshot value."""

    if options is not None and (tuple(include_fields) or omitted_fields is not None):
        raise ValueError("pass options or field options, not both")
    selected = options or SnapshotOptions(
        include_fields=frozenset(include_fields),
        omitted_fields=_DEFAULT_OMITTED_FIELDS if omitted_fields is None else frozenset(omitted_fields),
    )
    redacted = _redact_for_export(value, config=config, path="$.snapshot")
    result = _normalize(redacted, path="$.snapshot", options=selected)
    if result is None:
        return None
    return result


__all__ = ["SnapshotOptions", "snapshot"]
