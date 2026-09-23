from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "smoke_pypi_install.py"
SPEC = importlib.util.spec_from_file_location("smoke_pypi_install", SCRIPT)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_missing_release_version_retries_with_fresh_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    caches: list[str] = []
    sleeps: list[float] = []

    def fake_run(_args: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        assert cwd == tmp_path
        caches.append(env["UV_CACHE_DIR"])
        if len(caches) == 1:
            raise smoke.SmokeFailure("Because there is no version of sf-m3-cli==0.2.3")

    monkeypatch.setattr(smoke, "run", fake_run)
    monkeypatch.setattr(smoke.time, "sleep", sleeps.append)
    smoke.run_pypi_install(
        ["uv", "tool", "install", "sf-m3-cli==0.2.3"],
        version="0.2.3",
        cwd=tmp_path,
        env={"UV_CACHE_DIR": str(tmp_path / "cache")},
    )

    assert caches == [str(tmp_path / "cache"), str(tmp_path / "cache-retry-1")]
    assert sleeps == [10]


def test_unrelated_install_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def fake_run(_args: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        nonlocal calls
        calls += 1
        raise smoke.SmokeFailure("wheel metadata is invalid")

    monkeypatch.setattr(smoke, "run", fake_run)
    with pytest.raises(smoke.SmokeFailure, match="wheel metadata is invalid"):
        smoke.run_pypi_install(
            ["uv", "tool", "install", "sf-m3-cli==0.2.3"],
            version="0.2.3",
            cwd=tmp_path,
            env={"UV_CACHE_DIR": str(tmp_path / "cache")},
        )
    assert calls == 1


def test_missing_version_retry_has_a_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = iter((0, 601))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        smoke,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            smoke.SmokeFailure("no version of sf-m3==0.2.3")
        ),
    )
    with pytest.raises(smoke.SmokeFailure, match="no version"):
        smoke.run_pypi_install(
            ["uv", "add", "sf-m3==0.2.3"],
            version="0.2.3",
            cwd=tmp_path,
            env={"UV_CACHE_DIR": str(tmp_path / "cache")},
        )
