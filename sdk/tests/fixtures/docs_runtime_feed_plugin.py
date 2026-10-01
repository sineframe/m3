"""Redirect managed native-runtime metadata to a local feed when enabled."""

from __future__ import annotations

import os


def pytest_configure(config: object) -> None:
    _ = config
    feed = os.environ.get("M3_DOCS_RUNTIME_FEED_URL")
    if not feed:
        return

    from m3.runtime import core

    original = core.default_manifest_url

    def local_manifest_url(kind: str, version: str = "latest") -> str | None:
        if kind in {"codex", "pi", "claude", "opencode"}:
            return f"{feed.rstrip('/')}/{kind}/{version}"
        return original(kind, version)

    core.default_manifest_url = local_manifest_url
