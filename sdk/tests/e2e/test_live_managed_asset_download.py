"""Opt-in smoke check against real vendor release assets, without a provider key."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from m3.runtime import RuntimeManager

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.process_lifecycle]

_PINNED = {
    "claude": "2.1.278",
    "opencode": "1.18.30",
    "codex": "0.155.1",
    "pi": "0.86.1",
}


@pytest.mark.skipif(
    os.environ.get("M3_RUN_LIVE_MANAGED_ASSETS") != "1",
    reason="set M3_RUN_LIVE_MANAGED_ASSETS=1 to check real release assets",
)
@pytest.mark.asyncio
async def test_real_managed_asset_is_isolated_and_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = os.environ.get("M3_LIVE_MANAGED_KIND", "opencode")
    if kind not in _PINNED:
        pytest.fail(f"unsupported smoke recipe: {kind}")
    version = _PINNED[kind]
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "cache"
    manager = RuntimeManager(cache, project, tmp_path / "invocation")

    first = await manager.acquire(kind, version)
    assert first.executable.is_file()
    assert first.executable.resolve().is_relative_to(cache.resolve())
    assert first.provenance["version"] == version
    result = subprocess.run(
        [str(first.executable), "--version"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert version in result.stdout + result.stderr
    await first.release()

    def unexpected_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("warm acquisition contacted the network")

    monkeypatch.setattr(manager, "_download_staged", unexpected_network)
    monkeypatch.setattr(manager, "_fetch_json", unexpected_network)
    second = await manager.acquire(kind, version)
    assert second.executable == first.executable
    assert second.provenance["sha256"] == first.provenance["sha256"]
    await manager.close()
