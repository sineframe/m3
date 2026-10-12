"""Private descriptor custody for fixed artifact publication operations."""

from __future__ import annotations

import array
import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from ._bootstrap_operations import PrivateOperationError, _private, _remaining

_MAXIMUM = 4096


@dataclass(repr=False)
class ArtifactLease:
    descriptor: int = field(repr=False)
    root: Path = field(repr=False)
    root_identity: tuple[int, int]
    key: str
    cold: bool

    def __repr__(self) -> str:
        return "ArtifactLease(<private>)"

    def __getstate__(self) -> None:
        raise TypeError("artifact custody cannot be serialized")

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def transfer_artifact_lease(
    channel: socket.socket,
    descriptor: int,
    *,
    root: Path,
    key: str,
    cold: bool,
    deadline: float,
) -> None:
    info = root.stat(follow_symlinks=False)
    raw = json.dumps(
        {
            "version": 1,
            "kind": "immutable-cache",
            "root": str(root),
            "identity": [info.st_dev, info.st_ino],
            "key": key,
            "cold": cold,
        }
    ).encode("utf-8")
    if len(raw) > _MAXIMUM:
        raise PrivateOperationError("artifact custody frame exceeds limit")
    channel.settimeout(_remaining(deadline))
    channel.sendmsg(
        [raw], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [descriptor]))]
    )
    if channel.recv(1) != b"a":
        raise PrivateOperationError("artifact custody was not acknowledged")
    _remaining(deadline)


def receive_artifact_lease(channel: socket.socket) -> ArtifactLease:
    raw, ancillary, flags, _address = channel.recvmsg(
        _MAXIMUM + 1, socket.CMSG_SPACE(array.array("i").itemsize * 16)
    )
    descriptors: list[int] = []
    adopted = False
    try:
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                values = array.array("i")
                values.frombytes(data[: len(data) - len(data) % values.itemsize])
                descriptors.extend(values)
        if flags or len(raw) > _MAXIMUM or len(descriptors) != 1:
            raise PrivateOperationError("invalid artifact custody frame")
        frame = json.loads(raw)
        from ._immutable_cache import RIPGREP

        if (
            set(frame) != {"version", "kind", "root", "identity", "key", "cold"}
            or frame["version"] != 1
            or frame["kind"] != "immutable-cache"
            or frame["key"] != RIPGREP.key
            or type(frame["cold"]) is not bool
            or not isinstance(frame["root"], str)
            or not isinstance(frame["identity"], list)
            or len(frame["identity"]) != 2
            or any(type(value) is not int for value in frame["identity"])
        ):
            raise PrivateOperationError("invalid artifact custody frame")
        _private(os.fstat(descriptors[0]), directory=False)
        lease = ArtifactLease(
            descriptors[0],
            Path(frame["root"]),
            tuple(frame["identity"]),
            frame["key"],
            frame["cold"],
        )
        adopted = True
        return lease
    finally:
        if not adopted:
            for descriptor in descriptors:
                os.close(descriptor)


def quarantine_artifact(lease: ArtifactLease, suffix: str) -> None:
    """Executed only in an owned cleanup child after proven writer death."""
    info = lease.root.stat(follow_symlinks=False)
    _private(info, directory=True)
    if (info.st_dev, info.st_ino) != lease.root_identity:
        raise PrivateOperationError("artifact root identity changed")
    if lease.cold:
        seed = lease.root / lease.key
        try:
            seed.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        os.replace(seed, lease.root / (lease.key + ".rejected-" + suffix))
