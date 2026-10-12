"""Replay wire calls against a fresh Store through the same Surface the server uses."""

from __future__ import annotations

from _backend.store import Store
from _server.surfaces import build_surface


def replay(
    variant: str, nonce: str, calls: list[tuple[str, dict]]
) -> tuple[Store, list[tuple[str, bool]]]:
    store, surface = Store(7, nonce), build_surface(variant)
    return store, [surface.dispatch(store, name, args) for name, args in calls]
