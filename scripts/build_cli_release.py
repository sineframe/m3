"""Build the unpublished SDK, application, and standalone CLI wheels.

The CLI wheel is the only artifact that contains the production UI.  The UI is
copied into a temporary copy of ``cli/`` while building; the source checkout is
never modified and no generated UI files are committed.
"""

from __future__ import annotations

import argparse
import email
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    "sf_m3": ROOT / "sdk",
    "sf_m3_app": ROOT / "app",
    "sf_m3_cli": ROOT / "cli",
}
_VERSION_RE = re.compile(r"^\s*version\s*=\s*[\"']([^\"']+)[\"']\s*$", re.MULTILINE)
_WHEEL_DIST_INFO_RE = re.compile(r"^[^/]+-[^/]+\.dist-info/METADATA$")
_JUNK_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


class ReleaseBuildError(RuntimeError):
    """A safe, user-facing release build error."""


@dataclass(frozen=True)
class WheelMetadata:
    path: Path
    name: str
    version: str
    requires: tuple[str, ...]
    provides_extras: tuple[str, ...]
    files: tuple[str, ...]
    entry_points: tuple[str, ...]


def _package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _version(project: Path) -> str:
    try:
        contents = (project / "pyproject.toml").read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseBuildError(
            f"could not read project metadata: {project.name}"
        ) from exc
    match = _VERSION_RE.search(contents)
    if match is None:
        raise ReleaseBuildError(f"could not determine version for {project.name}")
    return match.group(1)


def _resolved(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve()


def validate_ui_dist(
    ui_dist: str | os.PathLike[str], out_dir: str | os.PathLike[str]
) -> Path:
    """Validate and return a production UI directory."""

    ui = _resolved(ui_dist)
    out = _resolved(out_dir)
    if not ui.is_dir():
        raise ReleaseBuildError("UI dist must be an existing directory")
    if out == ui or out in ui.parents or ui in out.parents:
        raise ReleaseBuildError("UI dist and output directory must not overlap")
    if not (ui / "index.html").is_file():
        raise ReleaseBuildError("UI dist is missing index.html")
    assets = ui / "assets"
    if not assets.is_dir():
        raise ReleaseBuildError("UI dist is missing the assets directory")
    if not any(path.is_file() for path in assets.rglob("*")):
        raise ReleaseBuildError("UI dist assets directory is empty")
    return ui


def _ignore_staged_junk(directory: str, names: list[str]) -> set[str]:
    """Keep source staging deterministic and free from local build products."""

    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if name in _JUNK_NAMES or name.endswith(".egg-info"):
            ignored.add(name)
        elif path.is_file() and path.suffix in {".pyc", ".pyo"}:
            ignored.add(name)
    return ignored


def _stage_cli(ui_dist: Path, temporary_root: Path) -> Path:
    staged = temporary_root / "cli"
    shutil.copytree(CLI_ROOT, staged, ignore=_ignore_staged_junk)
    packaged_ui = staged / "src" / "m3_cli" / "ui"
    if packaged_ui.exists():
        shutil.rmtree(packaged_ui)
    packaged_ui.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ui_dist, packaged_ui)
    return staged


