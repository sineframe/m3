"""Run-owned configuration shared by kits without repeating discovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from .configuration import Config, _with_overrides, load_config

if TYPE_CHECKING:
    from ._literal_store import RunLiteralStore


@dataclass(frozen=True, repr=False)
class ConfigurationContext:
    config: Config = field(repr=False)
    literal_registry: RunLiteralStore | None = field(default=None, repr=False)

    def __getstate__(self) -> None:
        raise TypeError("invocation configuration requires private snapshot transport")


@dataclass(repr=False)
class ConfigurationToken:
    context: ConfigurationContext
    previous: ConfigurationToken | None
    active: bool = True


_lock = RLock()
_current: ConfigurationToken | None = None


def install_configuration(context: ConfigurationContext) -> ConfigurationToken:
    global _current
    with _lock:
        token = ConfigurationToken(context, _current)
        _current = token
        return token


def restore_configuration(token: ConfigurationToken) -> None:
    global _current
    with _lock:
        token.active = False
        while _current is not None and not _current.active:
            _current = _current.previous


def resolve_configuration(
    explicit: Config | Mapping[str, Any] | None,
    *,
    env: Mapping[str, str] | None,
    cwd: str | Path | None,
) -> ConfigurationContext:
    with _lock:
        context = _current.context if _current is not None else None
    if context is None:
        config = (
            explicit
            if isinstance(explicit, Config)
            else load_config(explicit, env=env, cwd=cwd)
        )
        return ConfigurationContext(config)
    if explicit is None or explicit is context.config:
        return context
    return ConfigurationContext(
        _with_overrides(context.config, explicit), context.literal_registry
    )
