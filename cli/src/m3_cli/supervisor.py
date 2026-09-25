"""Run pytest with the Python environment belonging to the project."""

from __future__ import annotations

import importlib.metadata
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import typing
from collections import deque
from collections.abc import Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from .branding import M3_ASCII_ART
from .ci_credentials import parse_credential_mapping

if typing.TYPE_CHECKING:
    import tomli as _tomllib
elif sys.version_info >= (3, 11):
    import tomllib as _tomllib
else:  # pragma: no cover
    import tomli as _tomllib

OPERATIONAL_ERROR = 2
_HARNESS_KINDS = {"claude", "claude_code", "opencode", "codex", "pi", "acp"}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RUNTIME_VERSION = re.compile(
    r"^(?:latest|(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9a-z]+(?:\.[0-9a-z]+)*)?)$"
)
_PROGRESS_LINE_LIMIT = 2048
_PROGRESS_TOTAL_LIMIT = 16 * 1024 * 1024


def _format_progress_event(event: Mapping[str, Any]) -> str | None:
    """Render runtime-core acquisition records as compact human progress."""

    def safe_text(value: Any, maximum: int) -> str | None:
        if not isinstance(value, str) or len(value) > maximum:
            return None
        return (
            value if all(32 <= ord(character) <= 126 for character in value) else None
        )

    event_name = event.get("event")
    if not isinstance(event_name, str):
        value = event.get("message", event.get("status"))
        return safe_text(value, 256)
    if event_name not in {
        "acquire_start",
        "download_progress",
        "acquire_complete",
        "cache_hit",
    }:
        return None
    kind = safe_text(event.get("kind"), 64)
    version = safe_text(event.get("resolved_version"), 64)
    prefix = ""
    if kind is not None:
        prefix = {"opencode": "OpenCode", "claude_code": "Claude Code"}.get(
            kind, kind.title()
        )
        if version is not None:
            prefix += f" {version}"
        prefix += ": "
    phase_value = event.get("phase")
    phase = phase_value if isinstance(phase_value, str) else ""
    action = {
        "resolve": "resolving release",
        "download": "downloading",
        "extract": "installing",
        "verify": "verifying",
        "ready": "loaded from cache"
        if event_name == "cache_hit"
        else "ready; running test",
    }.get(phase, event_name)
    detail = ""
    current, total = event.get("bytes"), event.get("total_bytes")
    if (
        isinstance(current, (int, float))
        and not isinstance(current, bool)
        and isinstance(total, (int, float))
        and not isinstance(total, bool)
        and math.isfinite(current)
        and math.isfinite(total)
        and 0 <= current <= total <= 1024**4
        and total > 0
    ):
        detail = f" {current / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f} MB"
    return f"{prefix}{action}{detail}"


def _validate_selection_options(
    harnesses: Sequence[str],
    trials: int | None,
    credential_env: Sequence[str],
    execution_timeout: float | None = None,
    runtime: str = "system",
) -> str | None:
    if runtime not in {"system", "managed"}:
        return "--runtime must be system or managed"
    if execution_timeout is not None and (
        not math.isfinite(execution_timeout) or execution_timeout <= 0
    ):
        return "--execution-timeout must be a positive finite number"
    if trials is not None and (isinstance(trials, bool) or trials <= 0):
        return "--trials must be a positive integer"
    selected: set[tuple[str, str]] = set()
    for raw in harnesses:
        if "=" not in raw:
            return "--harness requires KIND=MODEL[,MODEL...]"
        raw_kind, models = raw.split("=", 1)
        raw_kind = raw_kind.strip()
        version: str | None = None
        if "@" in raw_kind:
            base, version = raw_kind.split("@", 1)
            if runtime != "managed":
                return "versioned harness selectors require --runtime managed"
            if (
                not base.strip()
                or len(version) > 64
                or not _RUNTIME_VERSION.fullmatch(version)
            ):
                return "--harness has an invalid version"
            kind = base.strip().lower().replace("-", "_")
        else:
            kind = raw_kind.lower().replace("-", "_")
        if kind == "claude":
            kind = "claude_code"
        selector = f"{kind}@{version}" if version is not None else kind
        if kind not in _HARNESS_KINDS:
            return f"unknown harness kind {kind!r}"
        if runtime == "managed" and kind == "acp":
            return "ACP does not support --runtime managed"
        if not models.strip() or any(not model.strip() for model in models.split(",")):
            return "--harness contains an empty model"
        for model in models.split(","):
            choice = (selector, model.strip())
            if choice in selected:
                return f"duplicate harness/model selection {kind}={model.strip()}"
            selected.add(choice)
    seen: set[tuple[str | None, str]] = set()
    for raw in credential_env:
        try:
            scope, target, source = parse_credential_mapping(raw)
        except ValueError:
            return "--credential-env requires TARGET=SOURCE"
        if scope is not None and scope not in _HARNESS_KINDS | {"judge"}:
            return f"unknown credential scope {scope!r}"
        if not _ENV_NAME.fullmatch(target) or not _ENV_NAME.fullmatch(source):
            return "credential environment names must be Python identifiers"
        key = (scope, target)
        if key in seen:
            return f"duplicate credential target {target!r}"
        seen.add(key)
    return None


