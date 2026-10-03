"""Metadata for canary builds published from labelled pull requests and main.

Canary CLI wheels carry ``m3_cli/canary.json``, written by
``scripts/build_cli_release.py``. Stable wheels do not, so every helper here
returns ``None`` for them and callers keep their stable behaviour.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

_TAG_RE = re.compile(r"^canary-(?:main|pr-[1-9][0-9]*)$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_BASE_URL_PREFIX = "https://github.com/sineframe/m3/releases/download/"


@dataclass(frozen=True)
class CanaryBuild:
    release_tag: str
    base_url: str
    source_commit: str

    @property
    def label(self) -> str:
        return f"{self.release_tag}, {self.source_commit[:7]}"

    def sdk_requirement(self, version: str) -> str:
        # M3_RELEASE_BASE_URL matches install.sh: it points at a local copy of
        # the release, which lets the build gate run setup before publishing.
        base_url = os.environ.get("M3_RELEASE_BASE_URL") or self.base_url
        wheel = f"sf_m3-{version}-py3-none-any.whl"
        return f"sf-m3[pytest,storage,judge] @ {base_url.rstrip('/')}/{wheel}"


@lru_cache(maxsize=1)
def canary_build() -> CanaryBuild | None:
    """Return the embedded canary metadata, or ``None`` for stable builds."""

    try:
        text = resources.files("m3_cli").joinpath("canary.json").read_text("utf-8")
        payload = json.loads(text)
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    tag = payload.get("release_tag")
    base_url = payload.get("base_url")
    commit = payload.get("source_commit")
    if not (
        isinstance(tag, str)
        and _TAG_RE.fullmatch(tag)
        and base_url == f"{_BASE_URL_PREFIX}{tag}"
        and isinstance(commit, str)
        and _COMMIT_RE.fullmatch(commit)
    ):
        return None
    return CanaryBuild(tag, base_url, commit)


__all__ = ["CanaryBuild", "canary_build"]
