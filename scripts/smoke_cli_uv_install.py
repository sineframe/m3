"""Smoke-test the three release wheels in disposable uv tool storage."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


class SmokeError(RuntimeError):
    """A safe, user-facing smoke-test failure."""


def run(command: list[str], *, env: dict[str, str], cwd: Path) -> None:
    print("+", " ".join(command))
    try:
        result = subprocess.run(command, env=env, cwd=cwd, check=False)
    except OSError as exc:
        raise SmokeError(f"could not start {command[0]}") from exc
    if result.returncode != 0:
        raise SmokeError(f"command failed with exit {result.returncode}: {command[0]}")


def wheel_paths(release_dir: Path, version: str) -> tuple[Path, Path, Path]:
    paths = tuple(
        release_dir / f"{prefix}-{version}-py3-none-any.whl"
        for prefix in ("mcp_pal_cli", "mcp_pal", "mcp_pal_app")
    )
    if any(not path.is_file() for path in paths):
        raise SmokeError("release directory is missing one of the three exact wheels")
    if len(tuple(release_dir.glob("*.whl"))) != 3:
        raise SmokeError("release directory must contain exactly three wheels")
    return paths


def assert_ui_in_wheel(cli_wheel: Path) -> None:
    try:
        with zipfile.ZipFile(cli_wheel) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise SmokeError("CLI wheel is not a readable wheel archive") from exc
    if "mcp_pal_cli/ui/index.html" not in names or not any(
        name.startswith("mcp_pal_cli/ui/assets/") and not name.endswith("/") for name in names
    ):
        raise SmokeError("CLI wheel does not contain the packaged UI")


def installed_ui_check(tool_root: Path, *, windows: bool) -> Path:
    python = tool_root / ("Scripts/python.exe" if windows else "bin/python")
    if not python.is_file():
        raise SmokeError(f"uv tool environment Python is missing: {python}")
    return python


def smoke(release_dir: str | Path, version: str) -> None:
    release = Path(release_dir).expanduser().resolve()
    cli_wheel, sdk_wheel, app_wheel = wheel_paths(release, version)
    assert_ui_in_wheel(cli_wheel)
    uv = shutil.which("uv")
    if uv is None:
        raise SmokeError("uv is required for the isolated release smoke test")
    with tempfile.TemporaryDirectory(prefix="mcp-pal-cli-uv-smoke-") as temporary:
        root = Path(temporary)
        tool_dir = root / "tool"
        tool_bin = root / "bin"
        tool_bin.mkdir()
        env = os.environ.copy()
        env.update(
            {
                "UV_TOOL_DIR": str(tool_dir),
                "UV_TOOL_BIN_DIR": str(tool_bin),
                "UV_CACHE_DIR": str(root / "cache"),
                "PATH": f"{tool_bin}{os.pathsep}{env.get('PATH', '')}",
            }
        )
        run(
            [
                uv,
                "tool",
                "install",
                "--force",
                str(cli_wheel),
                "--with",
                str(sdk_wheel),
                "--with",
                str(app_wheel),
            ],
            env=env,
            cwd=root,
        )
        executable = tool_bin / ("mcp-pal.exe" if os.name == "nt" else "mcp-pal")
        if not executable.is_file():
            raise SmokeError("uv did not create the mcp-pal tool command in isolated storage")
        run([str(executable), "--help"], env=env, cwd=root)
        tool_python = installed_ui_check(tool_dir / "mcp-pal-cli", windows=os.name == "nt")
        run(
            [
                str(tool_python),
                "-c",
                'from importlib import resources; root = resources.files("mcp_pal_cli").joinpath("ui"); assert root.joinpath("index.html").is_file(); assert any(item.is_file() for item in root.joinpath("assets").iterdir())',
            ],
            env=env,
            cwd=root,
        )
        if not tool_dir.is_dir():
            raise SmokeError("uv tool install did not use the isolated tool directory")
    print("isolated uv CLI install smoke test passed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        smoke(args.release_dir, args.version)
    except SmokeError as exc:
        print(f"CLI uv smoke test failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