@dataclass(frozen=True)
class StoredRun:
    """The small public-store projection needed by the CLI."""

    run_id: str
    created_at: datetime


@dataclass(frozen=True)
class StoredRuns:
    """A run listing and a safe warning when the database could not be read."""

    runs: tuple[StoredRun, ...] = ()
    warning: str | None = None


@dataclass(frozen=True)
class TestRunResult:
    """Pytest status and the identity and storage location of this invocation."""

    exit_code: int
    new_runs: tuple[StoredRun, ...] = ()
    warnings: tuple[str, ...] = ()
    run_id: str | None = None
    database_path: Path | None = None
    project_root: Path | None = None


def _absolute_path(value: str | os.PathLike[str]) -> Path:
    """Make a path absolute without resolving a venv's Python symlink."""

    return Path(value).expanduser().absolute()


class ProjectPythonError(ValueError):
    """A safe, actionable error while finding or checking project Python."""


class _TerminationSignal(BaseException):
    def __init__(self, signum: int) -> None:
        self.signum = signum


@contextmanager
def _termination_signal_handlers() -> Any:
    """Turn termination signals into cleanup-safe control flow."""

    if os.name != "posix" or threading.current_thread() is not threading.main_thread():
        yield
        return
    handled = tuple(
        candidate
        for candidate in (
            getattr(signal, "SIGTERM", None),
            getattr(signal, "SIGHUP", None),
        )
        if candidate is not None
    )
    previous: dict[int, Any] = {}

    def terminate(signum: int, _frame: Any) -> None:
        raise _TerminationSignal(signum)

    try:
        for candidate in handled:
            previous[int(candidate)] = signal.getsignal(candidate)
            signal.signal(candidate, terminate)
        yield
    finally:
        for candidate, handler in previous.items():
            signal.signal(candidate, handler)


def _python_in_environment(root: Path, environment: Mapping[str, str]) -> Path | None:
    virtual = environment.get("VIRTUAL_ENV")
    if virtual:
        base = Path(virtual).expanduser()
        executable = base / ("Scripts" if os.name == "nt" else "bin") / "python"
        if os.name == "nt" and not executable.exists():
            executable = executable.with_suffix(".exe")
        if executable.is_file():
            return _absolute_path(executable)
    conda = environment.get("CONDA_PREFIX")
    if conda:
        base = Path(conda).expanduser()
        names = (
            [(base / "python.exe")] if os.name == "nt" else [base / "bin" / "python"]
        )
        names.append(
            base / "bin" / "python.exe" if os.name == "nt" else base / "python"
        )
        for executable in names:
            if executable.is_file():
                return _absolute_path(executable)
    venv = root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / "python"
    if os.name == "nt" and not venv.exists():
        venv = venv.with_suffix(".exe")
    if venv.is_file():
        return _absolute_path(venv)
    return None


def _explicit_python(value: str | os.PathLike[str], *, path: str | None = None) -> Path:
    text = os.fspath(value)
    if os.sep not in text and (os.altsep is None or os.altsep not in text):
        found = shutil.which(text, path=path)
        if found:
            return _absolute_path(found)
    return _absolute_path(text)


def resolve_project_python(
    explicit: str | os.PathLike[str] | None = None,
    *,
    project_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
    fallback_to_system: bool = True,
) -> Path:
    """Resolve Python in the documented order, without importing project code."""

    root = (project_root or Path.cwd()).resolve()
    env = os.environ if environment is None else environment
    if explicit is not None:
        return _explicit_python(explicit, path=env.get("PATH"))
    if not fallback_to_system:
        for variable in ("VIRTUAL_ENV", "CONDA_PREFIX"):
            active = env.get(variable)
            if active:
                candidate = _python_in_environment(root, {variable: active})
                if candidate is None:
                    raise ProjectPythonError(
                        "the active project environment is unavailable"
                    )
                return candidate
    selected = _python_in_environment(root, env)
    if selected is not None:
        return selected
    if not fallback_to_system:
        raise ProjectPythonError("no project environment is configured; run m3 setup")
    for name in ("python3", "python"):
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            return _absolute_path(found)
    raise ProjectPythonError("no project Python was found; pass --python PATH")


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


def _cli_sdk_version() -> str:
    try:
        return importlib.metadata.version("sf-m3")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ProjectPythonError(
            "the CLI SDK version is unavailable; reinstall sf-m3-cli"
        ) from exc


def validate_project_python(
    python: Path,
    *,
    cli_sdk_version: str | None = None,
    project_root: Path | None = None,
) -> str:
    """Check project imports in a child process and enforce the SDK version."""

    try:
        result = subprocess.run(
            [str(python), "-c", _VALIDATE_SCRIPT],
            cwd=str((project_root or Path.cwd()).resolve()),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProjectPythonError(
            "the selected project Python could not be started"
        ) from None
    if result.returncode != 0:
        raise ProjectPythonError("the selected project Python could not be started")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError, TypeError):
        raise ProjectPythonError(
            "the selected project Python returned an invalid check result"
        ) from None
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        raise ProjectPythonError(
            "the selected project Python returned an invalid check result"
        )
    missing = [
        name
        for name in (
            "pytest",
            "m3",
            "m3.pytest_plugin",
            "openai",
            "SQLiteExecutionStore",
        )
        if not checks.get(name)
    ]
    if missing:
        names = ", ".join(missing)
        raise ProjectPythonError(
            f"the project Python is missing required M3 packages: {names}; run m3 setup in the project"
        )
    project_version = payload.get("version")
    expected = _cli_sdk_version() if cli_sdk_version is None else cli_sdk_version
    if not isinstance(project_version, str):
        raise ProjectPythonError(
            "the project m3 distribution version could not be determined"
        )
    if project_version != expected:
        raise ProjectPythonError(
            f"project m3 version {project_version} does not match CLI SDK version {expected}; install matching versions"
        )
    return project_version


