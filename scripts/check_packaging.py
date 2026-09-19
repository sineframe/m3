"""Build, inspect, and clean-install the SDK distribution.

This is an executable release gate rather than a pytest fixture. It deliberately
uses temporary build, virtualenv, and import working directories so the check
cannot leave databases, caches, or build artifacts in the repository.
"""

from __future__ import annotations

import email
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "sdk"
_VERSION_MATCH = re.search(
    r'^version\s*=\s*"([^"]+)"', (SDK / "pyproject.toml").read_text(), re.MULTILINE
)
assert _VERSION_MATCH is not None
EXPECTED_VERSION = _VERSION_MATCH.group(1)


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def inspect_artifacts(wheel: Path, sdist: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = email.message_from_bytes(archive.read(metadata_name))
        assert metadata["Name"] == "m3"
        assert metadata["Version"] == EXPECTED_VERSION
        assert metadata["Requires-Python"] == ">=3.10"
        assert metadata["License-Expression"] == "Apache-2.0"
        requires = "\n".join(metadata.get_all("Requires-Dist") or []).lower()
        for forbidden in ("fastapi", "streamlit", "requests", "pydantic-settings"):
            assert forbidden not in requires
        assert set(metadata.get_all("Provides-Extra") or []) >= {
            "pytest",
            "storage",
            "property",
            "docs",
            "all",
        }
        assert "app" not in (metadata.get_all("Provides-Extra") or [])
        assert "m3/py.typed" in names
        forbidden_modules = (
            "m3/api/",
            "m3/ui/",
            "m3/persistence/",
            "m3/main.py",
            "m3/config.py",
            "m3/services/run_manager.py",
            "m3/harness/claude_cli.py",
            "m3/harness/opencode_cli.py",
            "m3/domain/events.py",
        )
        assert not any(
            name.startswith(prefix) for name in names for prefix in forbidden_modules
        )
        assert "m3/schemas/m3.harness.v1.schema.json" in names
        assert "m3/schemas/m3.event.v0.2.schema.json" in names
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
        assert not any(name.startswith("m3/tests/") for name in names)
        assert not any(name.startswith("m3/examples/") for name in names)
        assert not any(name.startswith("m3/docs/") for name in names)
        entry_points_name = next(
            (name for name in names if name.endswith(".dist-info/entry_points.txt")),
            None,
        )
        entry_points = (
            archive.read(entry_points_name).decode("utf-8")
            if entry_points_name is not None
            else ""
        )
        console_scripts = {
            line.split("=", 1)[0].strip()
            for line in entry_points.splitlines()
            if "=" in line and not line.lstrip().startswith("[")
        }
        assert "m3" not in console_scripts
        assert (
            sum(name == "m3/schemas/m3.harness.v1.schema.json" for name in names) == 1
        )
        assert (
            sum(name == "m3/schemas/m3.event.v0.2.schema.json" for name in names) == 1
        )

    with tarfile.open(sdist) as archive:
        sdist_names = archive.getnames()
        assert any(
            name.endswith("/tests/unit/test_packaging.py") for name in sdist_names
        )
        assert any(
            name.endswith("/examples/acp-fixture-harness.json") for name in sdist_names
        )
        assert any(name.endswith("/docs/README.md") for name in sdist_names)
        assert any(name.endswith("/LICENSE") for name in sdist_names)
        assert not any(
            marker in name
            for name in sdist_names
            for marker in ("/.git/", "/research/", "/build/", "/dist/")
        )


def python_executable(venv: Path) -> Path:
    directory = venv / ("Scripts" if os.name == "nt" else "bin")
    return directory / ("python.exe" if os.name == "nt" else "python")


def script_executable(directory: Path, name: str) -> Path:
    """Return a venv console-script path on both POSIX and Windows."""

    return directory / (f"{name}.exe" if os.name == "nt" else name)


def extract_sdist(sdist: Path, destination: Path) -> Path:
    with tarfile.open(sdist) as archive:
        members = archive.getmembers()
        root = destination.resolve()
        for member in members:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(root):
                raise AssertionError(f"unsafe sdist member: {member.name}")
        if sys.version_info >= (3, 12):
            archive.extractall(destination, filter="data")
        else:
            archive.extractall(destination)
    extracted = [
        path
        for path in destination.iterdir()
        if path.is_dir() and path.name.startswith("m3-")
    ]
    assert len(extracted) == 1
    return extracted[0]


def installed_import_smoke(
    python: Path, smoke_dir: Path, expected_version: str
) -> None:
    smoke_dir.joinpath(".env").write_text("M3_IMPORT_SMOKE_SENTINEL=must-not-load\n")
    code = textwrap.dedent(
        f"""
        import asyncio
        import builtins
        import importlib.metadata as metadata
        import io
        import os
        import pathlib
        import socket
        import sqlite3
        import subprocess

        def denied(*args, **kwargs):
            raise AssertionError("m3 import attempted a forbidden side effect")

        real_open = builtins.open
        real_io_open = io.open
        def guarded_open(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                denied(file, mode)
            return real_open(file, mode, *args, **kwargs)
        def guarded_io_open(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                denied(file, mode)
            return real_io_open(file, mode, *args, **kwargs)

        builtins.open = guarded_open
        io.open = guarded_io_open
        pathlib.Path.write_text = denied
        pathlib.Path.write_bytes = denied
        pathlib.Path.touch = denied
        for name in ("mkdir", "makedirs", "replace", "rename", "remove", "unlink", "rmdir"):
            setattr(os, name, denied)
        real_os_open = os.open
        def guarded_os_open(path, flags, *args, **kwargs):
            write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            if flags & write_flags:
                denied(path, flags)
            return real_os_open(path, flags, *args, **kwargs)
        os.open = guarded_os_open
        # Keep the subscription protocol on Popen.  MCP 2.0.0 evaluates
        # ``subprocess.Popen[bytes]`` in a runtime annotation while it is
        # imported on Python 3.10; replacing Popen with a plain function
        # would make this smoke test fail before m3 is imported.
        class DeniedPopen:
            @classmethod
            def __class_getitem__(cls, item):
                return cls

            def __new__(cls, *args, **kwargs):
                denied(*args, **kwargs)

        subprocess.Popen = DeniedPopen
        for name in ("run", "call", "check_call", "check_output"):
            setattr(subprocess, name, denied)
        for name in ("socket", "create_connection", "create_server", "getaddrinfo", "socketpair"):
            setattr(socket, name, denied)
        asyncio.new_event_loop = denied
        asyncio.run = denied
        sqlite3.connect = denied

        import m3
        import importlib.util
        def module_missing(name):
            try:
                return importlib.util.find_spec(name) is None
            except ModuleNotFoundError:
                return True
        assert m3.__version__ == metadata.version("m3") == {expected_version!r}
        assert os.environ.get("M3_IMPORT_SMOKE_SENTINEL") is None
        for removed in ("m3.api", "m3.ui", "m3.main", "m3.config", "m3.persistence", "m3.services.run_manager", "m3.harness.claude_cli", "m3.harness.opencode_cli", "m3.domain.events", "m3.bridge", "m3.bridge.reference"):
            assert module_missing(removed), removed
        assert importlib.util.find_spec("m3.fixtures.acp_agent") is not None
        """
    )
    run([str(python), "-c", code], cwd=smoke_dir)


def main() -> None:
    run(["uv", "lock", "--check"])
    versions = [
        value.strip()
        for value in os.environ.get("M3_PYTHONS", "3.10,3.11,3.12,3.13").split(",")
        if value.strip()
    ]
    with tempfile.TemporaryDirectory(prefix="m3-package-check-") as temporary:
        root = Path(temporary)
        build_dir = root / "build"
        build_dir.mkdir()
        run(["uv", "build", "--project", str(SDK), "--out-dir", str(build_dir)])
        wheel = next(build_dir.glob("*.whl"))
        sdist = next(build_dir.glob("*.tar.gz"))
        inspect_artifacts(wheel, sdist)

        extracted_root = root / "extracted"
        extracted_root.mkdir()
        extracted = extract_sdist(sdist, extracted_root)
        rebuilt_dir = root / "rebuilt"
        rebuilt_dir.mkdir()
        run(["uv", "build", "--project", str(extracted), "--out-dir", str(rebuilt_dir)])
        wheel = next(rebuilt_dir.glob("*.whl"))
        sdist = next(rebuilt_dir.glob("*.tar.gz"))
        inspect_artifacts(wheel, sdist)

        for version in versions:
            venv = root / ("venv-" + version.replace(".", "-"))
            run(["uv", "venv", "--python", version, str(venv)])
            python = python_executable(venv)
            run(["uv", "pip", "install", "--python", str(python), f"{wheel}[all]"])
            smoke_dir = root / ("smoke-" + version.replace(".", "-"))
            smoke_dir.mkdir()
            installed_import_smoke(python, smoke_dir, EXPECTED_VERSION)
            scripts = python.parent
            assert not script_executable(scripts, "m3").exists()
            run([str(python), "-m", "m3.harness.cli", "--help"], cwd=smoke_dir)
            run([str(python), "-m", "m3.fixtures.acp_agent", "--help"], cwd=smoke_dir)
            assert [path.name for path in smoke_dir.iterdir()] == [".env"], (
                f"import/entry-point artifacts left in {smoke_dir}"
            )
            assert (
                smoke_dir.joinpath(".env").read_text()
                == "M3_IMPORT_SMOKE_SENTINEL=must-not-load\n"
            )

    print("packaging gate passed for", ", ".join(versions))


if __name__ == "__main__":
    main()
