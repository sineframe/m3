"""Run the production CLI through an isolated, two-environment smoke test.

This gate is intentionally a plain Python script.  It is run against the
three wheels produced by the release build and never installs the checkout as
an editable package.  The CLI tool environment and the project test
environment are separate on purpose: the former owns the command and web app,
while the latter owns pytest and the SDK test code.
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
import zipfile


class StandaloneGateError(RuntimeError):
    """A bounded, user-facing standalone gate failure."""


_MAX_DIAGNOSTICS = 6_000
_PROCESS_TIMEOUT = 180
_UI_TIMEOUT = 120
_SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_REMOVED_ENV_NAMES = frozenset(
    {"VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT"}
)


def _tail(value: str, limit: int = _MAX_DIAGNOSTICS) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[-limit:]


def _clean_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep ordinary process settings while excluding credentials and env links."""

    values = os.environ if source is None else source
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        upper = key.upper()
        if upper in _REMOVED_ENV_NAMES or any(part in upper for part in _SECRET_NAME_PARTS):
            continue
        cleaned[key] = value
    return cleaned


def _redact_diagnostics(value: str, env: Mapping[str, str]) -> str:
    """Remove values that could be credentials before showing child output."""

    redacted = value
    for key, candidate in env.items():
        if candidate and any(part in key.upper() for part in _SECRET_NAME_PARTS):
            redacted = redacted.replace(candidate, "<redacted>")
    return re.sub(
        r"(?i)(\b(?:api[_-]?key|token|secret|password|credential)\b\s*(?:=|:)\s*)[^\s,;]+",
        r"\1<redacted>",
        redacted,
    )


def _safe_diagnostics(value: str, env: Mapping[str, str]) -> str:
    return _tail(_redact_diagnostics(value, env))


