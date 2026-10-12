"""Private, bounded capsule and Unix endpoint operations in owned jobs.

Operation bytes and native handles use a one-use channel, separate from the
preparation control protocol. Only value-free lifecycle events reach its router.
"""

from __future__ import annotations

import array
import base64
import json
import math
import os
import shutil
import socket
import stat
import struct
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from ._preparation_owner import PreparationWorker, worker_preparation

if TYPE_CHECKING:
    from ._immutable_cache import CacheRequest
    from ._workspace_operations import CapturedWorkspace
    from .preparation import PreparationSupervisor

_MAX_BYTES = 8 * 1024 * 1024
_MAX_FRAME = 12 * 1024 * 1024
_closed_owners: dict[int, OwnedBootstrapOperations] = {}
_closed_owners_lock = threading.Lock()


def drain_closed_bootstrap_operations(deadline: float) -> None:
    """Run teardown retries owned cleanup before stopping its helper."""
    with _closed_owners_lock:
        owners = tuple(_closed_owners.values())
    for owner in owners:
        owner.close(deadline=deadline)


class PrivateOperationError(RuntimeError):
    """Value-free failure of an owned private operation."""


class PrivateBytesSource(Protocol):
    def consume(self) -> bytes: ...


def _encode_bytes(value: PrivateBytesSource) -> str:
    raw = value.consume()
    if not isinstance(raw, bytes) or len(raw) > _MAX_BYTES:
        raise PrivateOperationError("private bootstrap data exceeds limit")
    return base64.b64encode(raw).decode("ascii")


class PrivateBytes:
    def __init__(self, value: bytes) -> None:
        if len(value) > _MAX_BYTES:
            raise PrivateOperationError("private bootstrap data exceeds limit")
        self._value: bytes | None = value

    def __repr__(self) -> str:
        return "PrivateBytes(<private>)"

    def __getstate__(self) -> None:
        raise TypeError("private bootstrap bytes cannot be serialized")

    def consume(self) -> bytes:
        if self._value is None:
            raise PrivateOperationError("private bootstrap bytes already consumed")
        value, self._value = self._value, None
        return value


@dataclass(frozen=True)
class OwnedResource:
    root: Path
    path: Path
    device: int
    inode: int
    root_device: int
    root_inode: int
    native_home: bool = False

    def private_frame(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "path": str(self.path),
            "device": self.device,
            "inode": self.inode,
            "root_device": self.root_device,
            "root_inode": self.root_inode,
            "native_home": self.native_home,
        }

    @classmethod
    def from_private_frame(cls, value: dict[str, Any]) -> OwnedResource:
        expected = {
            "root",
            "path",
            "device",
            "inode",
            "root_device",
            "root_inode",
            "native_home",
        }
        if (
            set(value) != expected
            or not isinstance(value["root"], str)
            or not isinstance(value["path"], str)
        ):
            raise PrivateOperationError("invalid private bootstrap resource")
        if (
            any(
                type(value[key]) is not int or value[key] < 0
                for key in ("device", "inode", "root_device", "root_inode")
            )
            or type(value["native_home"]) is not bool
        ):
            raise PrivateOperationError("invalid private bootstrap resource")
        return cls(
            Path(value["root"]),
            Path(value["path"]),
            value["device"],
            value["inode"],
            value["root_device"],
            value["root_inode"],
            value["native_home"],
        )


@dataclass
class OwnedEndpoint:
    resource: OwnedResource
    channel: socket.socket = field(repr=False)


@dataclass
class _PrivateResult:
    resource: OwnedResource | None = None
    value: PrivateBytes | None = field(default=None, repr=False)
    channel: socket.socket | None = field(default=None, repr=False)
    absent: bool = False
    workspace_capture: CapturedWorkspace | None = field(default=None, repr=False)


