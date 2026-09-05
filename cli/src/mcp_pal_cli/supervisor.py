"""Run pytest with the Python environment belonging to the project."""

from __future__ import annotations

from contextlib import contextmanager
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence
from urllib.parse import quote
from urllib.error import URLError
from urllib.request import Request, urlopen

OPERATIONAL_ERROR = 2


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
    """Pytest status plus runs found while preparing the UI."""

    exit_code: int
    new_runs: tuple[StoredRun, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunURLs:
    history: str
    direct: str


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
        for candidate in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None))
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
        names = [(base / "python.exe")] if os.name == "nt" else [base / "bin" / "python"]
        names.append(base / "bin" / "python.exe" if os.name == "nt" else base / "python")
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
                    raise ProjectPythonError("the active project environment is unavailable")
                return candidate
    selected = _python_in_environment(root, env)
    if selected is not None:
        return selected
    if not fallback_to_system:
        raise ProjectPythonError("no project environment is configured; run mcp-pal setup")
    for name in ("python3", "python"):
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            return _absolute_path(found)
    raise ProjectPythonError("no project Python was found; pass --python PATH")


_VALIDATE_SCRIPT = r'''
import importlib
import importlib.metadata
import json

checks = {}
for name in ("pytest", "mcp_pal", "mcp_pal.pytest_plugin"):
    try:
        importlib.import_module(name)
    except Exception:
        checks[name] = False
    else:
        checks[name] = True
try:
    from mcp_pal.storage import SQLiteExecutionStore
except Exception:
    checks["SQLiteExecutionStore"] = False
else:
    checks["SQLiteExecutionStore"] = True
try:
    version = importlib.metadata.version("mcp-pal")
except Exception:
    version = None
print(json.dumps({"checks": checks, "version": version}, sort_keys=True))
'''


def _cli_sdk_version() -> str:
    try:
        return importlib.metadata.version("mcp-pal")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ProjectPythonError("the CLI SDK version is unavailable; reinstall mcp-pal-cli") from exc


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
        raise ProjectPythonError("the selected project Python could not be started") from None
    if result.returncode != 0:
        raise ProjectPythonError("the selected project Python could not be started")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError, TypeError):
        raise ProjectPythonError("the selected project Python returned an invalid check result") from None
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        raise ProjectPythonError("the selected project Python returned an invalid check result")
    missing = [name for name in ("pytest", "mcp_pal", "mcp_pal.pytest_plugin", "SQLiteExecutionStore") if not checks.get(name)]
    if missing:
        names = ", ".join(missing)
        raise ProjectPythonError(f"the project Python is missing required MCP Pal packages: {names}; install mcp-pal[pytest,storage] in the project")
    project_version = payload.get("version")
    expected = _cli_sdk_version() if cli_sdk_version is None else cli_sdk_version
    if not isinstance(project_version, str):
        raise ProjectPythonError("the project mcp-pal distribution version could not be determined")
    if project_version != expected:
        raise ProjectPythonError(
            f"project mcp-pal version {project_version} does not match CLI SDK version {expected}; install matching versions"
        )
    return project_version


def _absolute_database(value: str | os.PathLike[str] | None, *, project_root: Path | None = None) -> Path:
    root = (project_root or Path.cwd()).resolve()
    return (Path(value).expanduser() if value else root / ".mcp-pal" / "executions.sqlite").resolve()


_STORE_PAGE_SIZE = 100


def _execution_store_type() -> Any:
    """Load the optional SQLite store only when run discovery is requested."""

    from mcp_pal.storage import SQLiteExecutionStore

    return SQLiteExecutionStore


def list_stored_runs(database: Path) -> StoredRuns:
    """List all visible executions through the store's public paging API."""

    store: Any | None = None
    try:
        store = _execution_store_type()(database)
        runs: list[StoredRun] = []
        offset = 0
        while True:
            page = store.list_executions(limit=_STORE_PAGE_SIZE, offset=offset)
            items = tuple(page.items)
            runs.extend(
                StoredRun(
                    run_id=str(getattr(snapshot.execution_id, "root", snapshot.execution_id)),
                    created_at=snapshot.created_at,
                )
                for snapshot in items
            )
            offset += len(items)
            if not items or offset >= int(page.total):
                break
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


