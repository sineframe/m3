"""Private scoped factory for toolkit default execution stores."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Any

StoreFactory = Callable[[], Any]


@dataclass(frozen=True)
class StoreFactoryToken:
    identifier: int
    previous_factory: StoreFactory | None
    previous_token: "StoreFactoryToken | None" = None


_lock = RLock()
_counter = 0
_factory: StoreFactory | None = None
_token: StoreFactoryToken | None = None
_run_id_factory: Callable[[], Any] | None = None


def install_default_store_factory(factory: StoreFactory) -> StoreFactoryToken:
    if not callable(factory):
        raise TypeError("store factory must be callable")
    global _counter, _factory, _token
    with _lock:
        _counter += 1
        token = StoreFactoryToken(_counter, _factory, _token)
        _factory, _token = factory, token
        return token


def restore_default_store_factory(token: StoreFactoryToken) -> None:
    global _factory, _token
    with _lock:
        if _token == token:
            _factory, _token = token.previous_factory, token.previous_token


def make_default_store() -> Any | None:
    with _lock:
        factory = _factory
    return factory() if factory is not None else None


def install_default_run_id_factory(factory: Callable[[], Any]) -> Callable[[], Any] | None:
    global _run_id_factory
    with _lock:
        previous = _run_id_factory
        _run_id_factory = factory
        return previous


def restore_default_run_id_factory(previous: Callable[[], Any] | None) -> None:
    global _run_id_factory
    with _lock:
        _run_id_factory = previous


def make_default_run_id() -> Any | None:
    with _lock:
        factory = _run_id_factory
    return factory() if factory is not None else None


__all__ = ["StoreFactoryToken", "install_default_store_factory", "restore_default_store_factory", "make_default_store", "install_default_run_id_factory", "restore_default_run_id_factory", "make_default_run_id"]
