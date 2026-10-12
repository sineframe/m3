"""Private ownership interface for preparing a native login home."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class PrivateAuthPayload(Protocol):
    def consume(self) -> bytes: ...


class NativeAuthHome(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def path(self) -> Path: ...


class NativeAuthOperations(Protocol):
    """Framework-owned operations; caller code never performs native file IO."""

    def read_native_auth(
        self, path: Path, *, deadline: float
    ) -> PrivateAuthPayload | None: ...

    def stage_native_auth(
        self, value: PrivateAuthPayload, *, deadline: float
    ) -> NativeAuthHome: ...

    def close(self, *, deadline: float) -> None: ...

    @property
    def cleanup_complete(self) -> bool: ...


class PinnedAuthPayload:
    """One private transfer from session memory to its owned file operation."""

    def __init__(self, value: bytes) -> None:
        self._value: bytes | None = value

    def __repr__(self) -> str:
        return "PinnedAuthPayload(<private>)"

    def __getstate__(self) -> None:
        raise TypeError("native auth bytes cannot be serialized")

    def consume(self) -> bytes:
        value, self._value = self._value, None
        if value is None:
            raise RuntimeError("native auth bytes already consumed")
        return value
