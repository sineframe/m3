from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "prepare_release.py"
_SPEC = importlib.util.spec_from_file_location("prepare_release", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
release = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = release
_SPEC.loader.exec_module(release)


def test_repository_release_metadata_is_synchronized() -> None:
    state = release.read_state(release.ROOT)

    assert set(state.versions.values()) == {state.current_version}
    assert set(state.cli_dependencies.values()) == {state.current_version}


def _repo(tmp_path: Path, *, version: str = "1.2.3") -> Path:
    root = tmp_path / "repo"
    for relative in ("sdk/pyproject.toml", "app/pyproject.toml"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'[project]\nname = "{relative.split("/")[0]}"\nversion = "{version}"\n',
            encoding="utf-8",
        )
    cli = root / "cli/pyproject.toml"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text(
        f'[project]\nname = "cli"\nversion = "{version}"\ndependencies = [\n'
        f'  "mcp-pal[storage]=={version}",\n  "mcp-pal-app=={version}",\n]\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("lock\n", encoding="utf-8")
    return root


def _fake_lock(calls: list[list[str]]):
    def run(root: Path, command: list[str]) -> None:
        calls.append(command)

    return run


def test_prepare_updates_projects_and_runs_lock_checks(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    calls: list[list[str]] = []

    version, files = release.prepare(
        "2.0.0a1",
        root=root,
        lock_runner=_fake_lock(calls),
    )

    assert version == "2.0.0a1"
    assert len(files) == 4
    assert 'version = "2.0.0a1"' in (root / "sdk/pyproject.toml").read_text()
    cli = (root / "cli/pyproject.toml").read_text()
    assert '"mcp-pal[storage]==2.0.0a1"' in cli
    assert '"mcp-pal-app==2.0.0a1"' in cli
    assert calls == [["uv", "lock"], ["uv", "lock", "--check"]]


def test_invalid_version_does_not_change_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = {path: path.read_text() for path in root.rglob("pyproject.toml")}

    with pytest.raises(release.ReleasePreparationError, match="PEP 440"):
        release.prepare("not-a-version", root=root, lock_runner=_fake_lock([]))

    assert {path: path.read_text() for path in root.rglob("pyproject.toml")} == before


def test_inconsistent_project_versions_are_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = root / "app/pyproject.toml"
    path.write_text(
        path.read_text().replace('version = "1.2.3"', 'version = "1.2.4"'),
        encoding="utf-8",
    )

    with pytest.raises(
        release.ReleasePreparationError, match="project versions must match"
    ):
        release.prepare("2.0.0", root=root, lock_runner=_fake_lock([]))


def test_missing_cli_dependency_pin_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = root / "cli/pyproject.toml"
    path.write_text(
        path.read_text().replace('  "mcp-pal-app==1.2.3",\n', ""),
        encoding="utf-8",
    )

    with pytest.raises(
        release.ReleasePreparationError, match="exactly one CLI dependency pin"
    ):
        release.prepare("2.0.0", root=root, lock_runner=_fake_lock([]))


def test_missing_project_version_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = root / "app/pyproject.toml"
    path.write_text(
        path.read_text().replace('version = "1.2.3"\n', ""),
        encoding="utf-8",
    )

    with pytest.raises(
        release.ReleasePreparationError, match="exactly one project version"
    ):
        release.prepare("2.0.0", root=root, lock_runner=_fake_lock([]))


def test_mismatched_cli_dependency_pin_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = root / "cli/pyproject.toml"
    path.write_text(
        path.read_text().replace("mcp-pal-app==1.2.3", "mcp-pal-app==9.9.9"),
        encoding="utf-8",
    )

    with pytest.raises(
        release.ReleasePreparationError, match="dependency pins must match"
    ):
        release.prepare("2.0.0", root=root, lock_runner=_fake_lock([]))


def test_dry_run_does_not_write_or_run_uv(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = {path: path.read_text() for path in root.rglob("pyproject.toml")}
    calls: list[list[str]] = []

    version, files = release.prepare(
        "2.0.0",
        root=root,
        dry_run=True,
        lock_runner=_fake_lock(calls),
    )

    assert version == "2.0.0"
    assert len(files) == 3
    assert {path: path.read_text() for path in root.rglob("pyproject.toml")} == before
    assert calls == []


def test_check_accepts_synchronized_repository(tmp_path: Path) -> None:
    root = _repo(tmp_path, version="2.0.0")
    calls: list[list[str]] = []

    version, files = release.prepare(
        "2.0.0",
        root=root,
        check=True,
        lock_runner=_fake_lock(calls),
    )

    assert version == "2.0.0"
    assert files == ()
    assert calls == [["uv", "lock", "--check"]]


def test_check_rejects_unprepared_repository(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(release.ReleasePreparationError, match="not prepared"):
        release.prepare("2.0.0", root=root, check=True, lock_runner=_fake_lock([]))


def test_lock_failure_restores_metadata_and_lockfile(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = {path: path.read_text() for path in root.rglob("pyproject.toml")}
    lock = root / "uv.lock"
    before_lock = lock.read_text()

    def fail_lock(_root: Path, command: list[str]) -> None:
        if command == ["uv", "lock"]:
            lock.write_text("partially regenerated\n", encoding="utf-8")
            raise release.ReleasePreparationError("lock failed")

    with pytest.raises(release.ReleasePreparationError, match="lock failed"):
        release.prepare("2.0.0", root=root, lock_runner=fail_lock)

    assert {path: path.read_text() for path in root.rglob("pyproject.toml")} == before
    assert lock.read_text() == before_lock