def _decode_result(
    operation: str, root: Path, frame: dict[str, Any], descriptor: int | None
) -> _PrivateResult:
    result = _PrivateResult()
    if operation in {
        "private_capsule",
        "private_native_auth",
        "private_native_launch",
        "private_capture_root",
        "private_endpoint",
        "private_immutable_cache",
    }:
        if set(frame) != {"version", "resource"} or not isinstance(
            frame["resource"], dict
        ):
            raise PrivateOperationError("invalid private bootstrap result")
        resource = OwnedResource.from_private_frame(frame["resource"])
        name = (
            "ipc"
            if operation == "private_endpoint"
            else "auth.json"
            if operation == "private_native_auth"
            else "cache/opencode/bin/rg"
            if operation == "private_immutable_cache"
            else ".owner"
            if operation in {"private_native_launch", "private_capture_root"}
            else "capsule"
        )
        if (
            resource.root != root
            or resource.path != root / name
            or resource.native_home
            != (
                operation
                in {
                    "private_native_auth",
                    "private_immutable_cache",
                    "private_native_launch",
                    "private_capture_root",
                }
            )
        ):
            raise PrivateOperationError("invalid private bootstrap resource")
        result.resource = resource
    elif operation in {
        "private_read",
        "private_read_auth",
        "private_capture_read",
        "private_native_support",
        "private_native_probe",
    }:
        if (
            operation == "private_read_auth"
            and set(frame) == {"version", "absent"}
            and frame["absent"] is True
        ):
            result.absent = True
        elif set(frame) == {"version", "bytes"} and isinstance(frame["bytes"], str):
            result.value = PrivateBytes(base64.b64decode(frame["bytes"], validate=True))
        else:
            raise PrivateOperationError("invalid private bootstrap result")
    elif set(frame) != {"version"}:
        raise PrivateOperationError("invalid private bootstrap result")
    wants_socket = operation in {"private_endpoint", "private_connect"}
    if wants_socket != (descriptor is not None):
        raise PrivateOperationError("invalid private bootstrap handle")
    if descriptor is not None:
        result.channel = socket.socket(fileno=descriptor)
    return result


@dataclass
class _PrivateJob:
    root: Path
    events: set[str] = field(default_factory=set)
    fenced: bool = False
    submitted: bool = False
    binding_requested: bool = False
    channel: socket.socket | None = field(default=None, repr=False)
    removal: OwnedResource | None = None
    uses_resource: OwnedResource | None = None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(deadline) or remaining <= 0:
        raise PrivateOperationError("worker bootstrap deadline exceeded")
    return remaining


def _send_private(
    channel: socket.socket,
    value: dict[str, Any],
    deadline: float,
    descriptor: int | None = None,
) -> None:
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > _MAX_FRAME:
        raise PrivateOperationError("private bootstrap frame exceeds limit")
    channel.settimeout(_remaining(deadline))
    ancillary = (
        []
        if descriptor is None
        else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [descriptor]))]
    )
    header = struct.pack("!I", len(raw))
    sent = channel.sendmsg([header], ancillary)
    channel.sendall(header[sent:] + raw)


def _receive_private(
    channel: socket.socket, deadline: float
) -> tuple[dict[str, Any], int | None]:
    descriptors: list[int] = []
    try:
        header = bytearray()
        while len(header) < 4:
            channel.settimeout(_remaining(deadline))
            raw, ancillary, flags, _address = channel.recvmsg(
                4 - len(header), socket.CMSG_SPACE(array.array("i").itemsize * 64)
            )
            for level, kind, data in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    received = array.array("i")
                    received.frombytes(
                        data[: len(data) - len(data) % received.itemsize]
                    )
                    descriptors.extend(received)
            if not raw or flags or len(descriptors) > 1:
                raise PrivateOperationError("invalid private bootstrap frame")
            header.extend(raw)
        size = struct.unpack("!I", header)[0]
        if not 0 < size <= _MAX_FRAME:
            raise PrivateOperationError("invalid private bootstrap frame")
        body = bytearray()
        while len(body) < size:
            channel.settimeout(_remaining(deadline))
            raw = channel.recv(min(65536, size - len(body)))
            if not raw:
                raise PrivateOperationError("private bootstrap transport ended")
            body.extend(raw)
        value = json.loads(body)
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or type(value.get("version")) is not int
        ):
            raise PrivateOperationError("invalid private bootstrap frame")
        descriptor = descriptors.pop() if descriptors else None
        return value, descriptor
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def _acknowledge_handle(
    channel: socket.socket, result: _PrivateResult, deadline: float
) -> None:
    if result.channel is not None:
        os.set_inheritable(result.channel.fileno(), False)
        channel.settimeout(_remaining(deadline))
        channel.sendall(b"a")


