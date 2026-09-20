"""Project scaffolding for the standalone M3 CLI."""

from __future__ import annotations

import os
import subprocess
import sys
import typing
import uuid
from pathlib import Path

if typing.TYPE_CHECKING:
    import tomli as _tomllib
elif sys.version_info >= (3, 11):
    import tomllib as _tomllib
else:  # pragma: no cover - exercised by Python 3.10
    import tomli as _tomllib

DEFAULT_SUITE = "mcp-behavior"
_ENV_EXAMPLE = """# Copy to .env if it does not exist; otherwise add the keys you need.
# Keep real keys out of .env.example and ensure .env is gitignored.

# Agent model providers
OPENCODE_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# LLMJudge(model=...) reads this key by default.
M3_JUDGE_API_KEY=
"""


def _project_root(value: Path | None) -> Path:
    if value is not None:
        return value.expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return Path.cwd().resolve()
    return Path(result.stdout.strip()).resolve()


def _validate(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label} must not be blank")
    if len(value) > 256:
        raise ValueError(f"{label} must be at most 256 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} must not contain control characters")
    return value


def _toml_string(value: str) -> str:
    return (
        '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    )


def _starter(suite: str) -> str:
    return f'''import pytest


@pytest.mark.m3(suite_name={_toml_string(suite)})
@pytest.mark.skip(reason="TODO: implement this test for your MCP project")
def test_mcp_behavior() -> None:
    """Verify the MCP behavior this suite is meant to protect."""
    # TODO: connect to your MCP server with MCPTestKit, or add the
    # agent fixture to test how an agent uses the server.
    # TODO: assert the expected result or captured tool call.
    pytest.fail("Replace this starter with a real M3 assertion")
'''


def _identity(config: Path) -> dict[str, object] | None:
    try:
        data = _tomllib.loads(config.read_text(encoding="utf-8"))
        project_id = data.get("project_id")
        project_name = data.get("project_name")
        if (
            type(data.get("schema_version")) is not int
            or data["schema_version"] != 1
            or not isinstance(project_id, str)
            or not isinstance(project_name, str)
            or _validate(project_name, "project name") != project_name
            or str(uuid.UUID(project_id)) != project_id
        ):
            return None
        return data
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _style(value: str, code: str) -> str:
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ:
        return f"\x1b[{code}m{value}\x1b[0m"
    return value


def _create_env_example(path: Path) -> bool:
    if path.exists() or path.is_symlink():
        return False
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        try:
            stream.write(_ENV_EXAMPLE)
        except OSError:
            path.unlink(missing_ok=True)
            raise
    return True


def run(args: object) -> int:
    root = _project_root(getattr(args, "project_root", None))
    if not root.is_dir():
        print("m3 init: project root is unavailable", file=sys.stderr)
        return 2
    config = root / "m3.toml"
    starter = root / "tests" / "test_m3_starter.py"
    env_example = root / ".env.example"
    config_present = config.exists() or config.is_symlink()
    starter_present = starter.exists() or starter.is_symlink()
    data = (
        _identity(config)
        if config_present and config.is_file() and not config.is_symlink()
        else None
    )
    if (
        data is not None
        and starter_present
        and starter.is_file()
        and not starter.is_symlink()
    ):
        try:
            created_example = _create_env_example(env_example)
        except OSError:
            print("m3 init: could not create .env.example", file=sys.stderr)
            return 2
        print(f"m3 is already initialized at {root}")
        print(f"Project: {data['project_name']}")
        print(f"Files: {config}, {starter}")
        if created_example:
            print(f"Created {env_example}")
            print("Existing project files unchanged; supplied names were not applied.")
        else:
            print("Nothing changed; supplied names were not applied.")
        print("Edit project_name in m3.toml to rename the project; keep project_id.")
        return 0
    if config_present or starter_present:
        print(
            f"m3 init: partial initialization found at {root}; "
            "repair m3.toml and tests/test_m3_starter.py, then retry.",
            file=sys.stderr,
        )
        return 2

    print(_style("M3 init", "1;36"))
    project_arg = getattr(args, "project_name", None)
    suite_arg = getattr(args, "suite", None)
    interactive = sys.stdin.isatty()
    try:
        if project_arg is None:
            if not interactive:
                raise ValueError(
                    "--project-name is required when stdin is not interactive"
                )
            default = root.name or "project"
            answer = input(f"Project name [{default}]: ").strip()
            project_arg = answer or default
        if suite_arg is None:
            if not interactive:
                raise ValueError("--suite is required when stdin is not interactive")
            answer = input(f"Suite name [{DEFAULT_SUITE}]: ").strip()
            suite_arg = answer or DEFAULT_SUITE
        project_name = _validate(str(project_arg), "project name")
        suite_name = _validate(str(suite_arg), "suite name")
    except (EOFError, KeyboardInterrupt):
        print("m3 init: cancelled", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"m3 init: {exc}", file=sys.stderr)
        return 2

    config_text = (
        "# M3 project identity. Keep project_id stable when renaming.\n"
        "schema_version = 1\n"
        f"project_id = {_toml_string(str(uuid.uuid4()))}\n"
        f"project_name = {_toml_string(project_name)}\n"
    )
    created: list[Path] = []
    tests_dir = starter.parent
    created_tests_dir = False
    try:
        if tests_dir.is_symlink():
            raise OSError("tests path is a symlink")
        if not tests_dir.exists():
            tests_dir.mkdir()
            created_tests_dir = True
        with config.open("x", encoding="utf-8", newline="\n") as stream:
            created.append(config)
            stream.write(config_text)
        with starter.open("x", encoding="utf-8", newline="\n") as stream:
            created.append(starter)
            stream.write(_starter(suite_name))
        if _create_env_example(env_example):
            created.append(env_example)
    except OSError:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        if created_tests_dir:
            try:
                tests_dir.rmdir()
            except OSError:
                pass
        print("m3 init: could not create project files", file=sys.stderr)
        return 2
    print(_style(f"Initialized M3 project {project_name!r} at {root}", "32"))
    print(f"Created {config}")
    print(f"Created {starter}")
    if env_example in created:
        print(f"Created {env_example}")
    print("Next: m3 setup")
    print(
        "For agent or judge tests, add the keys you use to .env (copy .env.example if needed)."
    )
    print("Pass --env-file .env to m3 test to load those keys.")
    print("Then implement and unskip the test, and run:")
    print(f"  m3 test --suite {suite_name} -- tests/test_m3_starter.py")
    return 0
