"""Project-local M3 SDK setup.

The standalone CLI lives in an isolated tool environment.  This module is the
explicit bridge into a project's own Python environment; it never installs
the CLI or application package into that environment.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

_VALIDATE_SCRIPT = r"""
import importlib
import importlib.metadata
import json

checks = {}
for name in ("pytest", "m3", "m3.pytest_plugin", "openai"):
    try:
        importlib.import_module(name)
    except Exception:
        checks[name] = False
    else:
        checks[name] = True
try:
    from m3.storage import SQLiteExecutionStore
except Exception:
    checks["SQLiteExecutionStore"] = False
else:
    checks["SQLiteExecutionStore"] = True
try:
    version = importlib.metadata.version("sf-m3")
except Exception:
    version = None
print(json.dumps({"checks": checks, "version": version}, sort_keys=True))
"""


class SetupError(RuntimeError):
    """A safe, user-facing setup failure."""


@dataclass(frozen=True)
class EnvironmentTarget:
    path: Path
    python: Path
    source: str
    created: bool = False


def _python_path(base: Path) -> Path:
    candidate = base / ("Scripts" if os.name == "nt" else "bin") / "python"
    if os.name == "nt" and not candidate.is_file():
        candidate = candidate.with_suffix(".exe")
    return candidate


def _environment_root(python: Path) -> Path:
    return (
        python.parent.parent
        if python.parent.name in {"bin", "Scripts"}
        else python.parent
    )


def _absolute(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser().absolute()


def _resolve_explicit(
    value: str | os.PathLike[str], environment: Mapping[str, str]
) -> Path:
    text = os.fspath(value)
    if os.sep not in text and (os.altsep is None or os.altsep not in text):
        found = shutil.which(text, path=environment.get("PATH"))
        if found:
            return _absolute(found)
    return _absolute(text)


def _environment_python(name: str, environment: Mapping[str, str]) -> Path | None:
    raw = environment.get(name)
    if not raw:
        return None
    base = _absolute(raw)
    candidates = [_python_path(base)]
    if name == "CONDA_PREFIX":
        # Conda uses ``<prefix>/python.exe`` on Windows and
        # ``<prefix>/bin/python`` on POSIX; accept either layout so a copied
        # environment remains discoverable across platforms.
        candidates = [base / "python.exe", base / "bin" / "python", *candidates]
    for python in candidates:
        if python.is_file():
            return python
    raise SetupError(f"active {name} environment is unavailable")


def _probe_python(
    python: Path,
    *,
    allow_conda: bool = False,
    conda_prefix: Path | None = None,
) -> dict[str, Any]:
    if not python.is_file():
        raise SetupError("selected Python executable is unavailable")
    try:
        result = subprocess.run(
            [
                str(python),
                "-c",
                "import json,sys; print(json.dumps({'version': sys.version_info[:2], 'prefix': sys.prefix, 'base_prefix': sys.base_prefix}))",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError("selected Python executable could not be started") from None
    if result.returncode != 0:
        raise SetupError("selected Python executable could not be started")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        version = payload["version"]
        if tuple(version) < (3, 10):
            raise SetupError("Python 3.10 or newer is required")
        prefix = Path(str(payload["prefix"])).absolute()
        base_prefix = Path(str(payload["base_prefix"])).absolute()
        conda_root = conda_prefix.absolute() if conda_prefix is not None else None
        # An arbitrary interpreter containing conda-meta is not enough to
        # distinguish a project env from the global Conda base.
        is_conda = bool(allow_conda and conda_root is not None and prefix == conda_root)
        if prefix == base_prefix and not is_conda:
            raise SetupError(
                "refusing to install into a system or global Python; use an isolated environment"
            )
        return cast(dict[str, Any], payload)
    except SetupError:
        raise
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SetupError(
            "selected Python executable returned an invalid check result"
        ) from None


def resolve_target(
    project_root: Path,
    explicit: str | os.PathLike[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> EnvironmentTarget:
    """Select an existing isolated environment, or create project ``.venv``."""

    env = os.environ if environment is None else environment
    root = project_root.resolve()
    if not root.is_dir():
        raise SetupError("project root is unavailable")
    if explicit is not None:
        python = _resolve_explicit(explicit, env)
        _probe_python(python)
        return EnvironmentTarget(_environment_root(python), python, "--python")

    for variable in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        if (
            variable == "CONDA_PREFIX"
            and env.get(variable)
            and env.get("CONDA_DEFAULT_ENV", "").casefold() == "base"
        ):
            raise SetupError(
                "active Conda base is not a project environment; create or activate a project environment"
            )
        candidate = _environment_python(variable, env)
        if candidate is not None:
            python = candidate
            _probe_python(
                python,
                allow_conda=variable == "CONDA_PREFIX",
                conda_prefix=Path(raw).absolute()
                if (raw := env.get(variable))
                else None,
            )
            return EnvironmentTarget(_environment_root(python), python, variable)

    venv = root / ".venv"
    python = _python_path(venv)
    if python.is_file():
        _probe_python(python)
        return EnvironmentTarget(venv, python, "project .venv")
    if venv.exists():
        raise SetupError(
            "project .venv exists but its Python executable is unavailable"
        )

    # The caller creates this exact target, and records that fact for rollback.
    return EnvironmentTarget(venv, python, "new project .venv", created=True)


def _create_environment(target: EnvironmentTarget) -> EnvironmentTarget:
    try:
        target.path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise SetupError("could not create the project environment") from None
    uv = shutil.which("uv")
    try:
        if uv:
            result = subprocess.run(
                [uv, "venv", str(target.path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        else:
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(target.path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError("could not create the project environment") from None
    if result.returncode != 0:
        raise SetupError("could not create the project environment")
    python = _python_path(target.path)
    _probe_python(python)
    return EnvironmentTarget(target.path, python, target.source, created=True)


def _cli_version() -> str:
    try:
        sdk = importlib.metadata.version("sf-m3")
        cli = importlib.metadata.version("sf-m3-cli")
    except importlib.metadata.PackageNotFoundError:
        raise SetupError(
            "the bundled M3 SDK version is unavailable; reinstall sf-m3-cli"
        ) from None
    if sdk != cli:
        raise SetupError(
            "the bundled CLI and SDK versions do not match; reinstall sf-m3-cli"
        )
    return sdk


def _ready(python: Path, version: str, project_root: Path) -> bool:
    try:
        result = subprocess.run(
            [str(python), "-c", _VALIDATE_SCRIPT],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        checks = payload.get("checks", {})
        return (
            result.returncode == 0
            and payload.get("version") == version
            and all(
                checks.get(name)
                for name in (
                    "pytest",
                    "m3",
                    "m3.pytest_plugin",
                    "openai",
                    "SQLiteExecutionStore",
                )
            )
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
        IndexError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return False


def _install_sdk(target: EnvironmentTarget, version: str) -> str:
    requirement = f"sf-m3[pytest,storage,judge]=={version}"
    uv = shutil.which("uv")
    command = (
        [uv, "pip", "install", "--python", str(target.python), requirement]
        if uv
        else [
            str(target.python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            requirement,
        ]
    )
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=300
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError("could not install the matching M3 SDK from PyPI") from None
    if result.returncode != 0:
        raise SetupError("could not install the matching M3 SDK from PyPI")
    return "uv" if uv else "venv/pip"


def run(args: Any) -> int:
    root = (args.project_root or Path.cwd()).resolve()
    version = _cli_version()
    target = resolve_target(root, args.python)
    print(f"Installing M3 SDK {version}")
    print(f"Project environment: {target.path} ({target.source})")
    if not target.created and _ready(target.python, version, root):
        print(f"Project environment ready: {target.path}")
        print(f"M3 SDK: {version}")
        print("Installer: skipped; environment is already ready")
        print("Next:\n  m3 doctor\n  m3 test --ui -- -q")
        return 0

    created = target.created
    try:
        if created:
            print("[1/3] Creating project environment")
            target = _create_environment(target)
        else:
            print("[1/3] Selecting project environment")
        print("[2/3] Installing matching SDK from PyPI")
        installer = _install_sdk(target, version)
        print("[3/3] Verifying project environment")
        if not _ready(target.python, version, root):
            raise SetupError("project environment did not pass the M3 SDK checks")
    except SetupError:
        if created and target.path.exists():
            shutil.rmtree(target.path, ignore_errors=True)
        raise
    print(f"Project environment ready: {target.path}")
    print(f"M3 SDK: {version}")
    print(f"Installer: {installer}")
    print("Next:\n  m3 doctor\n  m3 test --ui -- -q")
    return 0


__all__ = ["EnvironmentTarget", "SetupError", "resolve_target", "run"]
