"""Fixed workspace operations executed only by owned preparation children."""

from __future__ import annotations

import base64
import os
import socket
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .storage import ArtifactStore, InMemoryArtifactStore, TemporaryArtifactStore
from .trace.redaction import RedactionConfig
from .types import ArtifactPolicy, ArtifactRef, ExecutionOutcome, WorkspacePolicy
from .workspace import (
    _MAX_ARTIFACT_BYTES,
    WorkspaceCapture,
    WorkspaceDiff,
    WorkspaceEntry,
    WorkspaceError,
    WorkspaceManager,
)


@dataclass(frozen=True)
class CapturedWorkspace:
    capture: WorkspaceCapture
    blobs: tuple[tuple[ArtifactRef, bytes], ...] = field(default=(), repr=False)


def artifact_backend(store: ArtifactStore, *, collect: bool) -> dict[str, Any]:
    """Pin supported backend settings before any bounded workspace job starts."""
    from .errors import UnsupportedFeature
    from .storage.sqlite import SQLiteArtifactStore

    if not collect:
        return {"kind": "memory", "redaction": {"include_environment": False}}
    if not isinstance(store, (InMemoryArtifactStore, SQLiteArtifactStore)) or type(
        store
    ) not in {InMemoryArtifactStore, TemporaryArtifactStore, SQLiteArtifactStore}:
        raise UnsupportedFeature(
            "bounded workspace capture requires a built-in artifact store"
        )
    config = store._redaction_config
    if config.include_environment:
        config = RedactionConfig.from_environment(
            secrets=config.secrets,
            sensitive_keys=config.sensitive_keys,
            credential_file_contents=config.credential_file_contents,
        )
    redaction = {
        key: sorted(value) if isinstance(value, frozenset) else value
        for key, value in asdict(config).items()
    }
    if isinstance(store, SQLiteArtifactStore):
        return {
            "kind": "sqlite",
            "database": os.path.abspath(store.database),
            "blob_root": str(store.blob_root),
            "busy_timeout_ms": store.busy_timeout_ms,
            "wal": store.wal_requested,
            "redaction": redaction,
        }
    if isinstance(store, TemporaryArtifactStore):
        return {"kind": "temporary", "root": str(store.root), "redaction": redaction}
    return {"kind": "memory", "redaction": redaction}


def _artifact_store(settings: dict[str, Any]) -> ArtifactStore:
    config = RedactionConfig(**settings["redaction"])
    if settings["kind"] == "sqlite":
        from .storage.sqlite import SQLiteArtifactStore

        return SQLiteArtifactStore(
            settings["database"],
            settings["blob_root"],
            busy_timeout_ms=settings["busy_timeout_ms"],
            wal=settings["wal"],
            config=config,
        )
    if settings["kind"] == "temporary":
        return TemporaryArtifactStore(settings["root"], config=config)
    if settings["kind"] == "memory":
        return InMemoryArtifactStore(config=config)
    raise WorkspaceError("unsupported workspace artifact backend")


def capture_workspace(
    request: dict[str, Any], channel: socket.socket, deadline: float
) -> None:
    from ._bootstrap_operations import _send_private

    store = _artifact_store(request["backend"])
    manager = WorkspaceManager(
        WorkspacePolicy.model_validate(request["policy"]),
        request["execution_id"],
        artifact_policy=ArtifactPolicy(request["artifact_policy"]),
        declared_artifacts=request["declared_artifacts"],
        artifact_store=store,
        include_excluded=request["include_excluded"],
        acknowledge_risky_inclusion=request["include_excluded"],
    )
    manager._root = Path(request["root"])
    if not _same_directory(manager._root, request["identity"]):
        raise WorkspaceError("workspace disappeared before capture")
    manager._baseline = {
        key: (value[0], value[1]) for key, value in request["baseline"].items()
    }
    capture = manager.capture(ExecutionOutcome(request["outcome"]))
    for entry in capture.diff.entries:
        _send_private(
            channel, {"version": 1, "event": "entry", "entry": asdict(entry)}, deadline
        )
    for ref in capture.artifacts:
        blob = (
            store._blobs[ref.sha256]
            if isinstance(store, InMemoryArtifactStore)
            else None
        )
        _send_private(
            channel,
            {
                "version": 1,
                "event": "artifact",
                "ref": ref.model_dump(mode="json"),
                "compressed_size": len(blob) if blob is not None else None,
            },
            deadline,
        )
        if blob is not None:
            for offset in range(0, len(blob), 512 * 1024):
                _send_private(
                    channel,
                    {
                        "version": 1,
                        "event": "chunk",
                        "bytes": base64.b64encode(
                            blob[offset : offset + 512 * 1024]
                        ).decode("ascii"),
                    },
                    deadline,
                )
        _send_private(channel, {"version": 1, "event": "artifact_end"}, deadline)
    _send_private(
        channel,
        {
            "version": 1,
            "event": "capture_end",
            "limitations": capture.limitations,
            "diff_limitations": capture.diff.limitations,
        },
        deadline,
    )