def find_new_runs(before: StoredRuns, after: StoredRuns) -> tuple[StoredRun, ...]:
    """Return after-runs whose IDs were absent before pytest started."""

    existing = {run.run_id for run in before.runs}
    unique: dict[str, StoredRun] = {}
    for run in after.runs:
        if run.run_id not in existing:
            unique[run.run_id] = run
    return tuple(sorted(unique.values(), key=lambda run: (run.created_at, run.run_id)))


def build_run_urls(run_id: str, port: int) -> RunURLs:
    """Build the history and encoded direct-run URLs for the local UI."""

    origin = f"http://127.0.0.1:{port}"
    encoded_run_id = quote(str(run_id), safe="")
    return RunURLs(
        history=f"{origin}/history",
        direct=f"{origin}/playground/run/{encoded_run_id}",
    )


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
        return "bundled MCP Pal UI assets are unavailable"
    return None


def _server_command(database: Path, port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "mcp_pal_cli.web",
        "--database-path",
        str(database),
        "--port",
        str(port),
    ]


class _ServerChild:
    def __init__(self, database: Path, port: int) -> None:
        kwargs: dict[str, Any] = {
            "env": _server_environment(),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
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
        self.process = subprocess.Popen(_server_command(database, port), **kwargs)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                self.lines.append(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass

    def alive(self) -> bool:
        return self.process.poll() is None


def _ready(url: str) -> bool:
    try:
        with urlopen(Request(url, method="GET"), timeout=0.5) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def _wait_ready(child: _ServerChild, url: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not child.alive():
            return False
        if _ready(url):
            return True
        time.sleep(0.05)
    return False


def _server_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Isolate the viewer from project test settings and credentials."""

    values = os.environ if source is None else source
    environment_links = {"VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT"}
    return {
        key: value
        for key, value in values.items()
        if key.upper() not in environment_links
        and not key.upper().startswith("MCP_PAL_")
        and not any(
            part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")
        )
    }


def _server_diagnostics(child: _ServerChild) -> tuple[str, ...]:
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
        for value in secret_values:
            line = line.replace(value, "<redacted>")
        line = re.sub(
            r"(?i)(\b(?:api[_-]?key|token|secret|password|credential)\b\s*(?:=|:)\s*)[^\s,;]+",
            r"\1<redacted>",
            line,
        )
        safe.append(line)
    return tuple(safe[-5:])


def pytest_command(python: Path, database: Path, pytest_args: Sequence[str]) -> list[str]:
    return [
        str(python),
        "-m",
        "pytest",
        "-p",
        "mcp_pal.pytest_plugin",
        "--mcp-pal-results-db",
        str(database),
        *pytest_args,
    ]


# Keep the implementation name easy to discover for callers that used the old
# supervisor's private command helper while making the Python argument explicit.
_pytest_command = pytest_command


def _terminate_process(process: Any) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
            process.send_signal(ctrl_break) if ctrl_break is not None else process.terminate()
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


def _run_pytest_process(python: Path, database: Path, pytest_args: Sequence[str]) -> int:
    """Run pytest with safe process-group cleanup and return its status."""

    process: subprocess.Popen[Any] | None = None
    try:
        with _termination_signal_handlers():
            kwargs: dict[str, Any] = {}
            if os.name == "posix":
                kwargs["start_new_session"] = True
            elif os.name == "nt":
                flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                if flags:
                    kwargs["creationflags"] = flags
            process = subprocess.Popen(pytest_command(python, database, pytest_args), **kwargs)
            try:
                raw_code = int(process.wait())
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
        print("mcp-pal test: pytest could not be started", file=sys.stderr)
        return OPERATIONAL_ERROR


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
        print(f"mcp-pal test: {exc}", file=sys.stderr)
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


def _print_ui_output(port: int, new_runs: Sequence[StoredRun], warnings: Sequence[str]) -> None:
    for warning in dict.fromkeys(warnings):
        print(f"Warning: {warning}", file=sys.stderr)
    history_url = build_run_urls("run", port).history
    print(f"MCP-Pal UI: {history_url}", flush=True)
    if not new_runs:
        print("No new stored runs.", flush=True)
        return
    for run in new_runs:
        print(f"Run: {build_run_urls(run.run_id, port).direct}", flush=True)


def _run_ui_server(
    database: Path,
    port: int,
    pytest_code: int,
    new_runs: Sequence[StoredRun],
    warnings: Sequence[str],
) -> int:
    child: _ServerChild | None = None
    try:
        child = _ServerChild(database, port)
        origin = f"http://127.0.0.1:{port}"
        if not _wait_ready(child, origin + "/api/v2/executions"):
            print("mcp-pal: UI server readiness failed", file=sys.stderr)
            for line in _server_diagnostics(child):
                print(f"mcp-pal: UI server: {line}", file=sys.stderr)
            return OPERATIONAL_ERROR
        _print_ui_output(port, new_runs, warnings)
        with _termination_signal_handlers():
            while True:
                if not child.alive():
                    print("mcp-pal: UI server stopped unexpectedly", file=sys.stderr)
                    return OPERATIONAL_ERROR
                time.sleep(0.1)
    except KeyboardInterrupt:
        return pytest_code
    except _TerminationSignal as exc:
        return 128 + exc.signum
    except (OSError, ValueError):
        print("mcp-pal: UI server could not be started", file=sys.stderr)
        return OPERATIONAL_ERROR
    finally:
        _stop_server(child)


def run_test_with_runs(
    *,
    python: str | os.PathLike[str] | None = None,
    pytest_args: Sequence[str] = (),
    database: str | os.PathLike[str] | None = None,
    ui: bool = False,
    port: int = 8000,
    ui_dir: str | os.PathLike[str] | None = None,
    project_root: Path | None = None,
) -> TestRunResult:
    """Run pytest and retain newly stored runs for optional UI serving."""

    if ui:
        port_error = _validate_port(port)
        if port_error is not None:
            print(f"mcp-pal test: UI startup failed ({port_error})", file=sys.stderr)
            return TestRunResult(OPERATIONAL_ERROR)
        ui_error = _ui_prerequisite_error(ui_dir)
        if ui_error is not None:
            print(f"mcp-pal test: {ui_error}", file=sys.stderr)
            return TestRunResult(OPERATIONAL_ERROR)
    root = (project_root or Path.cwd()).resolve()
    database_path = _absolute_database(database, project_root=root)
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        print("mcp-pal test: results database directory could not be created", file=sys.stderr)
        return TestRunResult(OPERATIONAL_ERROR)

    before = list_stored_runs(database_path)
    prepared = _prepare_test(python, database, root)
    if prepared is None:
        return TestRunResult(OPERATIONAL_ERROR, warnings=tuple(filter(None, (before.warning,))))
    selected, database_path = prepared

    exit_code = _run_pytest_process(selected, database_path, pytest_args)
    after = list_stored_runs(database_path)
    warnings = tuple(dict.fromkeys(
        warning for warning in (before.warning, after.warning) if warning is not None
    ))
    new_runs = find_new_runs(before, after)
    if not ui:
        return TestRunResult(exit_code, new_runs, warnings)
    if exit_code in {2, 130} or 128 <= exit_code < 256:
        return TestRunResult(exit_code, new_runs, warnings)
    server_code = _run_ui_server(database_path, port, exit_code, new_runs, warnings)
    return TestRunResult(server_code, new_runs, warnings)


def run_test(
    *,
    python: str | os.PathLike[str] | None = None,
    pytest_args: Sequence[str] = (),
    database: str | os.PathLike[str] | None = None,
    ui: bool = False,
    port: int = 8000,
    ui_dir: str | os.PathLike[str] | None = None,
    project_root: Path | None = None,
) -> int:
    """Run pytest and return its exact exit status."""

    if ui:
        return run_test_with_runs(
            python=python,
            pytest_args=pytest_args,
            database=database,
            ui=ui,
            port=port,
            ui_dir=ui_dir,
            project_root=project_root,
        ).exit_code
    root = (project_root or Path.cwd()).resolve()
    prepared = _prepare_test(python, database, root)
    if prepared is None:
        return OPERATIONAL_ERROR
    selected, database_path = prepared
    return _run_pytest_process(selected, database_path, pytest_args)


__all__ = [
    "OPERATIONAL_ERROR",
    "ProjectPythonError",
    "resolve_project_python",
    "validate_project_python",
    "pytest_command",
    "StoredRun",
    "StoredRuns",
    "TestRunResult",
    "RunURLs",
    "list_stored_runs",
    "find_new_runs",
    "build_run_urls",
    "run_test_with_runs",
    "run_test",
]
