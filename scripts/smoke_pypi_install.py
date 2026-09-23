"""Exercise independent fresh PyPI install paths after a release upload."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class SmokeFailure(RuntimeError):
    pass


def run(args: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise SmokeFailure(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
        )


def run_pypi_install(
    args: list[str], *, version: str, cwd: Path, env: dict[str, str]
) -> None:
    """Allow a newly uploaded release time to reach the runner's PyPI index."""
    deadline = time.monotonic() + 600
    cache_paths = {
        key: env[key] for key in ("UV_CACHE_DIR", "PIP_CACHE_DIR") if key in env
    }
    attempt = 0
    while True:
        try:
            run(args, cwd=cwd, env=env)
            return
        except SmokeFailure as exc:
            error = str(exc).lower()
            missing_version = f"=={version}" in error and (
                "no version of sf-m3" in error
                or "no matching distribution found for sf-m3" in error
                or "could not find a version that satisfies the requirement sf-m3"
                in error
            )
            remaining = deadline - time.monotonic()
            if not missing_version or remaining <= 0:
                raise
            attempt += 1
            print(
                f"PyPI has not exposed sf-m3 release {version} to this runner yet; "
                f"retrying install in {min(10, remaining):.0f}s (attempt {attempt + 1}).",
                flush=True,
            )
            time.sleep(min(10, remaining))
            for key, path in cache_paths.items():
                env[key] = f"{path}-retry-{attempt}"