def receive_workspace_capture(
    channel: socket.socket, deadline: float
) -> CapturedWorkspace:
    from ._bootstrap_operations import PrivateOperationError, _receive_private

    entries: list[WorkspaceEntry] = []
    refs: list[ArtifactRef] = []
    blobs: list[tuple[ArtifactRef, bytes]] = []
    current: ArtifactRef | None = None
    size: int | None = None
    content = bytearray()
    while True:
        frame, descriptor = _receive_private(channel, deadline)
        if descriptor is not None:
            os.close(descriptor)
            raise PrivateOperationError("unexpected workspace capture handle")
        event = frame.get("event")
        if event == "entry" and current is None and not refs:
            entries.append(WorkspaceEntry(**frame["entry"]))
        elif event == "artifact" and current is None:
            current = ArtifactRef.model_validate(frame["ref"])
            size = frame["compressed_size"]
            if size is not None and (
                type(size) is not int
                or not 0 <= size <= _MAX_ARTIFACT_BYTES + 1024 * 1024
            ):
                raise PrivateOperationError("workspace artifact exceeds capture limit")
            content = bytearray()
        elif event == "chunk" and current is not None and size is not None:
            chunk = base64.b64decode(frame["bytes"], validate=True)
            if len(chunk) > 512 * 1024 or len(content) + len(chunk) > size:
                raise PrivateOperationError("workspace artifact stream exceeds limit")
            content.extend(chunk)
        elif event == "artifact_end" and current is not None:
            if size is not None:
                if len(content) != size:
                    raise PrivateOperationError("workspace artifact stream ended early")
                blobs.append((current, bytes(content)))
            refs.append(current)
            current = None
        elif event == "capture_end" and current is None:
            return CapturedWorkspace(
                WorkspaceCapture(
                    WorkspaceDiff(tuple(entries), tuple(frame["diff_limitations"])),
                    tuple(refs),
                    tuple(frame["limitations"]),
                ),
                tuple(blobs),
            )
        else:
            raise PrivateOperationError("invalid workspace capture stream")


def _same_directory(path: Path, expected: object) -> bool:
    try:
        current = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISDIR(current.st_mode)
        or [current.st_dev, current.st_ino] != expected
    ):
        raise WorkspaceError("workspace directory identity changed")
    return True


def cleanup_workspace(request: dict[str, Any]) -> None:
    manager = WorkspaceManager(
        WorkspacePolicy.model_validate(request["policy"]), "cleanup"
    )
    manager._root = Path(request["root"])
    manager._owned_root = request["owned"]
    preparation_root = request["preparation_root"]
    manager._preparation_root = (
        Path(preparation_root) if preparation_root is not None else None
    )
    root_exists = _same_directory(manager._root, request["identity"])
    staging_exists = manager._preparation_root is not None and _same_directory(
        manager._preparation_root, request["preparation_identity"]
    )
    if root_exists:
        manager._cleanup(bounded=True)
    elif staging_exists:
        assert manager._preparation_root is not None
        manager._preparation_root.rmdir()