def _absolute_database(
    value: str | os.PathLike[str] | None, *, project_root: Path | None = None
) -> Path:
    root = (project_root or Path.cwd()).resolve()
    return (
        Path(value).expanduser() if value else root / ".m3" / "executions.sqlite"
    ).resolve()


def _test_environment(env_file: str | os.PathLike[str] | None) -> dict[str, str] | None:
    """Return a child environment with explicitly requested dotenv values."""
    if env_file is None:
        return None
    path = Path(env_file).expanduser()
    if not path.is_file():
        raise ProjectPythonError(f"env file was not found: {path}")
    try:
        from dotenv import dotenv_values

        values = dotenv_values(str(path), interpolate=False)
    except Exception as exc:
        raise ProjectPythonError(f"could not read env file: {path}") from exc
    child = dict(os.environ)
    for key, value in values.items():
        if key and value is not None and key not in child:
            child[key] = value
    return child


def _execution_store_type() -> Any:
    """Load the optional SQLite store only when run discovery is requested."""

    from m3.storage import SQLiteExecutionStore

    return SQLiteExecutionStore


def list_stored_runs(database: Path) -> StoredRuns:
    """List pytest run manifests, whose IDs identify feedback reports."""

    store: Any | None = None
    try:
        store = _execution_store_type()(database)
        runs: list[StoredRun] = []
        for manifest in store.list_test_runs():
            run_id = manifest.get("run_id")
            created_at = manifest.get("created_at")
            if (
                not isinstance(run_id, str)
                or not run_id
                or not isinstance(created_at, str)
            ):
                continue
            try:
                timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if timestamp.utcoffset() is None:
                continue
            runs.append(StoredRun(run_id=run_id, created_at=timestamp))
        return StoredRuns(tuple(runs))
    except Exception:
        # The test process must remain useful even when an old, locked, or
        # unreadable history database cannot be inspected.
        return StoredRuns(warning="could not read stored run history")
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


_BASELINE_SCRIPT = r"""
import json
import sys
from m3.storage import SQLiteExecutionStore

store = SQLiteExecutionStore(sys.argv[1])
try:
    run_id = sys.argv[2]
    expected_project = sys.argv[3] or None
    manifest = store.get_test_run(run_id)
    page = store.list_executions(limit=1, offset=0, run_id=run_id)
    found = manifest is not None or bool(page.items)
    actual_project = manifest.get("project_id") if manifest else None
    if actual_project is None and page.items:
        project = page.items[0].project_id
        actual_project = project.root if project is not None else None
    if expected_project is not None:
        found = found and actual_project == expected_project
finally:
    close = getattr(store, "close", None)
    if callable(close):
        close()
print(json.dumps({"found": bool(found)}))
"""


def _project_id(root: Path) -> str | None:
    path = root / "m3.toml"
    if not path.is_file():
        return None
    try:
        value = _tomllib.loads(path.read_text(encoding="utf-8"))
        from uuid import UUID

        identifier = str(value["project_id"])
        return identifier if str(UUID(identifier)) == identifier else None
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return None


