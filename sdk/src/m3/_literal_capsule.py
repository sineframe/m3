"""Private integration contract for framework-owned Windows capsule operations.

A backend must run filesystem calls inside the reusable owned helper, never
on a controller thread. This module deliberately supplies no Windows backend:
the Job Object ownership implementation is a separate prerequisite.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import NoReturn, Protocol


class CapsuleBytes:
    """One-consumer private bytes; excluded from repr and serialization."""

    __slots__ = ("_value",)

    def __init__(self, value: bytes) -> None:
        if not isinstance(value, bytes) or not 0 < len(value) <= 8 * 1024 * 1024:
            raise ValueError("invalid private capsule size")
        self._value: bytes | None = value

    def __repr__(self) -> str:
        return "CapsuleBytes(<private bootstrap bytes>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("private capsule bytes cannot be serialized")

    def consume(self) -> bytes:
        value, self._value = self._value, None
        if value is None:
            raise RuntimeError("private capsule bytes already consumed")
        return value


class OwnedCapsuleOperations(Protocol):
    """A run-owned helper with one absolute deadline and fenced publication.

    Create/read/write/delete/security checks are helper operations. Roots/files
    have protected current-token-user ACLs and non-inheritable handles. Reject
    remote/reparse paths; read and unlink the same verified open file. Consume
    CapsuleBytes through private bounded JSON/base64 helper IPC only, with no
    ordinary preparation metadata, events, logs or persisted results.

    Return only after accepted writes settle, all handles close and the fence
    remains unset before deadline. A timeout returns boundedly; the provider
    retains pending jobs and private roots, prevents late publication, and
    releases them only after confirmed writer death/completion. close fences
    pending writes before cancellation; cleanup_complete must remain false
    while any operation/resource is still owned. No uncancellable thread or
    generic dictionary-result fallback satisfies this protocol.
    """

    def create_root(self, parent: Path, *, deadline: float) -> Path: ...

    def write(
        self,
        path: Path,
        contents: CapsuleBytes,
        *,
        deadline: float,
        fence: threading.Event,
    ) -> None: ...

    def read_and_unlink(self, path: Path, *, deadline: float) -> CapsuleBytes: ...

    def remove(self, path: Path, *, deadline: float) -> None: ...

    def close(self, *, deadline: float) -> None: ...

    @property
    def cleanup_complete(self) -> bool: ...
