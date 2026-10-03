from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from m3_cli import canary, setup

_SCRIPTS = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import build_cli_release
import canary_version

_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_BUILD = canary.CanaryBuild(
    "canary-pr-12",
    "https://github.com/sineframe/m3/releases/download/canary-pr-12",
    _COMMIT,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    canary.canary_build.cache_clear()
    yield
    canary.canary_build.cache_clear()


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("0.2.0a13", "0.2.0a14.dev7"),
        ("1.0.0b2", "1.0.0b3.dev7"),
        ("1.0.0rc1", "1.0.0rc2.dev7"),
        ("1.2.3", "1.2.4.dev7"),
    ],
)
def test_canary_version_targets_the_next_release(current: str, expected: str) -> None:
    assert canary_version.canary_version(current, 7) == expected


@pytest.mark.parametrize("current", ["1.2.3.dev1", "1.2.3.post1", "1.2.3+local"])
def test_canary_version_rejects_non_release_versions(current: str) -> None:
    with pytest.raises(ValueError):
        canary_version.canary_version(current, 7)


def test_canary_metadata_validates_tag_and_commit() -> None:
    assert build_cli_release.canary_metadata("canary-main", _COMMIT) == {
        "release_tag": "canary-main",
        "base_url": "https://github.com/sineframe/m3/releases/download/canary-main",
        "source_commit": _COMMIT,
    }
    for tag, commit in (
        ("v1.2.3", _COMMIT),
        ("canary-pr-0", _COMMIT),
        ("canary-main", "abc"),
    ):
        with pytest.raises(build_cli_release.ReleaseBuildError):
            build_cli_release.canary_metadata(tag, commit)


def test_staged_cli_carries_canary_metadata_only_for_canaries(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html>", encoding="utf-8")
    stable = build_cli_release._stage_cli(ui, tmp_path / "stable")
    assert not (stable / "src" / "m3_cli" / "canary.json").exists()
    metadata = build_cli_release.canary_metadata("canary-pr-12", _COMMIT)
    staged = build_cli_release._stage_cli(ui, tmp_path / "canary", metadata)
    written = json.loads((staged / "src" / "m3_cli" / "canary.json").read_text())
    assert written == metadata


def test_canary_build_reads_embedded_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(canary.resources, "files", lambda _package: tmp_path)
    assert canary.canary_build() is None
    canary.canary_build.cache_clear()
    payload = build_cli_release.canary_metadata("canary-pr-12", _COMMIT)
    (tmp_path / "canary.json").write_text(json.dumps(payload), encoding="utf-8")
    assert canary.canary_build() == _BUILD
    assert _BUILD.label == "canary-pr-12, 0123456"
    canary.canary_build.cache_clear()
    payload["base_url"] = "https://example.com/elsewhere"
    (tmp_path / "canary.json").write_text(json.dumps(payload), encoding="utf-8")
    assert canary.canary_build() is None


def test_development_install_is_not_a_canary() -> None:
    assert canary.canary_build() is None


def _record_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[list[list[str]], setup.EnvironmentTarget]:
    calls: list[list[str]] = []
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/local/bin/uv")
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append(command) or type("Result", (), {"returncode": 0})()
        ),
    )
    target = setup.EnvironmentTarget(
        tmp_path / ".venv", tmp_path / ".venv" / "bin" / "python", "project .venv"
    )
    return calls, target


def test_canary_setup_installs_sdk_from_its_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("M3_RELEASE_BASE_URL", raising=False)
    monkeypatch.setattr(setup, "canary_build", lambda: _BUILD)
    calls, target = _record_install(monkeypatch, tmp_path)
    setup._install_sdk(target, "0.2.0a14.dev7")
    assert calls[0][-1] == (
        "sf-m3[pytest,storage,judge] @ "
        "https://github.com/sineframe/m3/releases/download/canary-pr-12/"
        "sf_m3-0.2.0a14.dev7-py3-none-any.whl"
    )
    assert setup._sdk_source() == "canary-pr-12"


def test_canary_setup_honours_local_release_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("M3_RELEASE_BASE_URL", "file:///tmp/release/")
    monkeypatch.setattr(setup, "canary_build", lambda: _BUILD)
    calls, target = _record_install(monkeypatch, tmp_path)
    setup._install_sdk(target, "0.2.0a14.dev7")
    assert calls[0][-1] == (
        "sf-m3[pytest,storage,judge] @ file:///tmp/release/sf_m3-0.2.0a14.dev7-py3-none-any.whl"
    )


def test_stable_setup_ignores_release_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("M3_RELEASE_BASE_URL", "file:///tmp/release/")
    calls, target = _record_install(monkeypatch, tmp_path)
    setup._install_sdk(target, "1.2.3")
    assert calls[0][-1] == "sf-m3[pytest,storage,judge]==1.2.3"
    assert setup._sdk_source() == "PyPI"