def _run_build(project: Path, out_dir: Path) -> None:
    command = [
        "uv",
        "build",
        "--wheel",
        "--no-sources",
        "--out-dir",
        str(out_dir),
        "--project",
        str(project),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ReleaseBuildError("uv is required to build the release wheels") from exc
    except OSError as exc:
        raise ReleaseBuildError(f"could not start uv for {project.name}") from exc
    if result.returncode != 0:
        raise ReleaseBuildError(
            f"wheel build failed for {project.name} (exit {result.returncode})"
        )


def _metadata_from_wheel(path: Path) -> WheelMetadata:
    try:
        with zipfile.ZipFile(path) as archive:
            names = tuple(archive.namelist())
            metadata_names = [name for name in names if _WHEEL_DIST_INFO_RE.match(name)]
            if len(metadata_names) != 1:
                raise ReleaseBuildError(
                    f"wheel has invalid METADATA layout: {path.name}"
                )
            metadata = email.message_from_bytes(archive.read(metadata_names[0]))
            entry_points_name = (
                metadata_names[0].removesuffix("METADATA") + "entry_points.txt"
            )
            entry_points = ()
            if entry_points_name in names:
                entry_points = tuple(
                    line.strip()
                    for line in archive.read(entry_points_name)
                    .decode("utf-8")
                    .splitlines()
                    if line.strip() and not line.lstrip().startswith("[")
                )
            name = metadata.get("Name")
            version = metadata.get("Version")
            if not isinstance(name, str) or not isinstance(version, str):
                raise ReleaseBuildError(f"wheel metadata is incomplete: {path.name}")
            return WheelMetadata(
                path=path,
                name=name,
                version=version,
                requires=tuple(metadata.get_all("Requires-Dist") or ()),
                provides_extras=tuple(metadata.get_all("Provides-Extra") or ()),
                files=names,
                entry_points=entry_points,
            )
    except zipfile.BadZipFile as exc:
        raise ReleaseBuildError(f"invalid wheel archive: {path.name}") from exc


def classify_wheels(
    paths: list[Path] | tuple[Path, ...], expected: dict[str, str]
) -> dict[str, Path]:
    """Classify wheels by exact normalized filename distribution/version.

    Exact token matching is intentional: ``m3`` must not accidentally
    classify the similarly-prefixed ``m3_app`` wheel.
    """

    expected_tokens = {
        (_package_name(name).replace("-", "_"), version): name
        for name, version in expected.items()
    }
    found: dict[str, Path] = {}
    for path in paths:
        parts = path.name.removesuffix(".whl").split("-")
        if len(parts) < 5:
            continue
        key = (parts[0], parts[1])
        name = expected_tokens.get(key)
        if name is None:
            continue
        if name in found:
            raise ReleaseBuildError(f"more than one wheel for {name} {expected[name]}")
        found[name] = path
    missing = [name for name in expected if name not in found]
    if missing:
        raise ReleaseBuildError("missing wheels: " + ", ".join(missing))
    return found


def _requirement_name(value: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", value)
    return _package_name(match.group(1)) if match else ""


def _mandatory_requirements(values: tuple[str, ...]) -> set[str]:
    """Return dependency names that are installed without selecting an extra."""

    return {
        _requirement_name(value) for value in values if "; extra" not in value.lower()
    }


def _verify_wheel(
    metadata: WheelMetadata, expected_name: str, expected_version: str
) -> None:
    if (
        _package_name(metadata.name) != _package_name(expected_name)
        or metadata.version != expected_version
    ):
        raise ReleaseBuildError(
            f"wheel metadata does not match {expected_name} {expected_version}: {metadata.path.name}"
        )
    forbidden_requires = {"streamlit", "requests"}
    if expected_name == "sf_m3_app":
        forbidden = forbidden_requires.intersection(
            {_requirement_name(value) for value in metadata.requires}
        )
        if forbidden:
            raise ReleaseBuildError("sf_m3_app wheel has forbidden dependency")
        if "legacy-ui" in {_package_name(value) for value in metadata.provides_extras}:
            raise ReleaseBuildError("sf_m3_app wheel provides removed legacy-ui extra")
        if any(name.startswith("m3_app/ui/") for name in metadata.files):
            raise ReleaseBuildError("sf_m3_app wheel contains removed UI package")
        required = f"sf-m3[storage]=={expected_version}"
        if required.lower() not in {
            re.sub(r"\s+", "", value).lower() for value in metadata.requires
        }:
            raise ReleaseBuildError("app wheel must pin the matching sf-m3 SDK")
    elif expected_name in {"sf_m3", "sf_m3_cli"}:
        forbidden = forbidden_requires.intersection(
            _mandatory_requirements(metadata.requires)
        )
        if forbidden:
            raise ReleaseBuildError(
                f"{expected_name} wheel has forbidden mandatory dependency"
            )
    if expected_name == "sf_m3_cli":
        normalized_requires = {
            re.sub(r"\s+", "", value).lower() for value in metadata.requires
        }
        required = {
            f"sf-m3[storage]=={expected_version}",
            f"sf-m3-app=={expected_version}",
        }
        if not required.issubset(normalized_requires):
            raise ReleaseBuildError(
                "CLI wheel dependencies do not pin m3 and sf-m3-app"
            )
        if "m3 = m3_cli.main:main" not in metadata.entry_points:
            raise ReleaseBuildError("CLI wheel does not own the m3 entry point")


def verify_release(
    out_dir: Path,
    expected: dict[str, str],
    *,
    ui_source_dist: Path | None = None,
) -> dict[str, Path]:
    wheels = tuple(sorted(out_dir.glob("*.whl")))
    if len(wheels) != len(expected):
        raise ReleaseBuildError(
            f"expected exactly {len(expected)} wheels, found {len(wheels)}"
        )
    classified = classify_wheels(wheels, expected)
    metadata = {name: _metadata_from_wheel(path) for name, path in classified.items()}
    for name, info in metadata.items():
        _verify_wheel(info, name, expected[name])

    cli_path = classified["sf_m3_cli"]
    source_maps_in_ui = (
        any(path.suffix.lower() == ".map" for path in ui_source_dist.rglob("*"))
        if ui_source_dist is not None
        else None
    )
    with zipfile.ZipFile(cli_path) as archive:
        names = set(archive.namelist())
        ui_files = {
            name
            for name in names
            if name.startswith("m3_cli/ui/") and not name.endswith("/")
        }
        if "m3_cli/ui/index.html" not in ui_files:
            raise ReleaseBuildError("CLI wheel is missing ui/index.html")
        if not any(name.startswith("m3_cli/ui/assets/") for name in ui_files):
            raise ReleaseBuildError("CLI wheel is missing UI assets")
        if source_maps_in_ui is False and any(
            name.lower().endswith(".map") for name in ui_files
        ):
            raise ReleaseBuildError(
                "CLI wheel contains source maps absent from the production UI"
            )
    return classified


CLI_ROOT = ROOT / "cli"


def project_versions() -> dict[str, str]:
    """Read and validate the versions of all three release projects."""

    versions = {name: _version(project) for name, project in PROJECTS.items()}
    if len(set(versions.values())) != 1:
        details = ", ".join(f"{name}={version}" for name, version in versions.items())
        raise ReleaseBuildError(f"SDK, app, and CLI versions must match ({details})")
    return versions


def build_release(
    ui_dist: str | os.PathLike[str],
    out_dir: str | os.PathLike[str],
    *,
    expected_version: str | None = None,
) -> dict[str, Path]:
    """Build and verify all release wheels, returning their artifact paths."""

    ui = validate_ui_dist(ui_dist, out_dir)
    output = _resolved(out_dir)
    if output.exists() and not output.is_dir():
        raise ReleaseBuildError("output path is not a directory")
    if output.exists() and any(output.iterdir()):
        raise ReleaseBuildError("output directory must be empty")
    expected = project_versions()
    if (
        expected_version is not None
        and next(iter(expected.values())) != expected_version
    ):
        raise ReleaseBuildError(
            f"project version {next(iter(expected.values()))} does not match expected version {expected_version}"
        )
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sf-m3-cli-release-") as temporary:
        staged = _stage_cli(ui, Path(temporary))
        for project in (PROJECTS["sf_m3"], PROJECTS["sf_m3_app"], staged):
            _run_build(project, output)
    return verify_release(output, expected, ui_source_dist=ui)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui-dist", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument(
        "--print-version",
        action="store_true",
        help="print the common SDK/app/CLI version and do not build",
    )
    args = parser.parse_args(argv)
    try:
        versions = project_versions()
        version = next(iter(versions.values()))
        if args.print_version:
            if args.expected_version is not None and version != args.expected_version:
                raise ReleaseBuildError(
                    f"project version {version} does not match expected version {args.expected_version}"
                )
            print(version)
            return 0
        if args.ui_dist is None or args.out_dir is None:
            parser.error(
                "--ui-dist and --out-dir are required unless --print-version is used"
            )
        artifacts = build_release(
            args.ui_dist,
            args.out_dir,
            expected_version=args.expected_version,
        )
    except ReleaseBuildError as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        return 2
    for name in ("sf_m3", "sf_m3_app", "sf_m3_cli"):
        print(f"{name}: {artifacts[name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
