from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from m3_cli import main, setup


def _python(path: Path) -> Path:
    return (
        path
        / ("Scripts" if sys.platform == "win32" else "bin")
        / ("python.exe" if sys.platform == "win32" else "python")
    )


def test_target_precedence_explicit_then_active_then_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit" / ("Scripts" if sys.platform == "win32" else "bin")
    explicit.mkdir(parents=True)
    explicit_python = explicit / ("python.exe" if sys.platform == "win32" else "python")
    explicit_python.write_text("placeholder")
    active = tmp_path / "active"
    project = tmp_path / ".venv"
    monkeypatch.setattr(
        setup, "_probe_python", lambda python, **kwargs: {"version": (3, 12)}
    )
    selected = setup.resolve_target(
        tmp_path, explicit_python, environment={"VIRTUAL_ENV": str(active)}
    )
    assert selected.path == explicit.parent

    active_python = _python(active)
    active_python.parent.mkdir(parents=True)
    active_python.write_text("placeholder")
    selected = setup.resolve_target(
        tmp_path,
        environment={
            "VIRTUAL_ENV": str(active),
            "CONDA_PREFIX": str(tmp_path / "conda"),
        },
    )
    assert selected.path == active

    active_python.unlink()
    project_python = _python(project)
    project_python.parent.mkdir(parents=True)
    project_python.write_text("placeholder")
    selected = setup.resolve_target(tmp_path, environment={})
    assert selected.path == project


def test_invalid_active_environment_does_not_fall_back(tmp_path: Path) -> None:
    with pytest.raises(setup.SetupError, match="active VIRTUAL_ENV"):
        setup.resolve_target(
            tmp_path, environment={"VIRTUAL_ENV": str(tmp_path / "missing")}
        )


def test_conda_root_python_is_supported_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conda = tmp_path / "conda"
    executable = conda / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("placeholder")
    monkeypatch.setattr(
        setup, "_probe_python", lambda python, **kwargs: {"version": (3, 12)}
    )
    target = setup.resolve_target(tmp_path, environment={"CONDA_PREFIX": str(conda)})
    assert target.path == conda
    assert target.python == executable


def test_active_conda_base_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(setup.SetupError, match="Conda base"):
        setup.resolve_target(
            tmp_path,
            environment={
                "CONDA_PREFIX": str(tmp_path / "conda"),
                "CONDA_DEFAULT_ENV": "BaSe",
            },
        )


def test_explicit_python_wins_over_active_conda_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit" / "bin" / "python"
    explicit.parent.mkdir(parents=True)
    explicit.write_text("placeholder")
    monkeypatch.setattr(
        setup, "_probe_python", lambda python, **kwargs: {"version": (3, 12)}
    )
    target = setup.resolve_target(
        tmp_path,
        explicit,
        environment={
            "CONDA_PREFIX": str(tmp_path / "conda"),
            "CONDA_DEFAULT_ENV": "base",
        },
    )
    assert target.path == explicit.parent.parent


def test_explicit_python_does_not_inherit_conda_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit" / "bin" / "python"
    explicit.parent.mkdir(parents=True)
    explicit.write_text("placeholder")
    observed: dict[str, object] = {}

    def probe(python: Path, **kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {"version": (3, 12)}

    monkeypatch.setattr(setup, "_probe_python", probe)
    setup.resolve_target(
        tmp_path,
        explicit,
        environment={"CONDA_PREFIX": str(tmp_path / "conda")},
    )
    assert observed == {}


def test_new_project_environment_is_marked_for_rollback(tmp_path: Path) -> None:
    target = setup.resolve_target(tmp_path, environment={})
    assert target.created is True
    assert target.path == tmp_path / ".venv"


def test_system_python_is_rejected_for_explicit_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Result:
        returncode = 0
        stdout = (
            '{"version": [3, 12], "prefix": "/usr/local", "base_prefix": "/usr/local"}'
        )

    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(setup.SetupError, match="system or global"):
        setup.resolve_target(tmp_path, sys.executable, environment={})


def test_install_prefers_uv_and_uses_pep508_local_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda name: "/usr/local/bin/uv" if name == "uv" else None,
    )
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
    installer = setup._install_sdk(target, "1.2.3")
    assert installer == "uv"
    assert calls == [
        [
            "/usr/local/bin/uv",
            "pip",
            "install",
            "--python",
            str(target.python),
            "sf-m3[pytest,storage,judge]==1.2.3",
        ]
    ]


