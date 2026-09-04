"""Pytest/UI process supervisor for the local history viewer."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from importlib.util import find_spec as _find_spec
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

OPERATIONAL_ERROR = 2


class _TerminationSignal(BaseException):
    """Internal control flow used to unwind through child cleanup."""

    def __init__(self, signum: int) -> None:
        self.signum = signum


@contextmanager
def _termination_signal_handlers() -> Any:
    """Turn POSIX termination signals into a scoped, cleanup-safe unwind."""

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


def _module_available(name: str) -> bool:
    try:
        return _find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _test_prerequisite_error() -> str | None:
    if not _module_available("pytest"):
        return "pytest is unavailable; install mcp-pal[pytest,storage]"
    if not _module_available("sqlalchemy"):
        return "SQLite storage is unavailable; install mcp-pal[pytest,storage]"
    return None


def _viewer_prerequisite_error(ui_dir: str | None) -> str | None:
    root = Path.cwd()
    source_app = root / "app" / "src" / "mcp_pal_app" / "main.py"
    if not _module_available("mcp_pal_app") and not source_app.is_file():
        return "API application is unavailable; run from the mcp-pal repository root"
    missing = [name for name in ("fastapi", "pydantic_settings") if not _module_available(name)]
    if missing:
        return f"API runtime is unavailable (missing {', '.join(missing)})"
    selected_ui = (Path(ui_dir).expanduser() if ui_dir else root.parent / "mcppal-ui").resolve()
    if not selected_ui.is_dir() or not (selected_ui / "package.json").is_file():
        return "UI directory or package.json is unavailable; pass --ui-dir"
    if shutil.which("npm") is None:
        return "npm is unavailable"
    return None


def _print_prerequisite_error(message: str) -> None:
    print(f"mcp-pal test: {message}", file=sys.stderr)


def _absolute_database(value: str | None) -> Path:
    return (Path(value).expanduser() if value else Path.cwd() / ".mcp-pal" / "executions.sqlite").resolve()


def _pytest_command(database: Path, pytest_args: Sequence[str]) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "mcp_pal.pytest_plugin",
        "--mcp-pal-results-db",
        str(database),
        *pytest_args,
    ]


def _api_command(port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "mcp_pal_app.viewer:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]


def _validate_ports(api_port: int, ui_port: int) -> str | None:
    if not (1 <= api_port <= 65535 and 1 <= ui_port <= 65535):
        return "ports must be between 1 and 65535"
    if api_port == ui_port:
        return "API and UI ports must be distinct"
    probes: list[socket.socket] = []
    try:
        for port in (api_port, ui_port):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probes.append(probe)
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    except OSError:
        return "API or UI port is already in use"
    finally:
        for probe in probes:
            probe.close()
    return None


def run_test(
    *,
    pytest_args: Sequence[str] = (),
    database: str | None = None,
    ui: bool = False,
    api_port: int = 8000,
    ui_port: int = 4173,
    ui_dir: str | None = None,
) -> int:
    """Run pytest and, when requested, supervise the local history viewer."""

    database_path = _absolute_database(database)
    prerequisite_error = _test_prerequisite_error()
    if prerequisite_error is not None:
        _print_prerequisite_error(prerequisite_error)
        return OPERATIONAL_ERROR
    if ui:
        prerequisite_error = _viewer_prerequisite_error(ui_dir)
        if prerequisite_error is not None:
            _print_prerequisite_error(prerequisite_error)
            return OPERATIONAL_ERROR
    process: subprocess.Popen[Any] | None = None
    try:
        with _termination_signal_handlers():
            try:
                kwargs: dict[str, Any] = {}
                if os.name == "posix":
                    kwargs["start_new_session"] = True
                elif os.name == "nt":
                    flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                    if flags:
                        kwargs["creationflags"] = flags
                process = subprocess.Popen(_pytest_command(database_path, pytest_args), **kwargs)
                try:
                    raw_code = int(process.wait())
                    interrupted = raw_code < 0
                    pytest_code = 128 + (-raw_code) if interrupted else raw_code
                except KeyboardInterrupt:
                    _terminate_process(process)
                    return 130
            except (OSError, ValueError):
                if process is not None and process.poll() is None:
                    _terminate_process(process)
                return OPERATIONAL_ERROR

            # Pytest's canonical interrupted status is 2. Do not open a viewer for an
            # incomplete collection or a process interrupted by a signal.
            if not ui or interrupted or pytest_code in {2, 130}:
                return pytest_code
            return _run_viewer(database_path, pytest_code, api_port, ui_port, ui_dir)
    except _TerminationSignal as exc:
        if process is not None and process.poll() is None:
            _terminate_process(process)
        return 128 + exc.signum


class _Child:
    def __init__(
        self,
        name: str,
        command: Sequence[str],
        env: dict[str, str],
        cwd: Path | None = None,
    ) -> None:
        self.name = name
        self._env = env
        self.lines: deque[str] = deque(maxlen=50)
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
            "env": env,
        }
        if cwd is not None:
            kwargs["cwd"] = str(cwd)
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif os.name == "nt":
            flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            if flags:
                kwargs["creationflags"] = flags
        self.process = subprocess.Popen(list(command), **kwargs)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                clean = _redact_line(line.rstrip("\r\n"), self._env)
                self.lines.append(clean)
                print(f"[{self.name}] {clean}", flush=True)
        except (OSError, ValueError):
            pass

    def alive(self) -> bool:
        return self.process.poll() is None


def _redact_line(line: str, env: dict[str, str]) -> str:
    for key, value in env.items():
        if value and any(label in key for label in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and len(value) >= 4:
            line = line.replace(value, "[REDACTED]")
    return line


def _api_child_environment(environment: dict[str, str]) -> dict[str, str]:
    """Return the minimum non-secret environment needed by the history API."""

    allowed = {
        "DATABASE_PATH",
        "PYTHONPATH",
        "PATH",
        "HOME",
        "USER",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "LC_ALL",
        "TZ",
        "MCP_PAL_ARTIFACT_POLICY",
        "MCP_PAL_PROTOCOL_REVISION",
        "MCP_PAL_TELEMETRY_ENABLED",
    }
    return {key: value for key, value in environment.items() if key in allowed}


def _windows_kill_tree(process: Any) -> None:
    """Force-stop a Windows process tree without invoking a shell."""

    try:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass


def _terminate_process(process: Any) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
            if ctrl_break is None:
                process.terminate()
            else:
                process.send_signal(ctrl_break)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=3)
    except (subprocess.TimeoutExpired, OSError):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                _windows_kill_tree(process)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _terminate(child: _Child | None) -> None:
    if child is None:
        return
    process = child.process
    if process.poll() is not None:
        try:
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    _terminate_process(process)


def _ready(url: str) -> bool:
    try:
        with urlopen(Request(url, method="GET"), timeout=0.5) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def _wait_ready(child: _Child, url: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not child.alive():
            return False
        if _ready(url):
            return True
        time.sleep(0.05)
    return False


def _startup_failure(name: str, reason: str, child: _Child | None) -> None:
    print(f"mcp-pal: {name} startup failed ({reason})", file=sys.stderr)
    if child is not None and child.lines:
        print(f"mcp-pal: last {len(child.lines)} {name} log lines:", file=sys.stderr)
        for line in child.lines:
            print(f"[{name}] {line}", file=sys.stderr)


def _run_viewer(
    database: Path,
    pytest_code: int,
    api_port: int,
    ui_port: int,
    ui_dir: str | None,
) -> int:
    api: _Child | None = None
    vite: _Child | None = None
    root = Path.cwd()
    selected_ui = (Path(ui_dir).expanduser() if ui_dir else root.parent / "mcppal-ui").resolve()
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root / "sdk" / "src"), str(root / "app" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    try:
        port_error = _validate_ports(api_port, ui_port)
        if port_error is not None:
            print(f"mcp-pal: viewer startup failed ({port_error})", file=sys.stderr)
            return OPERATIONAL_ERROR
        api = _Child(
            "api",
            _api_command(api_port),
            _api_child_environment(env),
        )
        api_origin = f"http://127.0.0.1:{api_port}"
        if not _wait_ready(api, api_origin + "/api/v2/executions"):
            _startup_failure("api", "readiness timeout or process exited", api)
            return OPERATIONAL_ERROR
        if not selected_ui.is_dir() or not (selected_ui / "package.json").is_file():
            _startup_failure("ui", "directory or package.json unavailable", None)
            return OPERATIONAL_ERROR
        ui_env = {
            key: value
            for key, value in env.items()
            if key in {"PATH", "HOME", "USER", "TMPDIR", "SystemRoot", "TEMP", "TMP"}
        }
        ui_env["VITE_API_PROXY_TARGET"] = api_origin
        vite = _Child(
            "ui",
            ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(ui_port), "--strictPort"],
            ui_env,
            selected_ui,
        )
        ui_origin = f"http://127.0.0.1:{ui_port}"
        if not _wait_ready(vite, ui_origin + "/history"):
            _startup_failure("ui", "readiness timeout or process exited", vite)
            return OPERATIONAL_ERROR
        print(f"mcp-pal history: {ui_origin}/history", flush=True)
        while True:
            if not api.alive():
                _startup_failure("api", "process exited after readiness", api)
                return OPERATIONAL_ERROR
            if not vite.alive():
                _startup_failure("ui", "process exited after readiness", vite)
                return OPERATIONAL_ERROR
            time.sleep(0.25)
    except KeyboardInterrupt:
        return pytest_code
    except (OSError, ValueError):
        return OPERATIONAL_ERROR
    finally:
        _terminate(vite)
        _terminate(api)


__all__ = ["run_test"]
