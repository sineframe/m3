"""Build the unpublished SDK, application, and standalone CLI wheels.

The CLI wheel is the only artifact that contains the production UI.  The UI is
copied into a temporary copy of ``cli/`` while building; the source checkout is
never modified and no generated UI files are committed.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import email
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

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
_FIREBASE_ASSET_NAMES = {"google-services.json", "googleservice-info.plist"}
_FIREBASE_SIGNATURES = (
    b"firebase/auth",
    b"@firebase/auth",
    b"firebase.initializeapp",
    b"authdomain",
    b"firebaseapp.com",
)
_MODULE_ASSET_EXTENSIONS = {".cjs", ".mjs"}
_SUPABASE_SIGNATURES = (
    b"@supabase",
    b"@supabase/supabase-js",
    b"@supabase/ssr",
    b"supabase-js",
    b"supabase.create_client",
    b"from supabase import",
    b"import supabase",
    b"supabase_auth",
    b"from gotrue",
    b"import gotrue",
    b"postgrest",
    b"supabase.co",
    b"supabase.in",
    b"supabase_url",
    b"supabase_anon_key",
    b"supabase_publishable_key",
    b"supabase_secret_key",
    b"supabase_service_role_key",
    b"sb_publishable_",
    b"sb_secret_",
)
_SUPABASE_PACKAGES = {"supabase", "gotrue", "postgrest", "realtime", "storage3"}
_SUPABASE_KEY_PREFIXES = (b"sb_secret_", b"sb_publishable_")
_JWT_CANDIDATE_RE = re.compile(
    rb"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)(?![A-Za-z0-9_-])"
)
_MAX_JWT_HEADER_BYTES = 4 * 1024
_MAX_JWT_JSON_BYTES = 32 * 1024


class ReleaseBuildError(RuntimeError):
    """A safe, user-facing release build error."""


def _is_firebase_asset(name: str) -> bool:
    lowered = name.lower()
    return "firebase" in lowered or Path(lowered).name in _FIREBASE_ASSET_NAMES


def _contains_firebase_signature(name: str, contents: bytes) -> bool:
    if not name.startswith("m3_cli/") or ".dist-info/" in name:
        return False
    if (
        Path(name).suffix.lower()
        not in {".js", ".json", ".py"} | _MODULE_ASSET_EXTENSIONS
    ):
        return False
    lowered = contents.lower()
    return any(signature in lowered for signature in _FIREBASE_SIGNATURES)


def _is_supabase_asset(name: str) -> bool:
    parts = name.lower().replace("\\", "/").split("/")
    return any(
        part == "supabase" or part.startswith(("supabase.", "supabase_", "supabase-"))
        for part in parts
    )


def _contains_supabase_signature(name: str, contents: bytes) -> bool:
    if not name.startswith("m3_cli/") or ".dist-info/" in name:
        return False
    if (
        Path(name).suffix.lower()
        not in {
            ".js",
            ".json",
            ".py",
            ".html",
            ".toml",
            ".yaml",
            ".yml",
        }
        | _MODULE_ASSET_EXTENSIONS
    ):
        return False
    lowered = contents.lower()
    return any(signature in lowered for signature in _SUPABASE_SIGNATURES)


class _JSONObjectPairs(list[tuple[str, object]]):
    """Preserve duplicate JWT claims so a later benign duplicate cannot hide one."""


def _decode_base64url(value: bytes) -> bytes:
    if len(value) % 4 == 1:
        raise binascii.Error("invalid base64url length")
    return base64.b64decode(
        value + b"=" * (-len(value) % 4), altchars=b"-_", validate=True
    )


def _jwt_header_is_meaningful(encoded: bytes) -> bool:
    if len(encoded) > _MAX_JWT_HEADER_BYTES:
        return False
    try:
        header = json.loads(
            _decode_base64url(encoded).decode("utf-8"),
            object_pairs_hook=_JSONObjectPairs,
        )
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        return False
    if not isinstance(header, _JSONObjectPairs):
        return False
    algorithms = [
        value
        for key, value in header
        if isinstance(key, str)
        and key.casefold() == "alg"
        and isinstance(value, str)
        and value
    ]
    return bool(algorithms)


def _supabase_auth_issuer(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if value.casefold() == "supabase":
        return True
    try:
        return urlsplit(value).path.rstrip("/").endswith("/auth/v1")
    except ValueError:
        return False


def _supabase_jwt_claims(claims: object) -> bool:
    if not isinstance(claims, _JSONObjectPairs):
        return False
    roles: set[str] = set()
    issuers: list[object] = []
    has_project_ref = False
    for key, value in claims:
        if not isinstance(key, str):
            continue
        normalized_key = key.casefold()
        if normalized_key == "role" and isinstance(value, str):
            roles.add(value.casefold())
        elif normalized_key == "iss":
            issuers.append(value)
        elif normalized_key == "ref" and isinstance(value, str) and value:
            has_project_ref = True
    if any(_supabase_auth_issuer(issuer) for issuer in issuers):
        return True
    # Supabase service/browser roles identify credentials without an issuer;
    # legacy anon keys use the project ref. Unrelated tokens may carry a `ref`.
    return (
        "service_role" in roles
        or "authenticated" in roles
        or ("anon" in roles and has_project_ref)
    )


def _contains_supabase_jwt(contents: bytes) -> bool:
    for match in _JWT_CANDIDATE_RE.finditer(contents):
        header_start, header_end = match.span(1)
        payload_start, payload_end = match.span(2)
        header_size = header_end - header_start
        payload_size = payload_end - payload_start
        if header_size > _MAX_JWT_HEADER_BYTES:
            # Avoid rejecting arbitrary dotted strings: fail closed only when a
            # bounded prefix is recognizable as a JSON JWT header.
            prefix_size = (_MAX_JWT_HEADER_BYTES // 4) * 4
            try:
                prefix = _decode_base64url(
                    contents[header_start : header_start + prefix_size]
                )
            except binascii.Error:
                continue
            if prefix.lstrip().startswith(b"{"):
                return True
            continue
        if not _jwt_header_is_meaningful(contents[header_start:header_end]):
            continue
        if payload_size > _MAX_JWT_JSON_BYTES:
            # Claims cannot be safely inspected under the JSON size bound.
            return True
        try:
            claims = json.loads(
                _decode_base64url(contents[payload_start:payload_end]).decode("utf-8"),
                object_pairs_hook=_JSONObjectPairs,
            )
        except RecursionError:
            # A recognized JWT with deeply nested claims cannot be inspected
            # safely, so fail closed without exposing the payload.
            return True
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if _supabase_jwt_claims(claims):
            return True
    return False


def _contains_supabase_credential(name: str, contents: bytes) -> bool:
    name_bytes = name.encode("utf-8", errors="surrogatepass")
    lowered_name = name_bytes.lower()
    lowered_contents = contents.lower()
    return (
        any(prefix in lowered_name for prefix in _SUPABASE_KEY_PREFIXES)
        or any(prefix in lowered_contents for prefix in _SUPABASE_KEY_PREFIXES)
        or _contains_supabase_jwt(name_bytes)
        or _contains_supabase_jwt(contents)
    )


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
    requirement_names = {_requirement_name(value) for value in metadata.requires}
    if requirement_names.intersection({"firebase", "firebase-admin"}):
        raise ReleaseBuildError(f"{expected_name} wheel has a Firebase dependency")
    if any(
        name.startswith("supabase") or name in _SUPABASE_PACKAGES
        for name in requirement_names
    ):
        raise ReleaseBuildError(f"{expected_name} wheel has a Supabase dependency")
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
        if any(_is_firebase_asset(name) for name in names):
            raise ReleaseBuildError("CLI wheel contains Firebase auth assets")
        if any(_is_supabase_asset(name) for name in names):
            raise ReleaseBuildError("CLI wheel contains Supabase auth assets")
    for project_name in ("sf_m3", "sf_m3_app", "sf_m3_cli"):
        with zipfile.ZipFile(classified[project_name]) as archive:
            for entry in archive.infolist():
                contents = archive.read(entry)
                if _contains_supabase_credential(entry.filename, contents):
                    raise ReleaseBuildError(
                        "release wheel contains a Supabase credential"
                    )
                if project_name != "sf_m3_cli":
                    continue
                if _contains_firebase_signature(entry.filename, contents):
                    raise ReleaseBuildError(
                        "CLI wheel contains Firebase auth code or config"
                    )
                if _contains_supabase_signature(entry.filename, contents):
                    raise ReleaseBuildError(
                        "CLI wheel contains Supabase auth code or config"
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