def test_install_falls_back_to_environment_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(setup.shutil, "which", lambda _name: None)
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
    assert setup._install_sdk(target, "1.2.3") == "venv/pip"
    assert calls[0][:4] == [str(target.python), "-m", "pip", "install"]
    assert calls[0][-1] == ("sf-m3[pytest,storage,judge]==1.2.3")


def test_cli_version_requires_matching_distributions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {"sf-m3": "1.2.3", "sf-m3-cli": "1.2.4"}
    monkeypatch.setattr(setup.importlib.metadata, "version", lambda name: values[name])
    with pytest.raises(setup.SetupError, match="versions do not match"):
        setup._cli_version()


def test_setup_ready_environment_skips_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = setup.EnvironmentTarget(
        tmp_path / ".venv", tmp_path / ".venv" / "bin" / "python", "project .venv"
    )
    monkeypatch.setattr(setup, "_cli_version", lambda: "1.2.3")
    monkeypatch.setattr(setup, "resolve_target", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(setup, "_ready", lambda *_args: True)
    monkeypatch.setattr(
        setup,
        "_install_sdk",
        lambda *_args: pytest.fail("installed despite ready environment"),
    )
    assert setup.run(SimpleNamespace(project_root=tmp_path, python=None)) == 0
    output = capsys.readouterr().out
    assert "Project environment ready" in output
    assert "Installer: skipped; environment is already ready" in output


def test_setup_mismatch_installs_and_existing_environment_survives_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = setup.EnvironmentTarget(
        tmp_path / ".venv", tmp_path / ".venv" / "bin" / "python", "project .venv"
    )
    target.path.mkdir()
    monkeypatch.setattr(setup, "_cli_version", lambda: "1.2.3")
    monkeypatch.setattr(setup, "resolve_target", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(setup, "_ready", lambda *_args: False)
    monkeypatch.setattr(setup, "_install_sdk", lambda *_args: "uv")
    with pytest.raises(setup.SetupError):
        setup.run(SimpleNamespace(project_root=tmp_path, python=None))
    assert target.path.is_dir()


def test_setup_failure_removes_only_new_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = setup.EnvironmentTarget(
        tmp_path / ".venv",
        tmp_path / ".venv" / "bin" / "python",
        "new project .venv",
        created=True,
    )
    monkeypatch.setattr(setup, "_cli_version", lambda: "1.2.3")
    monkeypatch.setattr(setup, "resolve_target", lambda *_args, **_kwargs: target)

    def fail_create(value: setup.EnvironmentTarget) -> setup.EnvironmentTarget:
        value.path.mkdir()
        raise setup.SetupError("secret failure")

    monkeypatch.setattr(setup, "_create_environment", fail_create)
    with pytest.raises(setup.SetupError):
        setup.run(SimpleNamespace(project_root=tmp_path, python=None))
    assert not target.path.exists()


def test_setup_invalid_secret_path_is_not_echoed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    secret_path = tmp_path / "secret-token-python"
    assert (
        main(["setup", "--project-root", str(tmp_path), "--python", str(secret_path)])
        == 2
    )
    captured = capsys.readouterr()
    assert str(secret_path) not in captured.out
    assert str(secret_path) not in captured.err


def test_ready_requires_exact_version_and_all_project_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '{"checks":{"pytest":true,"m3":true,"m3.pytest_plugin":true,"openai":true,"SQLiteExecutionStore":true},"version":"1.2.3"}',
            },
        )(),
    )
    assert setup._ready(Path("/tmp/python"), "1.2.3", tmp_path)
    assert not setup._ready(Path("/tmp/python"), "9.9.9", tmp_path)
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '{"checks":{"pytest":true,"m3":true,"m3.pytest_plugin":true,"SQLiteExecutionStore":true},"version":"1.2.3"}',
            },
        )(),
    )
    assert not setup._ready(Path("/tmp/python"), "1.2.3", tmp_path)
