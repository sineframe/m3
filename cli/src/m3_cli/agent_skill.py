"""Install the testing-with-m3 agent skill with the `skills` CLI."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from m3_cli.canary import canary_build

SKILL_NAME = "testing-with-m3"
SKILL_REPOSITORY = "sineframe/m3"
_CI_MARKERS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "CIRCLECI",
    "JENKINS_URL",
    "TF_BUILD",
    "TEAMCITY_VERSION",
    "BITBUCKET_BUILD_NUMBER",
    "CODEBUILD_BUILD_ID",
)
_TIMEOUT_SECONDS = 180


def install_command(cli_version: str) -> list[str]:
    return [
        "npx",
        "--yes",
        "skills@1.7.0",
        "add",
        f"{SKILL_REPOSITORY}#v{cli_version}",
        "--skill",
        SKILL_NAME,
        "--agent",
        "universal",
        "claude-code",
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


def _is_development_install() -> bool:
    try:
        direct_url = importlib.metadata.distribution("sf-m3-cli").read_text(
            "direct_url.json"
        )
        if direct_url is None:
            return False
        payload = json.loads(direct_url)
        if not isinstance(payload, dict):
            return False
        # Checkouts carry a placeholder version with no release tag, so editable,
        # local-directory and VCS installs cannot name a release to install.
        return isinstance(payload.get("dir_info"), dict) or isinstance(
            payload.get("vcs_info"), dict
        )
    except (
        importlib.metadata.PackageNotFoundError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ):
        return False


def _output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _failure_hint(output: str) -> str | None:
    output = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output)
    decorations = "│■◇◆●└┌├─◒◐◓◑"
    lines = [
        line
        for raw_line in output.splitlines()
        if (line := raw_line.strip().lstrip(decorations).strip())
        and re.search(r"[A-Za-z]", line)
    ]
    if not lines:
        return None
    failures = [
        line for line in lines if re.search(r"(fatal:|error)", line, re.IGNORECASE)
    ]
    return failures[-1] if failures else lines[-1]


def _kill_process(process: subprocess.Popen[str]) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def ensure_agent_skill(project_root: Path, cli_version: str, *, enabled: bool) -> None:
    """Install or update the release-matched skill without failing except on interrupts."""

    command = install_command(cli_version)
    cmd = " ".join(command)
    if not enabled:
        print("Agent skill: skipped (--no-skill)")
        return
    if any(os.environ.get(name) for name in _CI_MARKERS):
        print("Agent skill: skipped in CI. To install it, run:")
        print(f"  {cmd}")
        return
    if canary_build() is not None:
        print("Agent skill: skipped for a canary build of M3")
        return
    if _is_development_install():
        print(
            "Agent skill: skipped for a development install of M3. To install it, run:"
        )
        print(f"  {cmd}")
        return

    entry = _lock_entry(project_root)
    managed = entry is not None and entry.get("source") == SKILL_REPOSITORY
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
            if existing in project_paths:
                print(f"  To use the copy for this M3 release, run: {cmd}")
            else:
                print(
                    f"  To replace it with the copy for this M3 release, run: {cmd} -g"
                )
                print(
                    "  Without -g the command adds a second copy to this project "
                    "and leaves the one in your home directory in place."
                )
            return
        action = "installed"

    npx = shutil.which("npx")
    if npx is None:
        print("Agent skill: npx not found. Install Node.js, then run:")
        print(f"  {cmd}")
        return

    env = os.environ.copy()
    env["DISABLE_TELEMETRY"] = "1"
    env["DO_NOT_TRACK"] = "1"
    stdout = ""
    stderr = ""
    succeeded = False
    try:
        if sys.platform == "win32":
            process = subprocess.Popen(
                [npx, *command[1:]],
                cwd=project_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = subprocess.Popen(
                [npx, *command[1:]],
                cwd=project_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        try:
            stdout, stderr = process.communicate(timeout=_TIMEOUT_SECONDS)
            succeeded = process.returncode == 0
        except subprocess.TimeoutExpired:
            _kill_process(process)
            stdout, stderr = process.communicate()
        except BaseException:
            _kill_process(process)
            process.wait()
            raise
    except OSError:
        pass

    if succeeded:
        print(f"Agent skill: {action} {SKILL_NAME} for M3 {cli_version}")
        return

    print(f"Agent skill: could not install {SKILL_NAME}. Run:")
    print(f"  {cmd}")
    output = _output_text(stdout) + "\n" + _output_text(stderr)
    hint = _failure_hint(output)
    if hint is not None:
        print(f"  npx: {hint}")
