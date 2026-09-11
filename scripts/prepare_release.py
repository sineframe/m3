"""Prepare a synchronized MCP Pal release.

The command is a manual backup for synchronizing source metadata. Normal
releases derive their version from the pushed tag inside GitHub Actions. This
command updates the SDK, app, and CLI project metadata, keeps the CLI's internal
dependencies pinned to the same version, and refreshes ``uv.lock``. It does not
create a tag or publish anything.

Typical usage::

    uv run --no-project --with packaging python scripts/prepare_release.py 0.2.0a4

Use ``--dry-run`` to inspect the changes without writing files, or ``--check``
to validate an already-prepared release and its lockfile.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    from packaging.version import InvalidVersion, Version
except (
    ImportError
) as exc:  # pragma: no cover - the documented command supplies packaging
    raise SystemExit(
        "packaging is required; run this command through `uv run --with packaging`"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
PROJECT_FILES = {
    "mcp-pal": Path("sdk/pyproject.toml"),
    "mcp-pal-app": Path("app/pyproject.toml"),
    "mcp-pal-cli": Path("cli/pyproject.toml"),
}
CLI_INTERNAL_DEPENDENCIES = {
    "mcp-pal": re.compile(r'(?m)^(\s*"mcp-pal\[storage\]==)([^"\r\n]+)("\s*,?\s*)$'),
    "mcp-pal-app": re.compile(r'(?m)^(\s*"mcp-pal-app==)([^"\r\n]+)("\s*,?\s*)$'),
}
VERSION_PATTERN = re.compile(r'(?m)^(\s*version\s*=\s*["\'])([^"\']+)(["\']\s*)$')


class ReleasePreparationError(ValueError):
    """A safe, user-facing release preparation error."""


@dataclass(frozen=True)
class ReleaseState:
    versions: dict[str, str]
    cli_dependencies: dict[str, str]

    @property
    def current_version(self) -> str:
        values = set(self.versions.values())
        if len(values) != 1:
            details = ", ".join(
                f"{name}={value}" for name, value in self.versions.items()
            )
            raise ReleasePreparationError(f"project versions must match ({details})")
        return next(iter(values))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleasePreparationError(f"could not read {path}") from exc


def _single_match(
    pattern: re.Pattern[str], text: str, description: str, path: Path
) -> re.Match[str]:
    matches = tuple(pattern.finditer(text))
    if len(matches) != 1:
        raise ReleasePreparationError(
            f"{path}: expected exactly one {description}, found {len(matches)}"
        )
    return matches[0]


def _validate_target(value: str) -> str:
    try:
        normalized = str(Version(value))
    except InvalidVersion as exc:
        raise ReleasePreparationError(
            f"{value!r} is not a valid PEP 440 version"
        ) from exc
    if normalized != value:
        print(f"normalized release version {value!r} to {normalized!r}")
    return normalized


def read_state(root: Path = ROOT) -> ReleaseState:
    """Read and validate all release fields without changing any files."""

    versions: dict[str, str] = {}
    contents: dict[str, str] = {}
    for name, relative_path in PROJECT_FILES.items():
        path = root / relative_path
        text = _read(path)
        contents[name] = text
        versions[name] = _single_match(
            VERSION_PATTERN, text, "project version", path
        ).group(2)

    cli_path = root / PROJECT_FILES["mcp-pal-cli"]
    cli_text = contents["mcp-pal-cli"]
    dependencies: dict[str, str] = {}
    for name, pattern in CLI_INTERNAL_DEPENDENCIES.items():
        dependencies[name] = _single_match(
            pattern,
            cli_text,
            f"CLI dependency pin for {name}",
            cli_path,
        ).group(2)

    state = ReleaseState(versions, dependencies)
    current = state.current_version
    mismatched_dependencies = {
        name: value for name, value in dependencies.items() if value != current
    }
    if mismatched_dependencies:
        details = ", ".join(
            f"{name}={value}" for name, value in mismatched_dependencies.items()
        )
        raise ReleasePreparationError(
            f"CLI dependency pins must match project version {current} ({details})"
        )
    return state


def _updated_contents(root: Path, target: str) -> dict[Path, str]:
    state = read_state(root)
    updated: dict[Path, str] = {}
    for _name, relative_path in PROJECT_FILES.items():
        path = root / relative_path
        text = _read(path)
        match = _single_match(VERSION_PATTERN, text, "project version", path)
        updated[path] = text[: match.start(2)] + target + text[match.end(2) :]

    cli_path = root / PROJECT_FILES["mcp-pal-cli"]
    cli_text = updated[cli_path]
    for _name, pattern in CLI_INTERNAL_DEPENDENCIES.items():
        match = _single_match(
            pattern, cli_text, f"CLI dependency pin for {_name}", cli_path
        )
        cli_text = cli_text[: match.start(2)] + target + cli_text[match.end(2) :]
    updated[cli_path] = cli_text

    # Return only changed files for a same-version no-op.
    if state.current_version == target:
        return {path: text for path, text in updated.items() if _read(path) != text}
    return updated


def _run_uv_lock(root: Path, command: list[str]) -> None:
    try:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ReleasePreparationError(
            "could not run uv; install uv or use the repository dev environment"
        ) from exc
    if result.returncode:
        details = (result.stderr or result.stdout).strip()
        suffix = f": {details}" if details else ""
        raise ReleasePreparationError(
            f"{' '.join(command)} failed (exit {result.returncode}){suffix}"
        )


def _write_files(files: dict[Path, str]) -> None:
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")


def prepare(
    version: str,
    *,
    root: Path = ROOT,
    dry_run: bool = False,
    check: bool = False,
    lock_runner: Callable[[Path, list[str]], None] | None = None,
) -> tuple[str, tuple[Path, ...]]:
    """Prepare or validate a release and return its normalized version/files."""

    target = _validate_target(version)
    state = read_state(root)
    files = _updated_contents(root, target)
    lock_path = root / "uv.lock"
    if not lock_path.is_file():
        raise ReleasePreparationError(f"missing lockfile: {lock_path}")

    if check:
        if state.current_version != target:
            raise ReleasePreparationError(
                f"release is not prepared for {target}; project version is {state.current_version}"
            )
        if files:
            raise ReleasePreparationError("release metadata is not synchronized")
        (lock_runner or _run_uv_lock)(root, ["uv", "lock", "--check"])
        return target, ()

    if dry_run:
        return target, tuple(files)

    originals = {path: _read(path) for path in (*files, lock_path)}
    try:
        _write_files(files)
        runner = lock_runner or _run_uv_lock
        runner(root, ["uv", "lock"])
        runner(root, ["uv", "lock", "--check"])
    except (OSError, ReleasePreparationError):
        _write_files(originals)
        raise
    return target, (*tuple(files), lock_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="new PEP 440 version, for example 0.2.0a4")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="show files that would change"
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="validate metadata and uv.lock without changing files",
    )
    args = parser.parse_args(argv)
    try:
        version, files = prepare(args.version, dry_run=args.dry_run, check=args.check)
    except ReleasePreparationError as exc:
        print(f"release preparation failed: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print(f"release {version} is synchronized and uv.lock is current")
    elif args.dry_run:
        print(f"release {version} would update:")
        for path in files:
            print(f"  {path.relative_to(ROOT)}")
    else:
        print(f"prepared release {version}; updated:")
        for path in files:
            print(f"  {path.relative_to(ROOT)}")
        print("next:")
        print("  review, commit, open a PR, and merge it")
        print(f"  after merge, tag the exact default-branch commit as v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