def _private(info: os.stat_result, *, directory: bool) -> None:
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PrivateOperationError("worker bootstrap path is not private")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _resource(root: Path, path: Path, *, native_home: bool = False) -> dict[str, Any]:
    root_info = root.stat(follow_symlinks=False)
    info = path.stat(follow_symlinks=False)
    return OwnedResource(
        root,
        path,
        info.st_dev,
        info.st_ino,
        root_info.st_dev,
        root_info.st_ino,
        native_home,
    ).private_frame()


def execute_private_operation(
    channel: socket.socket, root: Path, operation: str, deadline: float
) -> None:
    """Called only after the helper's process-group gate has opened."""
    from ._native_operations import (
        discover_support,
        probe_native,
        read_capture,
        stage_native_launch,
        write_capture,
    )

    with channel:
        request, unexpected = _receive_private(channel, deadline)
        if unexpected is not None:
            os.close(unexpected)
            raise PrivateOperationError("invalid private bootstrap request")
        if request["deadline"] != deadline:
            raise PrivateOperationError("invalid private bootstrap deadline")
        _remaining(deadline)
        result: dict[str, Any] = {"version": 1}
        transferred: socket.socket | None = None
        try:
            if operation == "private_native_probe":
                result["bytes"] = probe_native(root, request)
            elif operation == "private_native_support":
                result["bytes"] = discover_support(root, request)
            elif operation == "private_capture_root":
                marker = root / ".owner"
                descriptor = os.open(
                    marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                os.close(descriptor)
                result["resource"] = _resource(root, marker, native_home=True)
            elif operation in {"private_capture_append", "private_capture_files"}:
                write_capture(
                    request, deadline, append=operation == "private_capture_append"
                )
            elif operation == "private_capture_read":
                result["bytes"] = read_capture(request)
            elif operation == "private_native_launch":
                result["resource"] = stage_native_launch(root, request, deadline)
            elif operation in {"private_capsule", "private_native_auth"}:
                raw = base64.b64decode(request["bytes"], validate=True)
                if len(raw) > _MAX_BYTES:
                    raise PrivateOperationError("private bootstrap data exceeds limit")
                path = root / (
                    "auth.json" if operation == "private_native_auth" else "capsule"
                )
                descriptor = os.open(
                    path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                result["resource"] = _resource(
                    root, path, native_home=operation == "private_native_auth"
                )
            elif operation == "private_endpoint":
                if request["directory"] is not None:
                    _private(
                        Path(request["directory"]).stat(follow_symlinks=False),
                        directory=True,
                    )
                path = root / "ipc"
                transferred = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                transferred.bind(str(path))
                path.chmod(0o600)
                transferred.listen()
                result["resource"] = _resource(root, path)
            elif operation in {"private_read", "private_read_auth"}:
                path = Path(request["path"])
                if operation == "private_read_auth" and path.name != "auth.json":
                    raise PrivateOperationError("unsupported native auth file")
                if operation == "private_read":
                    _private(path.parent.stat(follow_symlinks=False), directory=True)
                try:
                    descriptor = os.open(
                        path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                    )
                except FileNotFoundError:
                    if operation != "private_read_auth":
                        raise
                    result["absent"] = True
                    _send_private(channel, result, deadline)
                    return
                try:
                    opened = os.fstat(descriptor)
                    _private(opened, directory=False)
                    with os.fdopen(descriptor, "rb", closefd=False) as stream:
                        raw = stream.read(_MAX_BYTES + 1)
                    if len(raw) > _MAX_BYTES:
                        raise PrivateOperationError(
                            "private bootstrap data exceeds limit"
                        )
                    if _identity(path.stat(follow_symlinks=False)) != _identity(opened):
                        raise PrivateOperationError("worker bootstrap capsule changed")
                    if operation == "private_read":
                        path.unlink()
                    result["bytes"] = base64.b64encode(raw).decode("ascii")
                finally:
                    os.close(descriptor)
            elif operation == "private_connect":
                transferred = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                transferred.settimeout(_remaining(deadline))
                transferred.connect(request["path"])
            elif operation == "private_remove":
                resource = OwnedResource.from_private_frame(request["resource"])
                path, target_root = Path(resource.path), Path(resource.root)
                try:
                    root_stat = target_root.stat(follow_symlinks=False)
                except FileNotFoundError:
                    # A fenced earlier removal may have finished before its
                    # receipt arrived. The caller still owns this exact lease.
                    _send_private(channel, result, deadline)
                    return
                if _identity(root_stat) != (
                    resource.root_device,
                    resource.root_inode,
                ):
                    raise PrivateOperationError("worker bootstrap resource changed")
                try:
                    current = path.stat(follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if _identity(current) != (resource.device, resource.inode):
                        raise PrivateOperationError("worker bootstrap resource changed")
                    path.unlink()
                if resource.native_home:
                    shutil.rmtree(target_root)
                else:
                    target_root.rmdir()
            elif operation == "private_workspace_cleanup":
                from ._workspace_operations import cleanup_workspace

                cleanup_workspace(request)
            elif operation == "private_workspace_capture":
                from ._workspace_operations import capture_workspace

                capture_workspace(request, channel, deadline)
            else:
                raise PrivateOperationError("unsupported private bootstrap operation")
            _send_private(
                channel,
                result,
                deadline,
                transferred.fileno() if transferred is not None else None,
            )
            if transferred is not None:
                channel.settimeout(_remaining(deadline))
                if channel.recv(1) != b"a":
                    raise PrivateOperationError(
                        "private bootstrap handle transfer ended"
                    )
        finally:
            if transferred is not None:
                transferred.close()


class OwnedBootstrapOperations:
    """Synchronous facade; only IPC waiting happens on the caller thread."""

    def __init__(self, *, preparation: PreparationSupervisor | None = None) -> None:
        self._worker: PreparationWorker | None = None
        self._preparation = preparation
        self._condition = threading.Condition()
        self._jobs: dict[str, _PrivateJob] = {}
        self._resources: list[OwnedResource] = []
        self._closed = False

    @property
    def cleanup_complete(self) -> bool:
        with self._condition:
            return self._closed and not self._jobs and not self._resources

    def _deliver(self, frame: dict[str, Any]) -> None:
        with self._condition:
            job = self._jobs.get(frame["id"])
            if job is None:
                return
            event = frame["event"]
            job.events.add(event)
            if event == "private_bound" and job.fenced and not job.submitted:
                assert self._worker is not None
                self._worker.send("discard", frame["id"])
            if (
                event == "ready"
                and job.removal is not None
                and job.removal in self._resources
            ):
                self._resources.remove(job.removal)
                assert self._worker is not None
                self._worker.send(
                    "dispose_private", job.removal.root.name.removeprefix("m3-lit-")
                )
            if event in {"removed", "rejected", "private_unbound"}:
                self._jobs.pop(frame["id"], None)
                assert self._worker is not None
                self._worker.forget(frame["id"])
            self._condition.notify_all()
            if self.cleanup_complete:
                with _closed_owners_lock:
                    _closed_owners.pop(id(self), None)

    def _wait(self, job: _PrivateJob, event: str, deadline: float) -> None:
        with self._condition:
            while event not in job.events:
                remaining = _remaining(deadline)
                if job.fenced or job.events.intersection(
                    {"failed", "lost", "rejected", "cleanup_failed"}
                ):
                    raise PrivateOperationError("private bootstrap operation failed")
                self._condition.wait(remaining)
            _remaining(deadline)
            if job.fenced:
                raise PrivateOperationError("private bootstrap operation fenced")

    def _run(
        self,
        operation: str,
        payload: dict[str, Any],
        deadline: float,
        *,
        closing: bool = False,
    ) -> _PrivateResult:
        _remaining(deadline)
        ident = uuid4().hex
        with self._condition:
            if self._closed and not closing:
                raise PrivateOperationError("private bootstrap provider closed")
            if self._worker is None:
                try:
                    self._worker = (
                        self._preparation._private_worker()
                        if self._preparation is not None
                        else worker_preparation()
                    )
                except (OSError, RuntimeError, ValueError):
                    raise PrivateOperationError(
                        "private bootstrap owner unavailable"
                    ) from None
            worker = self._worker
            root = (
                Path("/tmp") / ("m3-lit-" + ident)
                if operation == "private_endpoint"
                else worker.root / ident
            )
            job = _PrivateJob(root)
            if operation in {
                "private_capture_append",
                "private_capture_files",
                "private_capture_read",
            }:
                job.uses_resource = OwnedResource.from_private_frame(
                    payload["resource"]
                )
                if job.uses_resource not in self._resources:
                    raise PrivateOperationError("private capture resource is not owned")
            if operation == "private_remove":
                job.removal = OwnedResource.from_private_frame(payload["resource"])
                if any(
                    pending.removal == job.removal for pending in self._jobs.values()
                ):
                    raise PrivateOperationError(
                        "private bootstrap removal is still owned"
                    )
            self._jobs[ident] = job
            try:
                worker.register(ident, None, self._deliver)
            except RuntimeError:
                self._jobs.pop(ident, None)
                raise PrivateOperationError(
                    "private bootstrap owner unavailable"
                ) from None
        local: socket.socket | None = None
        descriptor: int | None = None
        result: _PrivateResult | None = None
        published = False
        try:
            local, remote = socket.socketpair()
            with self._condition:
                job.channel = local
                if job.fenced:
                    remote.close()
                    raise PrivateOperationError("private bootstrap operation fenced")
            try:
                worker.bind_private(ident, remote)
                with self._condition:
                    job.binding_requested = True
            finally:
                remote.close()
            self._wait(job, "private_bound", deadline)
            with self._condition:
                if job.fenced:
                    raise PrivateOperationError("private bootstrap operation fenced")
                worker.send(
                    "submit",
                    ident,
                    root=str(root),
                    operation=operation,
                    payload={"deadline": deadline},
                )
                job.submitted = True
            _send_private(
                local, {"version": 1, "deadline": deadline, **payload}, deadline
            )
            workspace_capture = None
            if operation == "private_workspace_capture":
                from ._workspace_operations import receive_workspace_capture

                workspace_capture = receive_workspace_capture(local, deadline)
            frame, descriptor = _receive_private(local, deadline)
            result = _decode_result(operation, root, frame, descriptor)
            result.workspace_capture = workspace_capture
            descriptor = None
            _acknowledge_handle(local, result, deadline)
            self._wait(job, "ready", deadline)
            worker.send("accept", ident)
            self._wait(job, "accepted", deadline)
            worker.send("release", ident)
            self._wait(job, "released", deadline)
            if operation == "private_immutable_cache":
                local.settimeout(_remaining(deadline))
                local.sendall(b"c")
                self._wait(job, "guard_closed", deadline)
                worker.send("artifact_accept", ident)
                self._wait(job, "artifact_accepted", deadline)
            with self._condition:
                _remaining(deadline)
                if job.fenced:
                    raise PrivateOperationError("private bootstrap operation fenced")
                if result.resource is None:
                    # Transfer only the private result/handle; the helper keeps
                    # and removes this empty staging root after writer death.
                    worker.send("discard", ident)
                    job.fenced = True
                    published = True
                    return result
                self._resources.append(result.resource)
                worker.send("forget", ident)
                worker.forget(ident, published=True)
                self._jobs.pop(ident, None)
                published = True
            return result
        except PrivateOperationError:
            _remaining(deadline)
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            _remaining(deadline)
            raise PrivateOperationError(
                "private bootstrap operation unavailable"
            ) from None
        finally:
            if local is not None:
                local.close()
            if not published:
                if descriptor is not None:
                    os.close(descriptor)
                if result is not None and result.channel is not None:
                    result.channel.close()
                with self._condition:
                    job.fenced = True
                    if not job.binding_requested:
                        self._jobs.pop(ident, None)
                        worker.forget(ident)
                    self._condition.notify_all()
                try:
                    worker.send("discard", ident)
                except (RuntimeError, ValueError):
                    pass

    def create_capsule(
        self, value: PrivateBytesSource, *, deadline: float
    ) -> OwnedResource:
        result = self._run(
            "private_capsule",
            {"bytes": _encode_bytes(value)},
            deadline,
        )
        assert result.resource is not None
        return result.resource

    def stage_native_auth(
        self, value: PrivateBytesSource, *, deadline: float
    ) -> OwnedResource:
        result = self._run(
            "private_native_auth",
            {"bytes": _encode_bytes(value)},
            deadline,
        )
        assert result.resource is not None
        return result.resource

    def materialize_immutable_cache(
        self,
        request: CacheRequest,
        *,
        deadline: float,
    ) -> OwnedResource:
        result = self._run("private_immutable_cache", request.private_frame(), deadline)
        assert result.resource is not None
        return result.resource

    def probe_native(
        self, executable: str, args: tuple[str, ...], *, deadline: float
    ) -> PrivateBytes:
        result = self._run(
            "private_native_probe", {"executable": executable, "args": args}, deadline
        )
        assert result.value is not None
        return result.value

    def discover_native_support(
        self,
        executable: str,
        kind: str,
        identity: Mapping[str, Any] | None,
        *,
        deadline: float,
    ) -> PrivateBytes:
        result = self._run(
            "private_native_support",
            {
                "executable": executable,
                "kind": kind,
                "runtime_identity": identity,
            },
            deadline,
        )
        assert result.value is not None
        return result.value

    def read_capture(
        self, resource: OwnedResource, filename: str, *, deadline: float
    ) -> PrivateBytes:
        result = self._run(
            "private_capture_read",
            {"resource": resource.private_frame(), "name": filename},
            deadline,
        )
        assert result.value is not None
        return result.value

    def create_capture_root(self, *, deadline: float) -> OwnedResource:
        result = self._run("private_capture_root", {}, deadline)
        assert result.resource is not None
        return result.resource

    def write_capture_files(
        self,
        resource: OwnedResource,
        files: Mapping[str, PrivateBytesSource],
        *,
        deadline: float,
    ) -> None:
        self._capture_files("private_capture_files", resource, files, deadline)

    def append_capture(
        self,
        resource: OwnedResource,
        files: Mapping[str, PrivateBytesSource],
        *,
        deadline: float,
    ) -> None:
        self._capture_files("private_capture_append", resource, files, deadline)

    def _capture_files(
        self,
        operation: str,
        resource: OwnedResource,
        files: Mapping[str, PrivateBytesSource],
        deadline: float,
    ) -> None:
        self._run(
            operation,
            {
                "resource": resource.private_frame(),
                "files": {name: _encode_bytes(value) for name, value in files.items()},
            },
            deadline,
        )

    def stage_native_launch(
        self,
        harness: str,
        files: Mapping[str, PrivateBytesSource],
        *,
        deadline: float,
    ) -> OwnedResource:
        from ._native_operations import _NATIVE_FILES, _NATIVE_REQUIRED_FILES

        allowed = _NATIVE_FILES.get(harness)
        if (
            allowed is None
            or not set(files).issubset(allowed)
            or not _NATIVE_REQUIRED_FILES[harness].issubset(files)
        ):
            raise PrivateOperationError("unsupported native launch files")
        result = self._run(
            "private_native_launch",
            {
                "harness": harness,
                "files": {name: _encode_bytes(value) for name, value in files.items()},
            },
            deadline,
        )
        assert result.resource is not None
        return result.resource

    def read_native_auth(self, path: Path, *, deadline: float) -> PrivateBytes | None:
        result = self._run("private_read_auth", {"path": str(path)}, deadline)
        if result.absent:
            return None
        assert result.value is not None
        return result.value

    def create_endpoint(
        self, directory: Path | None = None, *, deadline: float
    ) -> OwnedEndpoint:
        result = self._run(
            "private_endpoint",
            {"directory": str(directory) if directory is not None else None},
            deadline,
        )
        assert result.resource is not None and result.channel is not None
        return OwnedEndpoint(result.resource, result.channel)

    def read_and_unlink(self, path: Path, *, deadline: float) -> PrivateBytes:
        result = self._run("private_read", {"path": str(path)}, deadline)
        assert result.value is not None
        return result.value

    def connect(self, path: str, *, deadline: float) -> socket.socket:
        result = self._run("private_connect", {"path": path}, deadline)
        assert result.channel is not None
        return result.channel

    def remove(self, resource: OwnedResource, *, deadline: float) -> None:
        with self._condition:
            if resource not in self._resources:
                raise PrivateOperationError("private bootstrap resource is not owned")
        self._run(
            "private_remove",
            {"resource": resource.private_frame()},
            deadline,
            closing=True,
        )
        with self._condition:
            if resource in self._resources:
                self._resources.remove(resource)

    def remove_workspace(self, payload: dict[str, Any], *, deadline: float) -> None:
        self._run("private_workspace_cleanup", payload, deadline)

    def capture_workspace(
        self, payload: dict[str, Any], *, deadline: float
    ) -> CapturedWorkspace:
        result = self._run("private_workspace_capture", payload, deadline)
        assert result.workspace_capture is not None
        return result.workspace_capture

    def fence_pending(self) -> None:
        """Fence IO while preserving the resources and open facade for disposal."""
        self._fence_jobs(close_provider=False)

    def close(self, *, deadline: float) -> None:
        with _closed_owners_lock:
            _closed_owners[id(self)] = self
        try:
            self._close_owned(deadline)
        finally:
            if self.cleanup_complete:
                with _closed_owners_lock:
                    _closed_owners.pop(id(self), None)

    def cancel_pending(self) -> None:
        """Fence pending jobs immediately, retaining their owner for teardown."""
        with _closed_owners_lock:
            _closed_owners[id(self)] = self
        self._fence_jobs()

    def _fence_jobs(self, *, close_provider: bool = True) -> None:
        with self._condition:
            if close_provider:
                self._closed = True
            jobs = tuple(self._jobs.items())
            for ident, job in jobs:
                if job.fenced:
                    continue
                job.fenced = True
                if job.channel is not None:
                    try:
                        job.channel.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                if self._worker is not None:
                    try:
                        self._worker.send("discard", ident)
                    except (RuntimeError, ValueError):
                        pass
            self._condition.notify_all()

    def _close_owned(self, deadline: float) -> None:
        self._fence_jobs()
        with self._condition:
            resources = tuple(self._resources)
        for resource in resources:
            with self._condition:
                while any(
                    job.removal == resource or job.uses_resource == resource
                    for job in self._jobs.values()
                ):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or (
                        self._worker is not None and self._worker.failed
                    ):
                        return
                    self._condition.wait(remaining)
                if resource not in self._resources:
                    continue
            try:
                self.remove(resource, deadline=deadline)
            except PrivateOperationError:
                break
        with self._condition:
            while self._jobs:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (self._worker is not None and self._worker.failed):
                    return
                self._condition.wait(remaining)