def baseline_exists(
    database: Path,
    run_id: str,
    *,
    python: Path | None = None,
    project_root: Path | None = None,
    project_id: str | None = None,
) -> bool:
    """Validate an explicit baseline before starting project pytest.

    When project Python is supplied, the check runs through that environment so
    the CLI never interprets a project database using a different SDK build.
    """
    if python is not None:
        try:
            result = subprocess.run(
                [
                    str(python),
                    "-c",
                    _BASELINE_SCRIPT,
                    str(database),
                    str(run_id),
                    project_id or "",
                ],
                cwd=str((project_root or Path.cwd()).resolve()),
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            return result.returncode == 0 and bool(payload.get("found"))
        except (
            OSError,
            subprocess.TimeoutExpired,
            IndexError,
            json.JSONDecodeError,
            AttributeError,
        ):
            return False
    store: Any | None = None
    try:
        store = _execution_store_type()(database)
        get_manifest = getattr(store, "get_test_run", None)
        manifest = get_manifest(run_id) if callable(get_manifest) else None
        page = store.list_executions(limit=1, offset=0, run_id=run_id)
        if manifest is None and not page.items:
            return False
        if project_id is None:
            return True
        actual = manifest.get("project_id") if manifest else None
        if actual is None and page.items:
            value = page.items[0].project_id
            actual = value.root if value is not None else None
        return actual == project_id
    except Exception:
        return False
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


def find_new_runs(before: StoredRuns, after: StoredRuns) -> tuple[StoredRun, ...]:
    """Return after-runs whose IDs were absent before pytest started."""

    existing = {run.run_id for run in before.runs}
    unique: dict[str, StoredRun] = {}
    for run in after.runs:
        if run.run_id not in existing:
            unique[run.run_id] = run
    return tuple(sorted(unique.values(), key=lambda run: (run.created_at, run.run_id)))


def build_run_url(run_id: str, port: int, auth_token: str | None = None) -> str:
    """Build an encoded feedback report URL for the local UI."""

    origin = f"http://127.0.0.1:{port}"
    encoded_run_id = quote(str(run_id), safe="")
    url = f"{origin}/reports/runs/{encoded_run_id}"
    return f"{url}#m3_token={auth_token}" if auth_token is not None else url


def build_ui_url(port: int, auth_token: str) -> str:
    """Build the authenticated link to the saved test-runs index."""

    return f"http://127.0.0.1:{port}/reports#m3_token={auth_token}"


def _validate_port(port: int) -> str | None:
    if not 1 <= port <= 65535:
        return "port must be between 1 and 65535"
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
    except OSError:
        return "port is already in use"
    finally:
        probe.close()
    return None


def _ui_prerequisite_error(ui_dir: str | os.PathLike[str] | None) -> str | None:
    try:
        from .web import ui_directory

        ui_directory(ui_dir)
    except (OSError, TypeError, ValueError):
        return "bundled M3 UI assets are unavailable"
    return None


def _server_command(database: Path, port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "m3_cli.web",
        "--database-path",
        str(database),
        "--port",
        str(port),
    ]


class _ServerChild:
    _READY_MARKER = "\x00M3_UI_SERVER_READY\x00"

    def __init__(self, database: Path, port: int, auth_token: str) -> None:
        kwargs: dict[str, Any] = {
            "env": _server_environment(),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif os.name == "nt":
            flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            if flags:
                kwargs["creationflags"] = flags
        self.lines: deque[str] = deque(maxlen=20)
        self._startup_ready = threading.Event()
        self.process = subprocess.Popen(_server_command(database, port), **kwargs)
        if self.process.stdin is None:
            _terminate_process(self.process)
            raise OSError("could not deliver local UI credentials")
        try:
            self.process.stdin.write(auth_token + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            _terminate_process(self.process)
            raise OSError("could not deliver local UI credentials") from None
        finally:
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                line = line.rstrip("\r\n")
                if line == self._READY_MARKER:
                    self._startup_ready.set()
                else:
                    self.lines.append(line)
        except (OSError, ValueError):
            pass

    def alive(self) -> bool:
        return self.process.poll() is None


def _wait_ready(child: _ServerChild, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not child.alive():
            return False
        remaining = deadline - time.monotonic()
        if child._startup_ready.wait(min(remaining, 0.05)):
            return child.alive()
    return False


def _server_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Isolate the viewer from project test settings and credentials."""

    values = os.environ if source is None else source
    environment_links = {
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "PYTHONPATH",
        "UV_PROJECT_ENVIRONMENT",
    }
    return {
        key: value
        for key, value in values.items()
        if key.upper() not in environment_links
        and not key.upper().startswith("M3_")
        and not any(
            part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")
        )
    }


def _server_diagnostics(
    child: _ServerChild, auth_token: str | None = None
) -> tuple[str, ...]:
    """Return bounded server output with ambient credentials removed."""

    secret_values = tuple(
        value
        for key, value in os.environ.items()
        if value
        and len(value) >= 4
        and any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    )
    safe: list[str] = []
    for raw in child.lines:
        line = raw[:1000]
        if auth_token:
            line = line.replace(auth_token, "<redacted>")
        for value in secret_values:
            line = line.replace(value, "<redacted>")
        line = re.sub(
            r"(?i)(\b(?:api[_-]?key|token|secret|password|credential)\b\s*(?:=|:)\s*)[^\s,;]+",
            r"\1<redacted>",
            line,
        )
        safe.append(line)
    return tuple(safe[-5:])


def pytest_command(
    python: Path,
    database: Path,
    pytest_args: Sequence[str],
    *,
    baseline: str | None = None,
    project_root: Path | None = None,
    harnesses: Sequence[str] = (),
    server_selections: Sequence[Mapping[str, object]] = (),
    trials: int | None = None,
    suite: str | None = None,
    credential_env: Sequence[str] = (),
    execution_timeout: float | None = None,
    judge_max_requests: int | None = None,
    runtime: str = "system",
    ci_mode: bool = False,
    run_id: str | None = None,
    ci_metadata: Mapping[str, object] | None = None,
) -> list[str]:
    command = [
        str(python),
        "-m",
        "pytest",
        "-p",
        "m3.pytest_plugin",
        "--results-db",
        str(database),
    ]
    if baseline:
        command.extend(("--baseline", baseline))
    if ci_mode:
        command.append("--m3-ci")
    if run_id is not None:
        command.extend(("--m3-run-id", run_id))
    if ci_metadata is not None:
        command.extend(
            (
                "--m3-ci-metadata",
                json.dumps(ci_metadata, ensure_ascii=True, separators=(",", ":")),
            )
        )
    if project_root is not None:
        command.extend(("--project-root", str(project_root)))
    if project_root is not None and not _has_rootdir_option(pytest_args):
        command.extend(("--rootdir", str(project_root)))
    for value in harnesses:
        command.extend(("--harness", value))
    if server_selections:
        command.extend(("--m3-server-selections", json.dumps(list(server_selections))))
    if runtime == "managed":
        command.extend(("--runtime", "managed"))
    for value in credential_env:
        command.extend(("--credential-env", value))
    if trials is not None:
        command.extend(("--trials", str(trials)))
    if suite is not None:
        command.extend(("--suite", suite))
    if execution_timeout is not None:
        command.extend(("--execution-timeout", str(execution_timeout)))
    if judge_max_requests is not None:
        command.extend(("--judge-max-requests", str(judge_max_requests)))
    command.extend(pytest_args)
    return command


def _has_rootdir_option(args: Sequence[str]) -> bool:
    return any(arg == "--rootdir" or arg.startswith("--rootdir=") for arg in args)


_RESERVED_PYTEST_OPTIONS = frozenset(
    {
        "--results-db",
        "--project-root",
        "--credential-env",
        "--m3-server-selections",
        "--m3-ci",
        "--m3-run-id",
        "--m3-ci-metadata",
    }
)


def _passthrough_option_error(args: Sequence[str]) -> str | None:
    """Keep CLI-owned run state out of raw pytest passthrough arguments."""
    for arg in args:
        if arg.startswith("@"):
            return "pytest response files are not supported in m3 passthrough"
        option = arg.split("=", 1)[0]
        if option in _RESERVED_PYTEST_OPTIONS:
            return f"{option} must be set through m3, not pytest passthrough"
    return None


# Keep the implementation name easy to discover for callers that used the old
# supervisor's private command helper while making the Python argument explicit.
_pytest_command = pytest_command


def _terminate_process(process: Any) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
            process.send_signal(
                ctrl_break
            ) if ctrl_break is not None else process.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=3)
    except (subprocess.TimeoutExpired, OSError):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _run_pytest_process(
    python: Path,
    database: Path,
    pytest_args: Sequence[str],
    *,
    baseline: str | None = None,
    project_root: Path | None = None,
    harnesses: Sequence[str] = (),
    server_selections: Sequence[Mapping[str, object]] = (),
    trials: int | None = None,
    suite: str | None = None,
    credential_env: Sequence[str] = (),
    execution_timeout: float | None = None,
    judge_max_requests: int | None = None,
    environment: Mapping[str, str] | None = None,
    runtime: str = "system",
    harness_cache_dir: str | os.PathLike[str] | None = None,
    ci_mode: bool = False,
    run_id: str | None = None,
    ci_metadata: Mapping[str, object] | None = None,
) -> int:
    """Run pytest with safe process-group cleanup and return its status."""

    process: subprocess.Popen[Any] | None = None
    runtime_dir: Path | None = None
    progress: _ProgressStream | None = None
    try:
        with _termination_signal_handlers():
            kwargs: dict[str, Any] = {}
            if os.name == "posix":
                kwargs["start_new_session"] = True
            elif os.name == "nt":
                flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                if flags:
                    kwargs["creationflags"] = flags
            if project_root is not None:
                kwargs["cwd"] = str(project_root)
            child_environment = (
                dict(environment) if environment is not None else dict(os.environ)
            )
            if runtime == "managed":
                runtime_dir = Path(tempfile.mkdtemp(prefix="m3-runtime-"))
                os.chmod(runtime_dir, 0o700)
                progress_path = runtime_dir / "progress.jsonl"
                progress_path.touch(mode=0o600)
                os.chmod(progress_path, 0o600)
                pin_dir = runtime_dir / "invocation-pin"
                pin_dir.mkdir(mode=0o700)
                # The aliases keep the boundary compatible across runtime-core versions.
                for key in (
                    "M3_RUNTIME_PROGRESS_FILE",
                    "M3_MANAGED_RUNTIME_PROGRESS_FILE",
                ):
                    child_environment[key] = str(progress_path)
                for key in ("M3_INVOCATION_PIN_DIR", "M3_RUNTIME_INVOCATION_DIR"):
                    child_environment[key] = str(pin_dir)
                if harness_cache_dir is not None:
                    child_environment["M3_HARNESS_CACHE_DIR"] = str(
                        Path(harness_cache_dir).expanduser().absolute()
                    )
                progress = _ProgressStream(progress_path)
            process = subprocess.Popen(
                pytest_command(
                    python,
                    database,
                    pytest_args,
                    baseline=baseline,
                    project_root=project_root,
                    harnesses=harnesses,
                    server_selections=server_selections,
                    trials=trials,
                    suite=suite,
                    credential_env=credential_env,
                    execution_timeout=execution_timeout,
                    judge_max_requests=judge_max_requests,
                    runtime=runtime,
                    ci_mode=ci_mode,
                    run_id=run_id,
                    ci_metadata=ci_metadata,
                ),
                env=child_environment,
                **kwargs,
            )
            try:
                while True:
                    if progress is not None:
                        progress.poll()
                    try:
                        raw_code = int(process.wait(timeout=0.2))
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if progress is not None:
                    progress.poll()
            except KeyboardInterrupt:
                _terminate_process(process)
                return 130
            return 128 + (-raw_code) if raw_code < 0 else raw_code
    except _TerminationSignal as exc:
        if process is not None and process.poll() is None:
            _terminate_process(process)
        return 128 + exc.signum
    except (OSError, ValueError):
        if process is not None and process.poll() is None:
            _terminate_process(process)
        print("m3 test: pytest could not be started", file=sys.stderr)
        return OPERATIONAL_ERROR
    finally:
        if progress is not None:
            progress.close()
        if runtime_dir is not None:
            shutil.rmtree(runtime_dir, ignore_errors=True)


class _ProgressStream:
    """Bounded, replacement-safe JSONL progress reader."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self._inode: tuple[int, int] | None = None
        self.total = 0
        self._pending = b""
        self._discard_long_line = False
        self._last: tuple[str, str] | None = None
        self._last_print = 0.0

    def poll(self) -> None:
        if self.total >= _PROGRESS_TOTAL_LIMIT:
            return
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                inode = (info.st_dev, info.st_ino)
                if self._inode != inode or info.st_size < self.offset:
                    self.offset = 0
                    self._pending = b""
                    self._discard_long_line = False
                self._inode = inode
                if info.st_size <= self.offset:
                    return
                stream.seek(self.offset)
                data = stream.read(
                    min(
                        info.st_size - self.offset,
                        _PROGRESS_TOTAL_LIMIT - self.total,
                        1024 * 1024,
                    )
                )
            self.offset += len(data)
        except OSError:
            return
        self.total += len(data)
        chunks = (self._pending + data).split(b"\n")
        discard_first = self._discard_long_line
        self._discard_long_line = False
        self._pending = chunks.pop()
        if len(self._pending) > _PROGRESS_LINE_LIMIT:
            self._pending = b""
            self._discard_long_line = True
        for index, raw in enumerate(chunks):
            if discard_first and index == 0:
                continue
            if len(raw) > _PROGRESS_LINE_LIMIT:
                continue
            try:
                event = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            message = _format_progress_event(event)
            if message is None:
                continue
            resolved = event.get("resolved_version")
            if resolved is not None and not isinstance(resolved, str):
                continue
            key = (message, resolved or "")
            now = time.monotonic()
            if key == self._last and now - self._last_print < 0.2:
                continue
            self._last, self._last_print = key, now
            print(f"m3: {message}", flush=True)

    def close(self) -> None:
        self.poll()


def _prepare_test(
    python: str | os.PathLike[str] | None,
    database: str | os.PathLike[str] | None,
    root: Path,
) -> tuple[Path, Path] | None:
    database_path = _absolute_database(database, project_root=root)
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        selected = resolve_project_python(python, project_root=root)
        validate_project_python(selected, project_root=root)
    except (OSError, ProjectPythonError) as exc:
        print(f"m3 test: {exc}", file=sys.stderr)
        return None
    return selected, database_path


def _stop_server(child: _ServerChild | None) -> None:
    if child is None:
        return
    if child.process.poll() is None:
        _terminate_process(child.process)
    else:
        try:
            child.process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _print_ui_output(
    port: int,
    auth_token: str,
    new_runs: Sequence[StoredRun],
    warnings: Sequence[str],
    *,
    history_index: bool = False,
) -> None:
    for warning in dict.fromkeys(warnings):
        print(f"Warning: {warning}", file=sys.stderr)
    if history_index:
        print(f"UI: {build_ui_url(port, auth_token)}", flush=True)
        return
    if not new_runs:
        print("No new stored runs.", flush=True)
        return
    for run in new_runs:
        print(f"Run: {build_run_url(run.run_id, port, auth_token)}", flush=True)


def _run_ui_server(
    database: Path,
    port: int,
    pytest_code: int,
    new_runs: Sequence[StoredRun],
    warnings: Sequence[str],
    *,
    history_index: bool = False,
) -> int:
    child: _ServerChild | None = None
    auth_token = secrets.token_urlsafe(32)
    try:
        child = _ServerChild(database, port, auth_token)
        if not _wait_ready(child):
            print("m3: UI server readiness failed", file=sys.stderr)
            for line in _server_diagnostics(child, auth_token):
                print(f"m3: UI server: {line}", file=sys.stderr)
            return OPERATIONAL_ERROR
        print(M3_ASCII_ART, flush=True)
        _print_ui_output(
            port, auth_token, new_runs, warnings, history_index=history_index
        )
        with _termination_signal_handlers():
            while True:
                if not child.alive():
                    print("m3: UI server stopped unexpectedly", file=sys.stderr)
                    return OPERATIONAL_ERROR
                time.sleep(0.1)
    except KeyboardInterrupt:
        return pytest_code
    except _TerminationSignal as exc:
        return 128 + exc.signum
    except (OSError, ValueError):
        print("m3: UI server could not be started", file=sys.stderr)
        return OPERATIONAL_ERROR
    finally:
        _stop_server(child)


def _history_database_error(database: Path) -> str | None:
    """Inspect existing history without initializing or migrating a store."""

    if database.parent.is_symlink() or database.is_symlink():
        return "history database must not be a symlink"
    if not database.is_file():
        return "no saved history found"
    try:
        # The SDK store constructor creates and migrates databases. Preflight
        # must instead reject an unrelated or broken file without modifying it.
        with closing(
            sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        ) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('v2_executions', 'v2_test_runs')"
                )
            }
            if tables != {"v2_executions", "v2_test_runs"}:
                return "file is not an M3 history database"
            connection.execute(
                "SELECT id, snapshot_json, created_at FROM v2_executions LIMIT 1"
            ).fetchone()
            connection.execute(
                "SELECT run_id, record_json, created_at, updated_at "
                "FROM v2_test_runs LIMIT 1"
            ).fetchone()
    except (OSError, sqlite3.Error):
        return "history database is unreadable or invalid"
    return None


def run_ui(*, port: int = 8000) -> int:
    """Serve existing history from the current directory without running pytest."""

    database = Path.cwd() / ".m3" / "executions.sqlite"
    error = _history_database_error(database)
    if error is not None:
        print(
            f"m3 ui: {error} at {database}; "
            "run from the directory containing .m3/executions.sqlite",
            file=sys.stderr,
        )
        return OPERATIONAL_ERROR
    port_error = _validate_port(port)
    if port_error is not None:
        print(f"m3 ui: UI startup failed ({port_error})", file=sys.stderr)
        return OPERATIONAL_ERROR
    ui_error = _ui_prerequisite_error(None)
    if ui_error is not None:
        print(f"m3 ui: {ui_error}", file=sys.stderr)
        return OPERATIONAL_ERROR
    return _run_ui_server(database, port, 0, (), (), history_index=True)


def run_test_with_runs(
    *,
    python: str | os.PathLike[str] | None = None,
    pytest_args: Sequence[str] = (),
    database: str | os.PathLike[str] | None = None,
    ui: bool = False,
    port: int = 8000,
    ui_dir: str | os.PathLike[str] | None = None,
    project_root: Path | None = None,
    baseline: str | None = None,
    harnesses: Sequence[str] = (),
    server_selections: Sequence[Mapping[str, object]] = (),
    trials: int | None = None,
    suite: str | None = None,
    credential_env: Sequence[str] = (),
    env_file: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
    execution_timeout: float | None = None,
    judge_max_requests: int | None = None,
    runtime: str = "system",
    harness_cache_dir: str | os.PathLike[str] | None = None,
    ci_mode: bool = False,
    run_id: str | None = None,
    ci_metadata: Mapping[str, object] | None = None,
) -> TestRunResult:
    """Run pytest and retain newly stored runs for optional UI serving."""

    passthrough_error = _passthrough_option_error(pytest_args)
    if passthrough_error is not None:
        print(f"m3 test: {passthrough_error}", file=sys.stderr)
        return TestRunResult(OPERATIONAL_ERROR)

    option_error = _validate_selection_options(
        harnesses, trials, credential_env, execution_timeout, runtime
    )
    if suite is not None and not suite.strip():
        print("m3 test: --suite must not be blank", file=sys.stderr)
        return TestRunResult(2)
    if option_error is not None:
        print(f"m3 test: {option_error}", file=sys.stderr)
        return TestRunResult(OPERATIONAL_ERROR)

    if ui:
        port_error = _validate_port(port)
        if port_error is not None:
            print(f"m3 test: UI startup failed ({port_error})", file=sys.stderr)
            return TestRunResult(OPERATIONAL_ERROR)
        ui_error = _ui_prerequisite_error(ui_dir)
        if ui_error is not None:
            print(f"m3 test: {ui_error}", file=sys.stderr)
            return TestRunResult(OPERATIONAL_ERROR)
    root = (project_root or Path.cwd()).resolve()
    invocation_run_id = run_id or f"run-{uuid4().hex}"
    database_path = _absolute_database(database, project_root=root)
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        print(
            "m3 test: results database directory could not be created",
            file=sys.stderr,
        )
        return TestRunResult(
            OPERATIONAL_ERROR,
            run_id=invocation_run_id,
            database_path=database_path,
            project_root=root,
        )

    before = list_stored_runs(database_path)
    prepared = _prepare_test(python, database, root)
    if prepared is None:
        return TestRunResult(
            OPERATIONAL_ERROR,
            warnings=tuple(filter(None, (before.warning,))),
            run_id=invocation_run_id,
            database_path=database_path,
            project_root=root,
        )
    selected, database_path = prepared
    if baseline is not None and not baseline_exists(
        database_path,
        baseline,
        python=selected,
        project_root=root,
        project_id=_project_id(root),
    ):
        print(f"m3 test: baseline run was not found: {baseline}", file=sys.stderr)
        return TestRunResult(
            OPERATIONAL_ERROR,
            warnings=tuple(filter(None, (before.warning,))),
            run_id=invocation_run_id,
            database_path=database_path,
            project_root=root,
        )

    try:
        child_environment = (
            dict(environment)
            if environment is not None
            else _test_environment(env_file)
        )
    except ProjectPythonError as exc:
        print(f"m3 test: {exc}", file=sys.stderr)
        return TestRunResult(
            OPERATIONAL_ERROR, warnings=tuple(filter(None, (before.warning,)))
        )
    exit_code = _run_pytest_process(
        selected,
        database_path,
        pytest_args,
        baseline=baseline,
        project_root=root,
        harnesses=harnesses,
        server_selections=server_selections,
        trials=trials,
        credential_env=credential_env,
        execution_timeout=execution_timeout,
        judge_max_requests=judge_max_requests,
        suite=suite,
        environment=child_environment,
        runtime=runtime,
        harness_cache_dir=harness_cache_dir,
        ci_mode=ci_mode,
        run_id=invocation_run_id,
        ci_metadata=ci_metadata,
    )
    after = list_stored_runs(database_path)
    warnings = tuple(
        dict.fromkeys(
            warning
            for warning in (before.warning, after.warning)
            if warning is not None
        )
    )
    new_runs = find_new_runs(before, after)
    if not ui:
        return TestRunResult(
            exit_code, new_runs, warnings, invocation_run_id, database_path, root
        )
    if exit_code in {2, 130} or 128 <= exit_code < 256:
        return TestRunResult(
            exit_code, new_runs, warnings, invocation_run_id, database_path, root
        )
    server_code = _run_ui_server(database_path, port, exit_code, new_runs, warnings)
    return TestRunResult(
        server_code, new_runs, warnings, invocation_run_id, database_path, root
    )


def run_test(
    *,
    python: str | os.PathLike[str] | None = None,
    pytest_args: Sequence[str] = (),
    database: str | os.PathLike[str] | None = None,
    ui: bool = False,
    port: int = 8000,
    ui_dir: str | os.PathLike[str] | None = None,
    project_root: Path | None = None,
    baseline: str | None = None,
    harnesses: Sequence[str] = (),
    server_selections: Sequence[Mapping[str, object]] = (),
    trials: int | None = None,
    suite: str | None = None,
    credential_env: Sequence[str] = (),
    env_file: str | os.PathLike[str] | None = None,
    execution_timeout: float | None = None,
    judge_max_requests: int | None = None,
    runtime: str = "system",
    harness_cache_dir: str | os.PathLike[str] | None = None,
    ci_mode: bool = False,
    run_id: str | None = None,
    ci_metadata: Mapping[str, object] | None = None,
) -> int:
    """Run pytest and return its exact exit status."""

    passthrough_error = _passthrough_option_error(pytest_args)
    if passthrough_error is not None:
        print(f"m3 test: {passthrough_error}", file=sys.stderr)
        return OPERATIONAL_ERROR

    if suite is not None and not suite.strip():
        print("m3 test: --suite must not be blank", file=sys.stderr)
        return 2

    option_error = _validate_selection_options(
        harnesses, trials, credential_env, execution_timeout, runtime
    )
    if option_error is not None:
        print(f"m3 test: {option_error}", file=sys.stderr)
        return OPERATIONAL_ERROR

    if ui:
        return run_test_with_runs(
            python=python,
            pytest_args=pytest_args,
            database=database,
            ui=ui,
            port=port,
            ui_dir=ui_dir,
            project_root=project_root,
            baseline=baseline,
            harnesses=harnesses,
            server_selections=server_selections,
            trials=trials,
            suite=suite,
            credential_env=credential_env,
            env_file=env_file,
            execution_timeout=execution_timeout,
            judge_max_requests=judge_max_requests,
            runtime=runtime,
            harness_cache_dir=harness_cache_dir,
            ci_mode=ci_mode,
            run_id=run_id,
            ci_metadata=ci_metadata,
        ).exit_code
    root = (project_root or Path.cwd()).resolve()
    prepared = _prepare_test(python, database, root)
    if prepared is None:
        return OPERATIONAL_ERROR
    selected, database_path = prepared
    if baseline is not None and not baseline_exists(
        database_path,
        baseline,
        python=selected,
        project_root=root,
        project_id=_project_id(root),
    ):
        print(f"m3 test: baseline run was not found: {baseline}", file=sys.stderr)
        return OPERATIONAL_ERROR
    try:
        child_environment = _test_environment(env_file)
    except ProjectPythonError as exc:
        print(f"m3 test: {exc}", file=sys.stderr)
        return OPERATIONAL_ERROR
    invocation_run_id = run_id or f"run-{uuid4().hex}"
    return _run_pytest_process(
        selected,
        database_path,
        pytest_args,
        baseline=baseline,
        project_root=root,
        harnesses=harnesses,
        server_selections=server_selections,
        trials=trials,
        credential_env=credential_env,
        execution_timeout=execution_timeout,
        judge_max_requests=judge_max_requests,
        suite=suite,
        environment=child_environment,
        runtime=runtime,
        harness_cache_dir=harness_cache_dir,
        ci_mode=ci_mode,
        run_id=invocation_run_id,
        ci_metadata=ci_metadata,
    )


def run_ci_test(**kwargs: Any) -> TestRunResult:
    """Run CI-selected pytest tests and return this invocation's exact run."""

    kwargs["ci_mode"] = True
    kwargs["ui"] = False
    return run_test_with_runs(**kwargs)


__all__ = [
    "OPERATIONAL_ERROR",
    "ProjectPythonError",
    "StoredRun",
    "StoredRuns",
    "TestRunResult",
    "baseline_exists",
    "build_run_url",
    "find_new_runs",
    "list_stored_runs",
    "pytest_command",
    "resolve_project_python",
    "run_ci_test",
    "run_test",
    "run_test_with_runs",
    "validate_project_python",
]
