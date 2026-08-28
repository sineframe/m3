"""Serialization for durable specifications and profile values.

Evidence redaction is intentionally lossy: a credential-shaped field is
replaced with ``[REDACTED]``.  That projection is not suitable for values that
will later be executed.  This module provides the separate, fail-closed
projection used by durable specifications, profiles, and queued commands.

The only credential value accepted at a durable boundary is a
``SecretReference`` (or its canonical JSON descriptor). References are never
resolved here; resolution belongs to the worker that owns execution. Ordinary
strings are projected with the supplied ``RedactionConfig`` so a known canary
cannot be persisted through a non-sensitive field. Legacy placeholders are
accepted only as exactly ``${VAR}`` or ``Bearer ${VAR}``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from enum import Enum
import json
import re
from pathlib import PurePath
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel

from ..trace.redaction import RedactionConfig, RedactionError, is_sensitive_key, is_sensitive_query_key, redact_for_persistence
from ..types import SecretReference


_ENVIRONMENT_REFERENCE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
_NAMED_VALUE_CONTAINERS = frozenset(
    {"mcpservers", "servers", "serverbindings", "configurations"}
)
_REFERENCE_VALUE_CONTAINERS = frozenset(
    {"credentials", "credential", "credential_references", "secret_refs", "secret_references", "auth_credentials"}
)
_MAX_DEPTH = 64


class DurableSerializationError(ValueError):
    """A value cannot safely cross a durable specification boundary."""

    def __init__(self, reason: str = "durable value is invalid") -> None:
        # Do not include a field path or the rejected value: either may contain
        # credentials supplied by an untrusted profile.
        self.reason = reason
        super().__init__(reason)


def _reference_descriptor(value: Any) -> dict[str, str] | None:
    if isinstance(value, SecretReference):
        return {"source": value.source, "name": value.name}
    if isinstance(value, Mapping):
        try:
            if set(value) != {"source", "name"}:
                return None
            source, name = value.get("source"), value.get("name")
        except BaseException:
            # Let the normal mapping walker convert hostile mapping behavior
            # into the value-free durable error below.
            return None
        if (
            source in {"environment", "provider"}
            and isinstance(name, str)
            and 1 <= len(name) <= 256
        ):
            return {"source": source, "name": name}
    return None


def _placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(
        _ENVIRONMENT_REFERENCE.fullmatch(value)
        or re.fullmatch(r"Bearer \$\{[A-Za-z_][A-Za-z0-9_]*\}", value)
    )


def _walk(
    value: Any,
    *,
    config: RedactionConfig,
    key_hint: str | None = None,
    path: str = "$",
    active: set[int] | None = None,
    depth: int = 0,
    credential_container: bool = False,
) -> Any:
    if depth > _MAX_DEPTH:
        raise DurableSerializationError("durable value exceeds maximum nesting depth")
    reference = _reference_descriptor(value)
    if reference is not None:
        return reference
    if credential_container:
        if _placeholder(value):
            return value
        if not isinstance(value, (Mapping, list, tuple)):
            raise DurableSerializationError("credential-bearing field requires a SecretReference")
    if key_hint is not None and is_sensitive_key(key_hint, config.sensitive_keys):
        # The legacy profile format permits exactly an environment reference
        # or the documented case-sensitive HTTP form ``Bearer ${VAR}```.
        # Arbitrary prefixes/suffixes remain credential literals and fail.
        if _placeholder(value):
            return value
        if isinstance(value, (Mapping, list, tuple)) and key_hint.casefold() in _REFERENCE_VALUE_CONTAINERS:
            credential_container = True
        else:
            raise DurableSerializationError("credential-bearing field requires a SecretReference")

    tracked = isinstance(value, (BaseModel, Mapping, list, tuple, set, frozenset))
    active_values = active if active is not None else set()
    identity = id(value)
    if tracked:
        if identity in active_values:
            raise DurableSerializationError("durable value contains a cycle")
        active_values.add(identity)
    try:
        return _walk_inner(
            value,
            config=config,
            key_hint=key_hint,
            path=path,
            active=active_values,
            depth=depth,
            credential_container=credential_container,
        )
    finally:
        if tracked:
            active_values.discard(identity)


def _walk_inner(
    value: Any,
    *,
    config: RedactionConfig,
    key_hint: str | None,
    path: str,
    active: set[int],
    depth: int,
    credential_container: bool,
) -> Any:
    if isinstance(value, str):
        if value.lower().startswith(("http://", "https://")):
            try:
                parsed = urlsplit(value)
                if parsed.username is not None or parsed.password is not None or any(
                    is_sensitive_query_key(name, config.sensitive_keys)
                    for name, _item in parse_qsl(parsed.query, keep_blank_values=True)
                ):
                    raise DurableSerializationError("durable URL cannot contain credentials")
            except DurableSerializationError:
                raise
            except (TypeError, ValueError):
                raise DurableSerializationError("durable value is invalid") from None
        try:
            # Known canaries are safe to redact in ordinary durable strings;
            # typed references above are returned before reaching here.
            return redact_for_persistence(value, config=config, path=path)
        except RedactionError:
            raise DurableSerializationError("durable value is invalid") from None
    if value is None or isinstance(value, (bool, int)):
        if credential_container:
            raise DurableSerializationError("credential-bearing field requires a SecretReference")
        return value
    if isinstance(value, float):
        if credential_container or not value == value or value in (float("inf"), float("-inf")):
            raise DurableSerializationError("durable value is invalid")
        return value
    if isinstance(value, (datetime, date, time)):
        if credential_container:
            raise DurableSerializationError("credential-bearing field requires a SecretReference")
        return value.isoformat()
    if isinstance(value, Enum):
        return _walk(
            value.value,
            config=config,
            key_hint=key_hint,
            path=path,
            active=active,
            depth=depth + 1,
            credential_container=credential_container,
        )
    if isinstance(value, BaseModel):
        try:
            return _walk(
                value.model_dump(mode="python"),
                config=config,
                key_hint=key_hint,
                path=path,
                active=active,
                depth=depth + 1,
                credential_container=credential_container,
            )
        except DurableSerializationError:
            raise
        except BaseException:
            raise DurableSerializationError("durable value is invalid") from None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        try:
            items = tuple(value.items())
        except BaseException:
            raise DurableSerializationError("durable value is invalid") from None
        for raw_key, raw_value in items:
            if not isinstance(raw_key, str) or not raw_key:
                raise DurableSerializationError("durable value is invalid")
            # Keys inside a named-server/container mapping are identifiers,
            # not field names. A server called ``secret-server`` must not be
            # mistaken for a credential-bearing field.
            child_hint = (
                None
                if key_hint is not None and key_hint.casefold() in _NAMED_VALUE_CONTAINERS
                else raw_key
            )
            result[raw_key] = _walk(
                raw_value,
                config=config,
                key_hint=child_hint,
                path=f"{path}.{raw_key}",
                active=active,
                depth=depth + 1,
                credential_container=credential_container,
            )
        return result
    if isinstance(value, (list, tuple)):
        return [
            _walk(
                item,
                config=config,
                path=f"{path}[{index}]",
                active=active,
                depth=depth + 1,
                credential_container=credential_container,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, (set, frozenset)):
        if credential_container:
            raise DurableSerializationError("credential-bearing field requires a SecretReference")
        try:
            return sorted(
                (
                    _walk(
                        item,
                        config=config,
                        path=f"{path}[]",
                        active=active,
                        depth=depth + 1,
                    )
                    for item in value
                ),
                key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
            )
        except (TypeError, ValueError):
            raise DurableSerializationError("durable value is invalid") from None
    if isinstance(value, PurePath):
        if credential_container:
            raise DurableSerializationError("credential-bearing field requires a SecretReference")
        return str(value)
    raise DurableSerializationError("durable value is invalid")


def serialize_durable(value: Any, *, config: RedactionConfig | None = None, path: str = "$") -> Any:
    """Return strict JSON-compatible durable data while preserving references."""

    durable_config = config if config is not None else RedactionConfig.from_environment()
    projected = _walk(value, config=durable_config, path=path)
    try:
        json.dumps(projected, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise DurableSerializationError("durable value is invalid") from None
    return projected


__all__ = ["DurableSerializationError", "serialize_durable"]