def _display(command: list[str]) -> str:
    return " ".join(command)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = _PROCESS_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    print("+", _display(command), flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise StandaloneGateError(f"command timed out: {command[0]}") from exc
    except OSError as exc:
        raise StandaloneGateError(f"could not start {command[0]}") from exc
    if result.returncode != 0:
        diagnostics = _safe_diagnostics(result.stdout + "\n" + result.stderr, env)
        detail = f"\n{diagnostics}" if diagnostics else ""
        raise StandaloneGateError(
            f"command failed with exit {result.returncode}: {command[0]}{detail}"
        )
    return result


def _python_path(venv: Path) -> Path:
    directory = venv / ("Scripts" if os.name == "nt" else "bin")
    name = "python.exe" if os.name == "nt" else "python"
    path = directory / name
    if not path.is_file():
        raise StandaloneGateError(f"virtualenv Python is missing: {path}")
    return path


def _command_path(directory: Path, name: str) -> Path:
    return directory / ("Scripts" if os.name == "nt" else "bin") / (
        f"{name}.exe" if os.name == "nt" else name
    )


def wheel_paths(release_dir: str | os.PathLike[str], version: str) -> tuple[Path, Path, Path]:
    """Return exactly the CLI, SDK, and app wheels for ``version``."""

    release = Path(release_dir).expanduser().resolve()
    if not release.is_dir():
        raise StandaloneGateError("release directory is unavailable")
    expected = tuple(
        release / f"{prefix}-{version}-py3-none-any.whl"
        for prefix in ("mcp_pal_cli", "mcp_pal", "mcp_pal_app")
    )
    if any(not path.is_file() for path in expected) or len(tuple(release.glob("*.whl"))) != 3:
        raise StandaloneGateError("release directory must contain exactly the three versioned wheels")
    if len(set(expected)) != 3:
        raise StandaloneGateError("release wheel names are not unique")
    return expected


def assert_bundled_ui(wheel: Path) -> None:
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise StandaloneGateError("CLI wheel is not a readable wheel archive") from exc
    if "mcp_pal_cli/ui/index.html" not in names or not any(
        name.startswith("mcp_pal_cli/ui/assets/") and not name.endswith("/")
        for name in names
    ):
        raise StandaloneGateError("CLI wheel does not contain the bundled production UI")


def _tool_probe(tool_python: Path, expected_version: str, env: Mapping[str, str]) -> None:
    code = r'''
import importlib.metadata as metadata
import importlib.util
import json
from importlib import resources

required = {
    name: importlib.util.find_spec(name) is not None
    for name in ("mcp_pal_cli", "mcp_pal", "mcp_pal_app")
}
forbidden = {
    name: importlib.util.find_spec(name) is None
    for name in ("pytest", "streamlit", "requests")
}
ui = resources.files("mcp_pal_cli").joinpath("ui")
payload = {
    "required": required,
    "forbidden": forbidden,
    "version": metadata.version("mcp-pal"),
    "ui": ui.joinpath("index.html").is_file() and any(item.is_file() for item in ui.joinpath("assets").iterdir()),
}
print(json.dumps(payload, sort_keys=True))
'''
    result = _run([str(tool_python), "-c", code], cwd=tool_python.parent, env=dict(env))
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise StandaloneGateError("CLI tool environment returned an invalid package check") from exc
    if payload.get("required") != {"mcp_pal_cli": True, "mcp_pal": True, "mcp_pal_app": True}:
        raise StandaloneGateError("CLI tool environment is missing a required MCP Pal package")
    if payload.get("forbidden") != {"pytest": True, "streamlit": True, "requests": True}:
        raise StandaloneGateError("CLI tool environment contains a project-only package")
    if payload.get("version") != expected_version or payload.get("ui") is not True:
        raise StandaloneGateError("CLI tool environment has the wrong version or no bundled UI")


def _project_probe(
    project_python: Path,
    cwd: Path,
    expected_version: str,
    env: Mapping[str, str],
) -> None:
    code = r'''
import importlib.metadata as metadata
import importlib.util
import json

required = {}
for name in ("pytest", "mcp_pal", "mcp_pal.pytest_plugin"):
    required[name] = importlib.util.find_spec(name) is not None
try:
    from mcp_pal.storage import SQLiteExecutionStore
except Exception:
    required["SQLiteExecutionStore"] = False
else:
    required["SQLiteExecutionStore"] = True
forbidden = {name: importlib.util.find_spec(name) is None for name in ("mcp_pal_cli", "mcp_pal_app")}
print(json.dumps({"required": required, "forbidden": forbidden, "version": metadata.version("mcp-pal")}, sort_keys=True))
'''
    result = _run([str(project_python), "-c", code], cwd=cwd, env=dict(env))
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise StandaloneGateError("project environment returned an invalid package check") from exc
    required = payload.get("required")
    if not isinstance(required, dict) or not all(required.get(name) for name in (
        "pytest", "mcp_pal", "mcp_pal.pytest_plugin", "SQLiteExecutionStore"
    )):
        raise StandaloneGateError("project environment is missing pytest, SDK, plugin, or SQLite storage")
    if payload.get("forbidden") != {"mcp_pal_cli": True, "mcp_pal_app": True}:
        raise StandaloneGateError("project environment contains the standalone CLI or app")
    if payload.get("version") != expected_version:
        raise StandaloneGateError("project SDK version does not match the CLI release")


def _write_dummy_test(repo: Path) -> None:
    tests = repo / "tests"
    tests.mkdir(parents=True)
    (tests / "test_public_sdk.py").write_text(
        '''from mcp_pal import MCPTestKit
from mcp_pal.testing import FaultInjector
from mcp_pal.types import DirectExecutionSpec, PingOperation, ServerBinding


def test_stored_public_sdk_run() -> None:
    # In-process registrations are intentionally not serializable execution
    # specs. The public SDK's deterministic stdio fixture gives kit.run a
    # portable spec while still exercising a real MCP process boundary.
    server = FaultInjector().stdio_server(name="standalone-gate")
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=server),),
        operation=PingOperation(),
    )
    with MCPTestKit() as kit:
        result = kit.run(spec)
    assert result.snapshot.outcome.value == "completed"
''',
        encoding="utf-8",
    )


def _stored_run_ids(
    project_python: Path,
    repo: Path,
    database: Path,
    env: Mapping[str, str],
) -> list[str]:
    code = r'''
import json
import sys
from mcp_pal.storage import SQLiteExecutionStore

store = SQLiteExecutionStore(sys.argv[1])
try:
    page = store.list_executions(limit=100, offset=0)
    print(json.dumps([str(item.execution_id.root) for item in page.items]))
finally:
    store.close()
'''
    result = _run([str(project_python), "-c", code, str(database)], cwd=repo, env=dict(env))
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise StandaloneGateError("project store query returned invalid JSON") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StandaloneGateError("project store query returned invalid run IDs")
    return value


class _ManagedOutput:
    """Capture a child without allowing unbounded output or pipe deadlocks."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.lines: deque[str] = deque(maxlen=600)
        self._lock = threading.Lock()
        self.done = threading.Event()
        thread = threading.Thread(target=self._read, daemon=True)
        thread.start()

    def _read(self) -> None:
        stream = self.process.stdout
        if stream is None:
            self.done.set()
            return
        try:
            for line in stream:
                with self._lock:
                    self.lines.append(line.rstrip("\r\n")[:_MAX_DIAGNOSTICS])
        except (OSError, ValueError):
            pass
        finally:
            self.done.set()

    def text(self) -> str:
        with self._lock:
            return "\n".join(self.lines)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _parse_ui_links(output: str, origin: str, expected_id: str) -> str:
    """Validate CLI link output and return the direct run URL."""

    history_matches = re.findall(r"(?m)^MCP-Pal UI: (https?://[^\s]+)$", output)
    expected_history = f"{origin}/history"
    if len(history_matches) != 1 or history_matches[0] != expected_history:
        raise StandaloneGateError("CLI UI history link does not match the selected loopback origin")
    direct_matches = re.findall(r"(?m)^Run: (https?://[^\s]+)$", output)
    if not direct_matches:
        raise StandaloneGateError("CLI UI process did not print a direct run link")
    direct_url = direct_matches[0]
    selected = urlsplit(origin)
    parsed = urlsplit(direct_url)
    try:
        effective_port = parsed.port
    except ValueError as exc:
        raise StandaloneGateError("CLI direct link has an invalid port") from exc
    if (
        parsed.scheme != selected.scheme
        or parsed.hostname != selected.hostname
        or effective_port != selected.port
        or parsed.query
        or parsed.fragment
    ):
        raise StandaloneGateError("CLI direct link does not match the selected loopback origin")
    prefix = "/playground/run/"
    if not parsed.path.startswith(prefix):
        raise StandaloneGateError("CLI direct link does not use the playground run route")
    encoded_suffix = parsed.path[len(prefix):]
    if not encoded_suffix or unquote(encoded_suffix) != expected_id:
        raise StandaloneGateError("CLI direct link does not identify the newly stored run")
    return direct_url


def _http(url: str) -> tuple[int, str, bytes]:
    try:
        with urlopen(Request(url, method="GET"), timeout=5) as response:
            body = response.read(2_000_000)
            return response.status, response.headers.get("Content-Type", ""), body
    except HTTPError as exc:
        body = exc.read(2_000_000)
        return exc.code, exc.headers.get("Content-Type", ""), body
    except (OSError, URLError) as exc:
        raise StandaloneGateError("could not query the local UI server") from exc


def _assert_json_execution(body: bytes, expected_id: str) -> None:
    try:
        payload: Any = json.loads(body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StandaloneGateError("the executions endpoint returned HTML or invalid JSON") from exc

    def contains(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                (key in {"execution_id", "executionId", "id"} and str(item) == expected_id)
                or contains(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(contains(item) for item in value)
        return False

    if not contains(payload):
        raise StandaloneGateError("the executions API does not contain the newly stored run")


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.kill(process.pid, signal.SIGINT)
        else:
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)
            process.send_signal(ctrl_break)
        process.wait(timeout=15)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _run_ui_gate(
    executable: Path,
    project_python: Path,
    repo: Path,
    database: Path,
    port: int,
    env: dict[str, str],
    existing_id: str,
) -> None:
    command = [
        str(executable), "test", "--ui", "--python", str(project_python),
        "--results-db", str(database), "--port", str(port), "--", "-q",
    ]
    print("+", _display(command), flush=True)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(repo),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=(os.name == "posix"),
            creationflags=(int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0),
        )
    except OSError as exc:
        raise StandaloneGateError("could not start the CLI UI process") from exc
    output = _ManagedOutput(process)
    deadline = time.monotonic() + _UI_TIMEOUT
    while time.monotonic() < deadline:
        text = output.text()
        if "MCP-Pal UI:" in text and re.search(r"(?m)^Run: http://127\.0\.0\.1:[0-9]+/playground/run/", text):
            break
        if process.poll() is not None:
            break
        time.sleep(0.1)
    else:
        _terminate(process)
        raise StandaloneGateError("CLI UI process did not print its history and run links")

    text = output.text()
    if "MCP-Pal UI:" not in text:
        _terminate(process)
        raise StandaloneGateError(
            f"CLI UI process exited before printing links\n{_safe_diagnostics(text, env)}"
        )
    current_runs = _stored_run_ids(project_python, repo, database, env)
    new_runs = [run_id for run_id in current_runs if run_id != existing_id]
    if len(current_runs) != 2 or len(new_runs) != 1:
        _terminate(process)
        raise StandaloneGateError("UI test did not create exactly one additional stored run")
    expected_id = new_runs[0]
    origin = f"http://127.0.0.1:{port}"
    try:
        direct_url = _parse_ui_links(text, origin, expected_id)
    except StandaloneGateError:
        _terminate(process)
        raise
    status, content_type, body = _http(origin + "/api/v2/executions")
    if status != 200 or "json" not in content_type.lower():
        _terminate(process)
        raise StandaloneGateError("executions API did not return JSON")
    _assert_json_execution(body, expected_id)
    status, content_type, body = _http(origin + "/history")
    if status != 200 or "html" not in content_type.lower() or b"<html" not in body.lower():
        _terminate(process)
        raise StandaloneGateError("history route did not return the bundled SPA")
    status, content_type, body = _http(direct_url)
    if status != 200 or "html" not in content_type.lower() or b"<html" not in body.lower():
        _terminate(process)
        raise StandaloneGateError("direct run route did not return the bundled SPA")
    if re.search(r"(?i)\b(?:node|npm|vite)\b", text):
        _terminate(process)
        raise StandaloneGateError("CLI UI process attempted to use a Node/Vite runtime")

    _terminate(process)
    if process.returncode != 0:
        raise StandaloneGateError(
            f"CLI UI process did not preserve pytest success (exit {process.returncode})\n"
            f"{_safe_diagnostics(output.text(), env)}"
        )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                pass
        except OSError:
            return
        time.sleep(0.1)
    raise StandaloneGateError("CLI UI port remained open after Ctrl-C cleanup")


def check(release_dir: str | os.PathLike[str], version: str) -> None:
    cli_wheel, sdk_wheel, app_wheel = wheel_paths(release_dir, version)
    assert_bundled_ui(cli_wheel)
    uv = shutil.which("uv")
    if uv is None:
        raise StandaloneGateError("uv is required for the standalone gate")

    with tempfile.TemporaryDirectory(prefix="mcp-pal-cli-standalone-") as temporary:
        # macOS commonly exposes the temporary directory through /var ->
        # /private/var. SQLite intentionally rejects symlinked database paths,
        # so use the physical temporary path throughout the gate.
        root = Path(temporary).resolve()
        repo = root / "dummy-repo"
        repo.mkdir()
        tool_dir = root / "uv-tools"
        tool_bin = root / "uv-bin"
        cache_dir = root / "uv-cache"
        project_venv = repo / ".venv"
        tool_bin.mkdir()
        env = _clean_environment()
        env.update({
            "UV_TOOL_DIR": str(tool_dir),
            "UV_TOOL_BIN_DIR": str(tool_bin),
            "UV_CACHE_DIR": str(cache_dir),
            "PATH": str(tool_bin) + os.pathsep + env.get("PATH", ""),
        })
        # The dummy repository is the only working directory used for test
        # commands.  No command below can see or modify the source checkout.
        _write_dummy_test(repo)
        _run(
            [uv, "tool", "install", "--force", str(cli_wheel), "--with", str(sdk_wheel), "--with", str(app_wheel)],
            cwd=repo,
            env=env,
        )
        executable = tool_bin / ("mcp-pal.exe" if os.name == "nt" else "mcp-pal")
        if not executable.is_file():
            raise StandaloneGateError("uv did not create the standalone mcp-pal command")
        tool_python = _command_path(tool_dir / "mcp-pal-cli", "python")
        if not tool_python.is_file():
            raise StandaloneGateError("uv tool environment Python is missing")
        _tool_probe(tool_python, version, env)

        _run([uv, "venv", "--python", sys.executable, str(project_venv)], cwd=repo, env=env)
        project_python = _python_path(project_venv)
        # Put extras on the package name in a PEP 508 direct reference so the
        # requirement remains portable across uv and pip.
        sdk_requirement = f"mcp-pal[pytest,storage] @ {sdk_wheel.as_uri()}"
        _run([uv, "pip", "install", "--python", str(project_python), sdk_requirement], cwd=repo, env=env)
        _project_probe(project_python, repo, version, env)
        database = repo / ".mcp-pal" / "executions.sqlite"
        _run([str(executable), "doctor", "--python", str(project_python), "--project-root", str(repo), "--require", "storage:sqlite"], cwd=repo, env=env)
        _run([str(executable), "test", "--python", str(project_python), "--results-db", str(database), "--", "-q"], cwd=repo, env=env)
        first_runs = _stored_run_ids(project_python, repo, database, env)
        if len(first_runs) != 1:
            raise StandaloneGateError(f"expected exactly one stored run after plain test, found {len(first_runs)}")
        port = _free_port()
        _run_ui_gate(executable, project_python, repo, database, port, env, first_runs[0])
        final_runs = _stored_run_ids(project_python, repo, database, env)
        if len(final_runs) != 2 or first_runs[0] not in final_runs:
            raise StandaloneGateError("UI test did not preserve exactly two stored runs")
        print("isolated CLI standalone gate passed", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        check(args.release_dir, args.version)
    except StandaloneGateError as exc:
        print(f"CLI standalone gate failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
