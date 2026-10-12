"""Fixed native configuration, capability and capture operations in owned jobs."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from ._bootstrap_operations import (
    _MAX_BYTES,
    OwnedResource,
    PrivateOperationError,
    _identity,
    _private,
    _remaining,
    _resource,
)

_NATIVE_FILES = {
    "codex": frozenset({"codex-home/auth.json", "codex-home/config.toml"}),
    "claude_code": frozenset({"mcp-config.json"}),
    "opencode": frozenset({"opencode.json"}),
}
_NATIVE_REQUIRED_FILES = {
    "codex": frozenset({"codex-home/config.toml"}),
    "claude_code": frozenset({"mcp-config.json"}),
    "opencode": frozenset({"opencode.json"}),
}


def probe_native(root: Path, request: dict[str, Any]) -> str:
    import asyncio

    from .harness.native_capabilities import _executable_path, _query

    path = _executable_path(request["executable"])
    opened = path.stat()
    output = asyncio.run(
        _query(
            path, tuple(request["args"]), root, owned_group=True, include_stderr=True
        )
    )
    raw = json.dumps(
        {
            "output": output,
            "identity": [
                str(path),
                opened.st_dev,
                opened.st_ino,
                opened.st_mtime_ns,
                opened.st_size,
            ],
        }
    ).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def discover_support(root: Path, request: dict[str, Any]) -> str:
    import asyncio

    from pydantic import TypeAdapter

    from .harness.native_capabilities import (
        NativeRuntimeIdentity,
        _OwnedNativeFacts,
        discover_native_support_receipt,
    )

    identity = request["runtime_identity"]
    runtime_identity = (
        None if identity is None else NativeRuntimeIdentity.from_provenance(identity)
    )
    cached = (
        TypeAdapter(_OwnedNativeFacts).validate_python(request["cached"])
        if request["cached"] is not None
        else None
    )
    facts = asyncio.run(
        discover_native_support_receipt(
            request["executable"], request["kind"], runtime_identity, cached, root
        )
    )
    raw = TypeAdapter(_OwnedNativeFacts).dump_json(facts)
    return base64.b64encode(raw).decode("ascii")


def read_capture(request: dict[str, Any]) -> str:
    resource = OwnedResource.from_private_frame(request["resource"])
    target_root = resource.root
    name = request["name"]
    if (
        not isinstance(name, str)
        or Path(name).name != name
        or not name.endswith(".jsonl")
    ):
        raise PrivateOperationError("invalid private capture filename")
    directory = os.open(target_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened_root = os.fstat(directory)
        _private(opened_root, directory=True)
        if _identity(opened_root) != (
            resource.root_device,
            resource.root_inode,
        ):
            raise PrivateOperationError("capture resource changed")
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
        except FileNotFoundError:
            raw = b""
        else:
            try:
                opened = os.fstat(descriptor)
                _private(opened, directory=False)
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    raw = stream.read(_MAX_BYTES + 1)
                if len(raw) > _MAX_BYTES:
                    raise PrivateOperationError("private capture data exceeds limit")
            finally:
                os.close(descriptor)
    finally:
        os.close(directory)
    return base64.b64encode(raw).decode("ascii")


def stage_native_launch(
    root: Path, request: dict[str, Any], deadline: float
) -> dict[str, Any]:
    harness, files = request["harness"], request["files"]
    allowed = _NATIVE_FILES.get(harness)
    if (
        allowed is None
        or not isinstance(files, dict)
        or not set(files).issubset(allowed)
        or not _NATIVE_REQUIRED_FILES[harness].issubset(files)
    ):
        raise PrivateOperationError("unsupported native launch files")
    for name_directory in ("home", "config", "data", "state", "cache"):
        (root / name_directory).mkdir(mode=0o700)
    if harness == "codex":
        (root / "codex-home").mkdir(mode=0o700)
    total = 0
    for name, value in files.items():
        _remaining(deadline)
        raw = base64.b64decode(value, validate=True)
        total += len(raw)
        if total > _MAX_BYTES:
            raise PrivateOperationError("native launch data exceeds limit")
        descriptor = os.open(
            root / name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
    marker = root / ".owner"
    descriptor = os.open(
        marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    os.close(descriptor)
    return _resource(root, marker, native_home=True)


def write_capture(request: dict[str, Any], deadline: float, *, append: bool) -> None:
    resource = OwnedResource.from_private_frame(request["resource"])
    target_root = resource.root
    directory = os.open(target_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened_root = os.fstat(directory)
        _private(opened_root, directory=True)
        if _identity(opened_root) != (
            resource.root_device,
            resource.root_inode,
        ):
            raise PrivateOperationError("capture resource changed")
        files = request["files"]
        if not isinstance(files, dict):
            raise PrivateOperationError("invalid private capture files")
        total = 0
        for name, value in files.items():
            _remaining(deadline)
            suffixes = (".jsonl",) if append else (".env.json", ".policy.json")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not name.endswith(suffixes)
                or len(name.encode("utf-8")) > 240
            ):
                raise PrivateOperationError("invalid private capture filename")
            raw = base64.b64decode(value, validate=True)
            total += len(raw)
            if total > _MAX_BYTES:
                raise PrivateOperationError("private capture data exceeds limit")
            flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT
            flags |= os.O_APPEND if append else os.O_EXCL
            try:
                descriptor = os.open(name, flags, 0o600, dir_fd=directory)
            except FileExistsError:
                if append:
                    raise
                existing = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory,
                )
                try:
                    _private(os.fstat(existing), directory=False)
                    with os.fdopen(existing, "rb", closefd=False) as stream:
                        if stream.read(_MAX_BYTES + 1) != raw:
                            raise PrivateOperationError(
                                "private capture handoff changed"
                            )
                finally:
                    os.close(existing)
                continue
            try:
                opened = os.fstat(descriptor)
                _private(opened, directory=False)
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(raw)
            finally:
                os.close(descriptor)
    finally:
        os.close(directory)
