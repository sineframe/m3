"""One verified immutable OpenCode artifact, copied into private launch roots.

Only the pinned ripgrep executable is eligible. Native state, credentials,
catalogs and arbitrary files never enter this store.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import stat
import tarfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ._artifact_identity import (
    RIPGREP as RIPGREP,
)
from ._artifact_identity import (
    ImmutableCacheError as ImmutableCacheError,
)
from ._artifact_identity import (
    RipgrepArtifact as RipgrepArtifact,
)
from ._artifact_identity import (
    UnsupportedArtifactBuild as UnsupportedArtifactBuild,
)

_ARCHIVE_LIMIT = 16 * 1024 * 1024
_URL = (
    "https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/"
    "ripgrep-15.1.0-aarch64-apple-darwin.tar.gz"
)
_MEMBER = "ripgrep-15.1.0-aarch64-apple-darwin/rg"


@dataclass(frozen=True, repr=False)
class CacheRequest:
    root: Path = field(repr=False)
    native_executable: Path = field(repr=False)
    harness_version: str

    def __repr__(self) -> str:
        return "CacheRequest(<private>)"

    def __getstate__(self) -> None:
        raise TypeError("cache launch inputs cannot be serialized")

    def private_frame(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "native_executable": str(self.native_executable),
            "harness_version": self.harness_version,
        }


def _digest(path: Path, *, maximum: int) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ImmutableCacheError("immutable cache artifact is invalid")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for block in iter(lambda: stream.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def validate_native(request: CacheRequest) -> None:
    """Called inside the owned job, before any shared cache mutation."""
    if (
        request.harness_version != RIPGREP.harness_version
        or (platform.system(), platform.machine()) != ("Darwin", "arm64")
        or _digest(request.native_executable, maximum=256 * 1024 * 1024)
        != RIPGREP.native_sha256
    ):
        raise UnsupportedArtifactBuild(
            "immutable cache does not support this native build"
        )


def verified_binary(path: Path) -> bool:
    try:
        info = path.stat(follow_symlinks=False)
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and info.st_size == RIPGREP.binary_size
            and stat.S_IMODE(info.st_mode) == 0o500
            and _digest(path, maximum=RIPGREP.binary_size) == RIPGREP.binary_sha256
        )
    except (OSError, ImmutableCacheError):
        return False


def download_binary(*, timeout: float) -> bytes:
    with urllib.request.urlopen(_URL, timeout=timeout) as response:
        raw = response.read(_ARCHIVE_LIMIT + 1)
    if (
        len(raw) > _ARCHIVE_LIMIT
        or hashlib.sha256(raw).hexdigest() != RIPGREP.archive_sha256
    ):
        raise ImmutableCacheError("immutable cache archive verification failed")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        member = archive.getmember(_MEMBER)
        if not member.isfile() or member.size != RIPGREP.binary_size:
            raise ImmutableCacheError("immutable cache member verification failed")
        stream = archive.extractfile(member)
        if stream is None:
            raise ImmutableCacheError("immutable cache member is unavailable")
        with stream:
            binary = stream.read(RIPGREP.binary_size + 1)
    if (
        len(binary) != RIPGREP.binary_size
        or hashlib.sha256(binary).hexdigest() != RIPGREP.binary_sha256
    ):
        raise ImmutableCacheError("immutable cache binary verification failed")
    return binary


def materialize_binary(seed: Path, destination: Path) -> None:
    """Private copy only: changing the launch file cannot change its seed."""
    destination.parent.mkdir(mode=0o700, parents=True)
    with seed.open("rb") as source, destination.open("xb") as target:
        shutil.copyfileobj(source, target, length=65536)
    destination.chmod(0o500)
    if not verified_binary(destination):
        raise ImmutableCacheError("immutable cache private copy verification failed")