def python_path(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def verify_sdk(python: Path, version: str, cwd: Path, env: dict[str, str]) -> None:
    code = f"import importlib.metadata as m, m3; assert m.version('sf-m3') == {version!r} == m3.__version__"
    run([str(python), "-c", code], cwd=cwd, env=env)


def verify_cli(python: Path, version: str, cwd: Path, env: dict[str, str]) -> None:
    code = f"import importlib.metadata as m; from importlib import resources; assert m.version('sf-m3-cli') == {version!r}; ui=resources.files('m3_cli').joinpath('ui'); assert ui.joinpath('index.html').is_file() and any(ui.joinpath('assets').iterdir())"
    run([str(python), "-c", code], cwd=cwd, env=env)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    version = args.version
    uv = shutil.which("uv")
    if not uv:
        raise SmokeFailure("uv is required by the PyPI install smoke matrix")
    uvx = shutil.which("uvx")
    if not uvx:
        raise SmokeFailure("uvx is required by the PyPI install smoke matrix")
    with tempfile.TemporaryDirectory(prefix="sf-m3-pypi-smoke-") as temporary:
        root = Path(temporary).resolve()
        blocked = (
            "TOKEN",
            "SECRET",
            "PASSWORD",
            "API_KEY",
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
            "CONDA_DEFAULT_ENV",
        )
        base_env = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith(("UV_", "PIP_"))
            and not any(part in key.upper() for part in blocked)
        }
        base_env.update(
            {
                "UV_DEFAULT_INDEX": "https://pypi.org/simple",
                "UV_INDEX_URL": "https://pypi.org/simple",
                "PIP_INDEX_URL": "https://pypi.org/simple",
            }
        )
        print(f"Testing sf-m3 release {version} with {sys.executable}")

        # uv tool install: independent tool and cache directories.
        tool = root / "tool"
        tool_cache = root / "cache-tool"
        tool_env = {
            **base_env,
            "UV_TOOL_DIR": str(tool / "envs"),
            "UV_TOOL_BIN_DIR": str(tool / "bin"),
            "UV_CACHE_DIR": str(tool_cache),
        }
        run_pypi_install(
            [
                uv,
                "tool",
                "install",
                "--python",
                sys.executable,
                "--prerelease",
                "allow",
                "--force",
                f"sf-m3-cli=={version}",
            ],
            version=version,
            cwd=root,
            env=tool_env,
        )
        command = tool / "bin" / ("m3.exe" if os.name == "nt" else "m3")
        run([str(command), "--help"], cwd=root, env=tool_env)
        tool_python = python_path(tool / "envs" / "sf-m3-cli")
        verify_cli(tool_python, version, root, tool_env)
        project = root / "setup-project"
        project.mkdir()
        run(
            [uv, "venv", "--python", sys.executable, str(project / ".venv")],
            cwd=project,
            env=tool_env,
        )
        run(
            [str(command), "setup", "--project-root", str(project)],
            cwd=project,
            env=tool_env,
        )
        run(
            [str(command), "doctor", "--project-root", str(project)],
            cwd=project,
            env=tool_env,
        )
        verify_sdk(python_path(project / ".venv"), version, project, tool_env)

        # uv add: new project and isolated cache.
        add_project = root / "uv-add"
        add_project.mkdir()
        add_env = {**base_env, "UV_CACHE_DIR": str(root / "cache-add")}
        run(
            [uv, "init", "--bare", "--name", "sf-m3-smoke"],
            cwd=add_project,
            env=add_env,
        )
        run_pypi_install(
            [uv, "add", "--prerelease", "allow", f"sf-m3[pytest,judge]=={version}"],
            version=version,
            cwd=add_project,
            env=add_env,
        )
        run([uv, "run", "python", "-c", "import m3"], cwd=add_project, env=add_env)
        verify_sdk(python_path(add_project / ".venv"), version, add_project, add_env)

        # uv pip: separate venv and cache.
        pip_venv = root / "uv-pip-env"
        pip_env = {**base_env, "UV_CACHE_DIR": str(root / "cache-uv-pip")}
        run(
            [uv, "venv", "--python", sys.executable, str(pip_venv)],
            cwd=root,
            env=pip_env,
        )
        run_pypi_install(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python_path(pip_venv)),
                "--prerelease",
                "allow",
                f"sf-m3-cli=={version}",
            ],
            version=version,
            cwd=root,
            env=pip_env,
        )
        verify_cli(python_path(pip_venv), version, root, pip_env)

        # uvx: an independent ephemeral command environment.
        uvx_env = {
            **base_env,
            "UV_CACHE_DIR": str(root / "cache-uvx"),
            "UV_TOOL_DIR": str(root / "uvx-tools"),
            "UV_TOOL_BIN_DIR": str(root / "uvx-bin"),
        }
        run_pypi_install(
            [
                uvx,
                "--prerelease",
                "allow",
                "--from",
                f"sf-m3-cli=={version}",
                "m3",
                "--help",
            ],
            version=version,
            cwd=root,
            env=uvx_env,
        )

        # pip: independent CLI and SDK virtual environments.
        pip_envvars = {**base_env, "PIP_CACHE_DIR": str(root / "cache-pip-cli")}
        cli_venv = root / "pip-cli-env"
        sdk_venv = root / "pip-sdk-env"
        run(
            [uv, "venv", "--seed", "--python", sys.executable, str(cli_venv)],
            cwd=root,
            env=pip_envvars,
        )
        run_pypi_install(
            [
                str(python_path(cli_venv)),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                f"sf-m3-cli=={version}",
            ],
            version=version,
            cwd=root,
            env=pip_envvars,
        )
        verify_cli(python_path(cli_venv), version, root, pip_envvars)
        run(
            [
                str(cli_venv / ("Scripts/m3.exe" if os.name == "nt" else "bin/m3")),
                "--help",
            ],
            cwd=root,
            env=pip_envvars,
        )
        run(
            [uv, "venv", "--seed", "--python", sys.executable, str(sdk_venv)],
            cwd=root,
            env=pip_envvars,
        )
        sdk_pip_env = {**pip_envvars, "PIP_CACHE_DIR": str(root / "cache-pip-sdk")}
        run_pypi_install(
            [
                str(python_path(sdk_venv)),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                f"sf-m3[pytest,judge]=={version}",
            ],
            version=version,
            cwd=root,
            env=sdk_pip_env,
        )
        verify_sdk(python_path(sdk_venv), version, root, sdk_pip_env)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"PyPI install smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
