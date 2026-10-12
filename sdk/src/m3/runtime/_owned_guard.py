"""Fixed managed-runtime writer custody inside the private preparation owner."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import json
import os
import select
import signal
import socket
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .._artifact_custody import transfer_artifact_lease
from .._bootstrap_operations import (
    PrivateOperationError,
    _receive_private,
    _remaining,
    _send_private,
)
from .._preparation_worker import TERM_GRACE, _ForkJob
from .core import (
    SHA_RE,
    RuntimeManager,
    RuntimeValidationError,
    _entry_lock,
    _kernel_entry_lock,
    _lease_is_active,
    _legacy_entry_lock,
    _version,
    detect_target,
)
from .owned import RUNTIME_ENVIRONMENT, RuntimeAcquisition

_GENERATION = ".m3-owned-generation.json"


def _peer_open(channel: socket.socket) -> bool:
    readable, _, _ = select.select([channel], [], [], 0)
    return not readable or bool(channel.recv(1, socket.MSG_PEEK))


def _run_writer(writer: _ForkJob, channel: socket.socket, deadline: float) -> None:
    stopping: float | None = None
    try:
        readable, _, _ = select.select(
            [writer.ready, channel], [], [], _remaining(deadline)
        )
        if writer.ready not in readable or not _peer_open(channel):
            raise PrivateOperationError("managed runtime writer unavailable")
        if writer.ready.read(1) != b"s":
            raise PrivateOperationError("managed runtime writer group unavailable")
        writer.ready.close()
        writer.group_established = True
        writer.begin()
        while writer.poll() is None or writer.alive():
            if time.monotonic() >= deadline or not _peer_open(channel):
                if stopping is None:
                    stopping = time.monotonic()
                    writer.signal(signal.SIGTERM)
            if stopping is not None and time.monotonic() - stopping >= TERM_GRACE:
                writer.signal(signal.SIGKILL)
            select.select([], [], [], 0.01)
        if stopping is not None or writer.returncode != 0:
            raise PrivateOperationError("managed runtime writer failed")
        _remaining(deadline)
    finally:
        if writer.poll() is None or writer.alive():
            writer.signal(signal.SIGKILL)
            while writer.poll() is None or writer.alive():
                select.select([], [], [], 0.01)
        writer.ready.close()


def _stage(request: RuntimeAcquisition, root: Path) -> None:
    manager = RuntimeManager(
        cache_root=root / "cache",
        project_root=request.project_root,
        invocation_dir=request.invocation_dir,
        progress_file=request.progress_file,
    )
    lease = asyncio.run(manager.acquire(request.kind, request.selector))
    (root / "runtime-result.json").write_text(
        json.dumps(
            {"executable": str(lease.executable), "provenance": dict(lease.provenance)}
        ),
        encoding="utf-8",
    )


def _warm(
    manager: RuntimeManager, request: RuntimeAcquisition, deadline: float
) -> Path | None:
    kind = {"claude_code": "claude", "open-code": "opencode"}.get(
        request.kind, request.kind
    )
    if isinstance(request.selector, str):
        if request.selector == "latest":
            return None
        return manager._cached_version(
            kind, _version(request.selector), detect_target(kind), deadline=deadline
        )
    digest = request.selector.get("sha256")
    version = request.selector.get("version") or request.selector.get("tag")
    if (
        not isinstance(digest, str)
        or not SHA_RE.fullmatch(digest.lower())
        or version == "latest"
    ):
        return None
    version = _version(version)
    target = detect_target(kind, env=request.selector.get("env"))
    candidate = (
        manager.cache_root / kind / version / target / ("sha256-" + digest.lower())
    )
    if not candidate.is_dir():
        return None
    with _entry_lock(candidate, timeout=_remaining(deadline)):
        return candidate if manager._receipt_valid(candidate) else None


def _lease_handle(destination: Path) -> tuple[int, Path]:
    marker = destination / ("lease-kernel-" + uuid4().hex)
    descriptor = os.open(
        marker, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if os.write(descriptor, b"kernel-v1\n") != len(b"kernel-v1\n"):
            raise PrivateOperationError("managed runtime lease marker write failed")
        return descriptor, marker
    except BaseException:
        os.close(descriptor)
        raise


def execute_runtime_guard(
    channel: socket.socket, root: Path, deadline: float, custody: socket.socket
) -> None:
    """Publish accepted dead-writer output and transfer a kernel reader lease."""
    frame, unexpected = _receive_private(channel, deadline)
    if unexpected is not None:
        os.close(unexpected)
        raise PrivateOperationError("unexpected managed runtime handle")
    if frame.pop("version", None) != 1 or frame.pop("deadline", None) != deadline:
        raise PrivateOperationError("invalid managed runtime request")
    request = RuntimeAcquisition.from_private_frame(frame)
    for key in RUNTIME_ENVIRONMENT:
        os.environ.pop(key, None)
    os.environ.update(request.environment)
    manager = RuntimeManager(
        cache_root=request.cache_root,
        project_root=request.project_root,
        invocation_dir=request.invocation_dir,
        progress_file=request.progress_file,
    )
    source = _warm(manager, request, deadline)
    if not _peer_open(channel):
        raise PrivateOperationError("managed runtime caller ended")
    _remaining(deadline)
    staged = source is None
    if source is None:
        _run_writer(
            _ForkJob(lambda: _stage(request, root), output=False, gated=True),
            channel,
            deadline,
        )
        material = json.loads(
            (root / "runtime-result.json").read_text(encoding="utf-8")
        )
        provenance: dict[str, Any] = material["provenance"]
        executable = str(provenance["executable"])
        source = Path(material["executable"])
        for _ in Path(executable).parts:
            source = source.parent
    else:
        receipt = json.loads((source / "receipt.json").read_text(encoding="utf-8"))
        provenance = receipt["provenance"]
        executable = str(provenance["executable"])
    relative = source.relative_to(
        (root / "cache").resolve() if staged else manager.cache_root
    )
    destination = manager.cache_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    lease_descriptor = -1
    cold = False
    accepted = False
    with _kernel_entry_lock(destination, deadline=deadline) as guard:
        with _legacy_entry_lock(destination, timeout=_remaining(deadline)):
            try:
                _remaining(deadline)
                cold = not manager._receipt_valid(destination)
                transfer_artifact_lease(
                    custody,
                    guard,
                    root=manager.cache_root,
                    key=relative.as_posix(),
                    cold=cold,
                    deadline=deadline,
                    kind="managed-runtime",
                )
                if cold:
                    if source == destination:
                        raise RuntimeValidationError(
                            "managed runtime cache changed during resolution"
                        )
                    if destination.exists() and any(
                        _lease_is_active(path) for path in destination.glob("lease-*")
                    ):
                        raise RuntimeValidationError(
                            "managed runtime cache entry remains in use"
                        )

                    def publish() -> None:
                        for marker in source.glob("lease-*"):
                            marker.unlink()
                        (source / _GENERATION).write_text(
                            json.dumps({"generation": root.name, "accepted": False})
                        )
                        _remaining(deadline)
                        if destination.exists():
                            os.replace(
                                destination,
                                destination.with_name(
                                    destination.name + ".corrupt-" + root.name
                                ),
                            )
                        os.replace(source, destination)
                        _remaining(deadline)

                    _run_writer(
                        _ForkJob(publish, output=False, gated=True, keep_fds=(guard,)),
                        channel,
                        deadline,
                    )
                _remaining(deadline)
                if not _peer_open(channel):
                    raise PrivateOperationError("managed runtime caller ended")
                lease_descriptor, marker = _lease_handle(destination)
                payload = {
                    "executable": str(destination / executable),
                    "provenance": provenance,
                    "lease_path": str(marker),
                }
                _send_private(
                    channel,
                    {
                        "version": 1,
                        "bytes": base64.b64encode(
                            json.dumps(payload).encode()
                        ).decode(),
                    },
                    deadline,
                    lease_descriptor,
                )
                channel.settimeout(_remaining(deadline))
                if channel.recv(1) != b"a":
                    raise PrivateOperationError("managed runtime lease receipt ended")
                os.write(1, b'{"runtime_ready":true}\n')
                if channel.recv(1) != b"c":
                    raise PrivateOperationError("managed runtime acceptance ended")
                _remaining(deadline)
                if cold:
                    (destination / _GENERATION).write_text(
                        json.dumps({"generation": root.name, "accepted": True})
                    )
                _remaining(deadline)
                if not _peer_open(channel):
                    raise PrivateOperationError("managed runtime caller ended")
                accepted = True
            finally:
                if not accepted and cold and destination.exists():
                    metadata = destination / _GENERATION
                    if (
                        metadata.exists()
                        and json.loads(metadata.read_text()).get("generation")
                        == root.name
                    ):
                        os.replace(
                            destination,
                            destination.with_name(
                                destination.name + ".rejected-" + root.name
                            ),
                        )
                if lease_descriptor >= 0:
                    os.close(lease_descriptor)


def execute_models_guard(
    channel: socket.socket, root: Path, deadline: float, custody: socket.socket
) -> None:
    """Publish a validated immutable catalog after its owned writer has exited."""
    from .opencode_models import _read_models, _validate_models, prepare_models

    frame, descriptor = _receive_private(channel, deadline)
    if descriptor is not None:
        os.close(descriptor)
        raise PrivateOperationError("unexpected catalog handle")
    if (
        set(frame) != {"version", "deadline", "directory", "source"}
        or frame["version"] != 1
        or frame["deadline"] != deadline
        or not isinstance(frame["directory"], str)
        or (frame["source"] is not None and not isinstance(frame["source"], str))
    ):
        raise PrivateOperationError("invalid catalog request")
    directory = Path(frame["directory"]).expanduser().resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    snapshot = directory / "opencode-models.json"
    generation = snapshot.with_name(snapshot.name + ".generation")
    accepted = False
    published = False
    with _kernel_entry_lock(snapshot, deadline=deadline) as lock:
        with _legacy_entry_lock(snapshot, timeout=_remaining(deadline)):
            try:
                if snapshot.is_symlink() or generation.is_symlink():
                    raise RuntimeValidationError(
                        "OpenCode catalog must not be a symlink"
                    )
                if (
                    generation.exists()
                    and json.loads(generation.read_text()).get("accepted") is not True
                ):
                    raise RuntimeValidationError(
                        "OpenCode catalog publication is pending"
                    )
                reusable = snapshot.exists()
                if reusable:
                    raw = _read_models(snapshot)
                    try:
                        _validate_models(raw)
                    except RuntimeValidationError:
                        if frame["source"] is None:
                            raise
                        reusable = False
                transfer_artifact_lease(
                    custody,
                    lock,
                    root=directory,
                    key=snapshot.name,
                    cold=not reusable,
                    deadline=deadline,
                    kind="model-catalog",
                )
                if not reusable:

                    def stage_catalog() -> None:
                        prepare_models(root / "models", frame["source"])

                    _run_writer(
                        _ForkJob(stage_catalog, output=False, gated=True),
                        channel,
                        deadline,
                    )
                    staged = root / "models" / snapshot.name

                    def publish() -> None:
                        generation.write_text(
                            json.dumps({"accepted": False, "generation": root.name})
                        )
                        _remaining(deadline)
                        os.replace(staged, snapshot)
                        _remaining(deadline)

                    published = True
                    _run_writer(
                        _ForkJob(publish, output=False, gated=True, keep_fds=(lock,)),
                        channel,
                        deadline,
                    )
                    raw = _read_models(snapshot)
                payload = {
                    "path": str(snapshot),
                    "digest": hashlib.sha256(raw).hexdigest(),
                }
                _send_private(
                    channel,
                    {
                        "version": 1,
                        "bytes": base64.b64encode(
                            json.dumps(payload).encode()
                        ).decode(),
                    },
                    deadline,
                )
                os.write(1, b'{"runtime_ready":true}\n')
                channel.settimeout(_remaining(deadline))
                if channel.recv(1) != b"c":
                    raise PrivateOperationError("catalog acceptance ended")
                _remaining(deadline)
                if published:
                    generation.write_text(
                        json.dumps({"accepted": True, "generation": root.name})
                    )
                _remaining(deadline)
                if not _peer_open(channel):
                    raise PrivateOperationError("catalog caller ended")
                accepted = True
            finally:
                if published and not accepted and snapshot.exists():
                    os.replace(snapshot, root / "rejected-models.json")
                    generation.unlink(missing_ok=True)
