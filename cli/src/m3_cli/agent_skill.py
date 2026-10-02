"""Install the testing-with-m3 agent skill with the `skills` CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

SKILL_NAME = "testing-with-m3"
SKILL_REPOSITORY = "sineframe/m3"
_CI_MARKERS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI")
_TIMEOUT_SECONDS = 180


def install_command(cli_version: str) -> list[str]:
    return [
        "npx",
        "--yes",
        "skills",
        "add",
        f"{SKILL_REPOSITORY}#v{cli_version}",
        "--skill",
        SKILL_NAME,
        "-y",
    ]


def _lock_entry(project_root: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((project_root / "skills-lock.json").read_text())
        skills = payload.get("skills") if isinstance(payload, dict) else None
        entry = skills.get(SKILL_NAME) if isinstance(skills, dict) else None
        return entry if isinstance(entry, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def ensure_agent_skill(project_root: Path, cli_version: str, *, enabled: bool) -> None:
    """Install or update the release-matched skill without failing the command."""

    command = install_command(cli_version)
    cmd = " ".join(command)
    if not enabled:
        print("Agent skill: skipped (--no-skill)")
        return
    if any(os.environ.get(name) for name in _CI_MARKERS):
        print("Agent skill: skipped in CI")
        return

    entry = _lock_entry(project_root)
    source = ""
    if entry is not None:
        for key in ("source", "sourceUrl"):
            value = entry.get(key, "")
            if isinstance(value, str):
                source += f" {value}"
    managed = entry is not None and SKILL_REPOSITORY in source
    relative_paths = (
        Path(".agents") / "skills" / SKILL_NAME / "SKILL.md",
        Path(".claude") / "skills" / SKILL_NAME / "SKILL.md",
    )
    project_paths = tuple(project_root / path for path in relative_paths)
    home_paths = tuple(Path.home() / path for path in relative_paths)

    if entry is not None and managed:
        if entry.get("ref") == f"v{cli_version}" and any(
            _exists(path) for path in project_paths
        ):
            print(f"Agent skill: {SKILL_NAME} matches M3 {cli_version}")
            return
        action = "updated"
    else:
        existing = next(
            (path for path in (*project_paths, *home_paths) if _exists(path)), None
        )
        if existing is not None:
            print(
                f"Agent skill: using existing {SKILL_NAME} at {existing}; "
                "it was not installed by m3"
            )
            print(f"  To use the copy for this M3 release, run: {cmd}")
            return
        action = "installed"

    npx = shutil.which("npx")
    if npx is None:
        print("Agent skill: npx not found. Install Node.js, then run:")
        print(f"  {cmd}")
        return

    stderr = ""
    try:
        result = subprocess.run(
            [npx, *command[1:]],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=_TIMEOUT_SECONDS,
        )
        stderr = result.stderr
        if result.returncode == 0:
            print(f"Agent skill: {action} {SKILL_NAME} for M3 {cli_version}")
            return
    except (OSError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, subprocess.TimeoutExpired) and isinstance(exc.stderr, str):
            stderr = exc.stderr

    print(f"Agent skill: could not install {SKILL_NAME}. Run:")
    print(f"  {cmd}")
    lines = [line for line in stderr.splitlines() if line.strip()]
    if lines:
        print(f"  npx: {lines[-1]}")
