"""Pinned public identities of verified immutable artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


class ImmutableCacheError(RuntimeError):
    """Value-free failure of an allowlisted immutable artifact operation."""


class UnsupportedArtifactBuild(ImmutableCacheError):
    """The pinned native build identity is not in the artifact allowlist."""


@dataclass(frozen=True)
class RipgrepArtifact:
    harness_version: str = "1.18.35"
    platform: str = "darwin-arm64"
    native_sha256: str = (
        "8c3c351b138cfe35905ab11846a1373f1beea590aee7eda412fb765b72c79d82"
    )
    archive_sha256: str = (
        "378e973289176ca0c6054054ee7f631a065874a352bf43f0fa60ef079b6ba715"
    )
    binary_sha256: str = (
        "4fdf1d8365af224bc70e3c1490d8461d859c37cc70e739a11e987af0215f3e94"
    )
    binary_size: int = 4048816

    @property
    def key(self) -> str:
        recipe = ":".join(
            (
                "opencode-ripgrep-copy-v1",
                self.harness_version,
                self.platform,
                self.native_sha256,
                self.archive_sha256,
                self.binary_sha256,
                str(self.binary_size),
            )
        )
        return hashlib.sha256(recipe.encode("ascii")).hexdigest()


RIPGREP = RipgrepArtifact()
