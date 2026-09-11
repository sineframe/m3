"""Run the manual live OpenCode and bundled-UI merge gate.

This gate performs one paid provider call. It installs the production CLI
wheel into an isolated uv tool environment and runs the selected real pytest
target in a separate project virtual environment. The checkout is never
installed as an editable package.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT.parent / "mcppal-ui"
TARGET = "sdk/examples/tests/test_live_opencode.py::test_live_opencode_uses_shipping_quote_and_captures_wire_evidence"
DEFAULT_MODEL = "opencode/big-pickle"
READY_TIMEOUT = 180.0
PLAYWRIGHT_TIMEOUT = 120.0
PROCESS_TIMEOUT = 300.0
MAX_DIAGNOSTICS = 6_000
SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
REMOVED_ENV_NAMES = frozenset(
    {"VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT"}
)


class GateFailure(RuntimeError):
    """A bounded, user-facing live-gate failure."""


def _tail(value: str, limit: int = MAX_DIAGNOSTICS) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[-limit:]


def clean_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Remove credentials and environment links before starting a child."""

    values = os.environ if source is None else source
    return {
        key: value
        for key, value in values.items()
        if key.upper() not in REMOVED_ENV_NAMES
        and not any(part in key.upper() for part in SECRET_NAME_PARTS)
    }


def redact(value: str, env: Mapping[str, str] | None = None) -> str:
    """Redact secret-like values and common credential assignments."""

    source = os.environ if env is None else env
    redacted = value
    for key, candidate in source.items():
        if candidate and any(part in key.upper() for part in SECRET_NAME_PARTS):
            redacted = redacted.replace(candidate, "<redacted>")
    return re.sub(
        r"(?i)(\b(?:api[_-]?key|token|secret|password|credential)\b\s*(?:=|:)\s*)[^\s,;]+",
        r"\1<redacted>",
        redacted,
    )


def safe_diagnostics(value: str, env: Mapping[str, str]) -> str:
    return _tail(redact(value, env))


def wheel_paths(
    release_dir: str | os.PathLike[str], version: str
) -> tuple[Path, Path, Path]:
    """Return the production CLI, SDK, and app wheels for one version."""

    release = Path(release_dir).expanduser().resolve()
    if not release.is_dir():
        raise GateFailure("release directory is unavailable")
    paths = tuple(
        release / f"{prefix}-{version}-py3-none-any.whl"
        for prefix in ("mcp_pal_cli", "mcp_pal", "mcp_pal_app")
    )
    if any(not path.is_file() for path in paths):
        raise GateFailure(
            "release directory is missing one of the three production wheels"
        )
    return paths


def assert_bundled_ui(wheel: Path) -> None:
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise GateFailure("CLI wheel is not a readable wheel archive") from exc
    if "mcp_pal_cli/ui/index.html" not in names or not any(
        name.startswith("mcp_pal_cli/ui/assets/") and not name.endswith("/")
        for name in names
    ):
        raise GateFailure("CLI wheel does not contain the bundled production UI")


def execution_report_url(origin: str, run_id: str) -> str:
    """Build the v2 report URL without treating run IDs as path syntax."""

    return f"{origin}/api/v2/executions/{quote(str(run_id), safe='')}/report"


def _display(command: list[str]) -> str:
    return " ".join(command)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float = PROCESS_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    print("+", _display(command), flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateFailure(f"command timed out: {command[0]}") from exc
    except OSError as exc:
        raise GateFailure(f"could not start {command[0]}") from exc
    if result.returncode != 0:
        detail = safe_diagnostics(result.stdout + "\n" + result.stderr, env)
        suffix = f"\n{detail}" if detail else ""
        raise GateFailure(
            f"command failed with exit {result.returncode}: {command[0]}{suffix}"
        )
    return result


def _python_path(venv: Path) -> Path:
    directory = venv / ("Scripts" if os.name == "nt" else "bin")
    name = "python.exe" if os.name == "nt" else "python"
    path = directory / name
    if not path.is_file():
        raise GateFailure(f"virtualenv Python is missing: {path}")
    return path


def _tool_command(tool_bin: Path) -> Path:
    path = tool_bin / ("mcp-pal.exe" if os.name == "nt" else "mcp-pal")
    if not path.is_file():
        raise GateFailure("uv did not create the standalone mcp-pal command")
    return path


def _tool_python(tool_dir: Path) -> Path:
    directory = tool_dir / "mcp-pal-cli" / ("Scripts" if os.name == "nt" else "bin")
    path = directory / ("python.exe" if os.name == "nt" else "python")
    if not path.is_file():
        raise GateFailure("uv tool environment Python is missing")
    return path


def _probe_tool(
    tool_python: Path, version: str, env: Mapping[str, str], cwd: Path
) -> None:
    code = r"""
import importlib.metadata as metadata
import importlib.util
import json
from importlib import resources

required = {name: importlib.util.find_spec(name) is not None for name in ("mcp_pal_cli", "mcp_pal", "mcp_pal_app")}
forbidden = {name: importlib.util.find_spec(name) is None for name in ("pytest", "streamlit", "requests")}
ui = resources.files("mcp_pal_cli").joinpath("ui")
print(json.dumps({"required": required, "forbidden": forbidden, "version": metadata.version("mcp-pal-cli"), "ui": ui.joinpath("index.html").is_file() and any(item.is_file() for item in ui.joinpath("assets").iterdir())}, sort_keys=True))
"""
    result = _run([str(tool_python), "-c", code], cwd=cwd, env=env)
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise GateFailure(
            "CLI tool environment returned an invalid package check"
        ) from exc
    if payload.get("required") != {
        "mcp_pal_cli": True,
        "mcp_pal": True,
        "mcp_pal_app": True,
    }:
        raise GateFailure(
            "CLI tool environment is missing a production MCP Pal package"
        )
    if payload.get("forbidden") != {
        "pytest": True,
        "streamlit": True,
        "requests": True,
    }:
        raise GateFailure("CLI tool environment contains project-only packages")
    if payload.get("version") != version or payload.get("ui") is not True:
        raise GateFailure("CLI tool environment has the wrong version or no bundled UI")


def _probe_project(
    project_python: Path, version: str, env: Mapping[str, str], cwd: Path
) -> None:
    code = r"""
import importlib.metadata as metadata
import importlib.util
import json

required = {name: importlib.util.find_spec(name) is not None for name in ("pytest", "mcp_pal", "mcp_pal.pytest_plugin")}
try:
    from mcp_pal.storage import SQLiteExecutionStore
except Exception:
    required["SQLiteExecutionStore"] = False
else:
    required["SQLiteExecutionStore"] = True
forbidden = {name: importlib.util.find_spec(name) is None for name in ("mcp_pal_cli", "mcp_pal_app")}
print(json.dumps({"required": required, "forbidden": forbidden, "version": metadata.version("mcp-pal")}, sort_keys=True))
"""
    result = _run([str(project_python), "-c", code], cwd=cwd, env=env)
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise GateFailure(
            "project environment returned an invalid package check"
        ) from exc
    required = payload.get("required")
    expected_required = (
        "pytest",
        "mcp_pal",
        "mcp_pal.pytest_plugin",
        "SQLiteExecutionStore",
    )
    if not isinstance(required, dict) or not all(
        required.get(name) for name in expected_required
    ):
        raise GateFailure(
            "project environment is missing pytest, SDK, plugin, or SQLite storage"
        )
    if payload.get("forbidden") != {"mcp_pal_cli": True, "mcp_pal_app": True}:
        raise GateFailure("project environment contains the standalone CLI or app")
    if payload.get("version") != version:
        raise GateFailure("project SDK version does not match the release")


def _copy_live_target(repo: Path) -> None:
    """Copy only the selected example into a disposable project checkout."""

    source = ROOT / "sdk" / "examples"
    destination = repo / "sdk" / "examples"
    (destination / "tests").mkdir(parents=True)
    (destination / "servers").mkdir()
    for relative in (
        Path("tests/test_live_opencode.py"),
        Path("servers/example_mcp_server.py"),
    ):
        shutil.copy2(source / relative, destination / relative)
    (repo / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    e2e: end-to-end tests\n    live: external provider tests\n",
        encoding="utf-8",
    )


def _free_port() -> int:
    # Avoid the OS ephemeral range: the live provider call opens outbound
    # sockets between this check and the later UI bind, and can otherwise
    # consume the exact source port that bind(port=0) just selected.
    for port in range(18_000, 19_000):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise GateFailure("no free loopback port is available for the live UI gate")


def cli_test_command(
    executable: Path, project_python: Path, database: Path, port: int
) -> list[str]:
    return [
        str(executable),
        "test",
        "--ui",
        "--python",
        str(project_python),
        "--results-db",
        str(database),
        "--port",
        str(port),
        "--",
        "-q",
        TARGET,
    ]


class Child:
    """Capture bounded, redacted output from the CLI process."""

    def __init__(self, command: list[str], cwd: Path, env: Mapping[str, str]) -> None:
        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": dict(env),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif os.name == "nt":
            kwargs["creationflags"] = int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        try:
            self.process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            raise GateFailure("could not start the standalone CLI") from exc
        self.env = dict(env)
        self.lines: deque[str] = deque(maxlen=600)
        self._lock = threading.Lock()
        threading.Thread(target=self._capture, daemon=True).start()

    def _capture(self) -> None:
        if self.process.stdout is None:
            return
        try:
            for line in self.process.stdout:
                clean = redact(line.rstrip("\r\n"), self.env)
                with self._lock:
                    self.lines.append(clean[:MAX_DIAGNOSTICS])
                print(f"[cli] {clean}", flush=True)
        except (OSError, ValueError):
            return

    def text(self) -> str:
        with self._lock:
            return "\n".join(self.lines)

    def alive(self) -> bool:
        return self.process.poll() is None


def terminate(child: Child | None) -> None:
    if child is None:
        return
    process = child.process
    if process.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except (OSError, ProcessLookupError):
            pass
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def interrupt(child: Child | None) -> None:
    if child is None or child.process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(child.process.pid, signal.SIGINT)
        else:
            child.process.send_signal(
                getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)
            )
        child.process.wait(timeout=15)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        terminate(child)


def parse_ui_links(output: str, origin: str) -> tuple[str, str, str]:
    """Return history URL, direct URL, and decoded run ID from CLI output."""

    history = re.findall(r"(?m)^MCP-Pal UI: (https?://[^\s]+)$", output)
    if len(history) != 1 or history[0] != f"{origin}/history":
        raise GateFailure("CLI UI history link does not match the selected origin")
    direct = re.findall(r"(?m)^Run: (https?://[^\s]+)$", output)
    if len(direct) != 1:
        raise GateFailure("CLI UI process did not print exactly one direct run link")
    selected = urlsplit(origin)
    parsed = urlsplit(direct[0])
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise GateFailure("CLI direct link has an invalid port") from exc
    if (
        parsed.scheme != selected.scheme
        or parsed.hostname != selected.hostname
        or parsed_port != selected.port
        or parsed.query
        or parsed.fragment
    ):
        raise GateFailure("CLI direct link does not match the selected origin")
    prefix = "/playground/run/"
    if not parsed.path.startswith(prefix):
        raise GateFailure("CLI direct link does not use the playground run route")
    encoded_id = parsed.path[len(prefix) :]
    run_id = unquote(encoded_id)
    if not encoded_id or not run_id:
        raise GateFailure("CLI direct link has an empty run ID")
    return history[0], direct[0], run_id


def _http(url: str) -> tuple[int, str, bytes]:
    try:
        with urlopen(Request(url, method="GET"), timeout=5) as response:
            return (
                response.status,
                response.headers.get("Content-Type", ""),
                response.read(2_000_000),
            )
    except HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read(2_000_000)
    except (OSError, URLError) as exc:
        raise GateFailure("could not query the local UI server") from exc


def _json_get(url: str) -> Any:
    status, content_type, body = _http(url)
    if not 200 <= status < 300 or "json" not in content_type.lower():
        raise GateFailure(f"local API returned HTTP {status} or non-JSON content")
    try:
        return json.loads(body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GateFailure("local API returned invalid JSON") from exc


def _assert_execution_in_history(payload: Any, run_id: str) -> None:
    if not isinstance(payload, dict):
        raise GateFailure("executions API returned an invalid page")
    page = payload.get("page", payload)
    items = page.get("items", []) if isinstance(page, dict) else []
    if not isinstance(items, list) or len(items) != 1:
        raise GateFailure("expected exactly one persisted live execution")
    item = items[0] if isinstance(items[0], dict) else {}
    snapshot = item.get("snapshot", item)
    execution_id = snapshot.get("execution_id") if isinstance(snapshot, dict) else None
    if execution_id != run_id:
        raise GateFailure("history execution ID does not match the CLI direct link")


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "state" in value:
        return value.get("value") if value.get("state") == "observed" else None
    return value


def assert_report(envelope: Any, expected_model: str, expected_id: str) -> None:
    root = envelope if isinstance(envelope, dict) else {}
    report = root.get("report") if isinstance(root.get("report"), dict) else root
    snapshot = report.get("snapshot") if isinstance(report, dict) else None
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("lifecycle") != "finished"
        or snapshot.get("outcome") != "completed"
    ):
        raise GateFailure("live execution did not finish successfully")
    if snapshot.get("execution_id") != expected_id:
        raise GateFailure("report execution ID does not match the direct link")
    trace = root.get("trace") if isinstance(root.get("trace"), dict) else {}
    runtime = trace.get("runtime") if isinstance(trace, dict) else {}
    if not isinstance(runtime, dict) or runtime.get("kind") != "opencode":
        raise GateFailure("OpenCode runtime/model context is missing")
    spec = root.get("spec") if isinstance(root.get("spec"), dict) else {}
    harness = spec.get("harness") if isinstance(spec, dict) else {}
    if (
        harness.get("model") if isinstance(harness, dict) else None
    ) != expected_model and _unwrap(runtime.get("model_id")) != expected_model:
        raise GateFailure("OpenCode model context is missing")
    timeline = trace.get("timeline") if isinstance(trace, dict) else []
    calls = [
        item
        for item in timeline
        if isinstance(item, dict) and item.get("kind") == "tool_call"
    ]
    if len(calls) != 1:
        raise GateFailure("expected exactly one tool-call entry")
    call = calls[0]
    if _unwrap(call.get("tool")) != "shipping_quote" or _unwrap(
        call.get("arguments")
    ) != {"weight_kg": 2, "zone": "local"}:
        raise GateFailure("expected one shipping_quote call with required arguments")
    if _unwrap(call.get("tool_status")) != "success":
        raise GateFailure("shipping_quote call was not successful")
    result = _unwrap(call.get("result"))
    structured = (
        _unwrap(result.get("structured_content")) if isinstance(result, dict) else None
    )
    if not isinstance(structured, dict) or structured.get("currency") != "USD":
        raise GateFailure("shipping_quote result is not structured as USD")
    wire = call.get("wire")
    if not isinstance(wire, dict) or wire.get("state") != "observed":
        raise GateFailure("correlated wire evidence was not observed")


def browser_environment(
    source: Mapping[str, str], origin: str, run_id: str, model: str
) -> dict[str, str]:
    """Build Playwright's environment without provider credentials."""

    env = clean_environment(source)
    env.update(
        {
            "MCP_PAL_LIVE_UI_BASE_URL": origin,
            "MCP_PAL_LIVE_EXECUTION_ID": run_id,
            "MCP_PAL_LIVE_MODEL": model,
        }
    )
    return env


def run_playwright(playwright: Path, cwd: Path, env: Mapping[str, str]) -> None:
    try:
        result = subprocess.run(
            [str(playwright), "test", "e2e/live-opencode.spec.ts"],
            cwd=str(cwd),
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=PLAYWRIGHT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateFailure("Playwright live UI assertion could not complete") from exc
    diagnostics = safe_diagnostics(result.stdout + "\n" + result.stderr, env)
    if result.returncode != 0:
        suffix = f"\n{diagnostics}" if diagnostics else ""
        raise GateFailure(f"Playwright live UI assertion failed{suffix}")
    if diagnostics:
        print(diagnostics, flush=True)


def _check_browser_prerequisites(ui_dir: Path, env: Mapping[str, str]) -> Path:
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node is None:
        raise GateFailure("Node.js 24 or newer is required for the live UI gate")
    if npm is None:
        raise GateFailure(
            "npm is required to build the production UI for the live gate"
        )
    node_version = _run([node, "--version"], cwd=ui_dir, env=env, timeout=15).stdout
    match = re.search(r"v(\d+)", node_version)
    if match is None or int(match.group(1)) < 24:
        raise GateFailure("Node.js 24 or newer is required for the live UI gate")
    playwright = (
        ui_dir
        / "node_modules"
        / ".bin"
        / ("playwright.cmd" if os.name == "nt" else "playwright")
    )
    if not playwright.is_file():
        raise GateFailure(
            "UI Playwright dependency is unavailable; run npm ci in the UI checkout"
        )
    _run([str(playwright), "--version"], cwd=ui_dir, env=env, timeout=30)
    _run(
        [
            node,
            "-e",
            (
                "const fs=require('fs'); const p=require('playwright'); "
                "if(!fs.existsSync(p.chromium.executablePath())) process.exit(3)"
            ),
        ],
        cwd=ui_dir,
        env=env,
        timeout=30,
    )
    return playwright


def _wait_for_links(child: Child, origin: str) -> tuple[str, str, str]:
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        output = child.text()
        if "MCP-Pal UI:" in output and re.search(r"(?m)^Run: https?://", output):
            return parse_ui_links(output, origin)
        if not child.alive():
            break
        time.sleep(0.2)
    raise GateFailure("CLI did not print its history and direct run links")


def check(
    release_dir: str | os.PathLike[str] | None = None,
    version: str | None = None,
    ui_dir: str | os.PathLike[str] = UI_ROOT,
) -> int:
    if (release_dir is None) != (version is None):
        raise GateFailure("--release-dir and --version must be provided together")
    selected_ui = Path(ui_dir).expanduser().resolve()
    original_env = dict(os.environ)
    install_env = clean_environment(original_env)
    if not (selected_ui.is_dir() and (selected_ui / "package.json").is_file()):
        raise GateFailure("UI directory or package.json is unavailable")
    playwright = _check_browser_prerequisites(selected_ui, install_env)
    uv = shutil.which("uv")
    if uv is None:
        raise GateFailure("uv is required for the isolated live gate")
    api_key = os.environ.get("OPENCODE_API_KEY", "").strip()
    if not api_key:
        raise GateFailure("OPENCODE_API_KEY is not set in the selected environment")
    if shutil.which("opencode") is None:
        raise GateFailure("OpenCode executable is not installed")

    temp_path: Path | None = None
    child: Child | None = None
    failure: str | None = None
    try:
        temp_path = Path(tempfile.mkdtemp(prefix="mcp-pal-live-ui-")).resolve()
        if release_dir is None:
            version_result = _run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_cli_release.py"),
                    "--print-version",
                ],
                cwd=ROOT,
                env=install_env,
                timeout=30,
            )
            version = version_result.stdout.strip().splitlines()[-1]
            ui_dist = temp_path / "ui-dist"
            _run(
                ["npm", "run", "build", "--", "--outDir", str(ui_dist)],
                cwd=selected_ui,
                env=install_env,
                timeout=PROCESS_TIMEOUT,
            )
            release = temp_path / "release"
            _run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_cli_release.py"),
                    "--ui-dist",
                    str(ui_dist),
                    "--out-dir",
                    str(release),
                    "--expected-version",
                    version,
                ],
                cwd=ROOT,
                env=install_env,
                timeout=PROCESS_TIMEOUT,
            )
            release_dir = release
        assert release_dir is not None and version is not None
        cli_wheel, sdk_wheel, app_wheel = wheel_paths(release_dir, version)
        assert_bundled_ui(cli_wheel)
        repo = temp_path / "project"
        repo.mkdir()
        _copy_live_target(repo)
        tool_dir = temp_path / "uv-tools"
        tool_bin = temp_path / "uv-bin"
        cache_dir = temp_path / "uv-cache"
        tool_bin.mkdir()
        isolated_env = dict(install_env)
        isolated_env.update(
            {
                "UV_TOOL_DIR": str(tool_dir),
                "UV_TOOL_BIN_DIR": str(tool_bin),
                "UV_CACHE_DIR": str(cache_dir),
                "PATH": str(tool_bin) + os.pathsep + install_env.get("PATH", ""),
            }
        )
        _run(
            [
                uv,
                "tool",
                "install",
                "--force",
                str(cli_wheel),
                "--with",
                str(sdk_wheel),
                "--with",
                str(app_wheel),
            ],
            cwd=repo,
            env=isolated_env,
        )
        executable = _tool_command(tool_bin)
        _probe_tool(_tool_python(tool_dir), version, isolated_env, repo)
        project_venv = repo / ".venv"
        _run(
            [uv, "venv", "--python", sys.executable, str(project_venv)],
            cwd=repo,
            env=isolated_env,
        )
        project_python = _python_path(project_venv)
        # Put extras on the package name in a PEP 508 direct reference so the
        # requirement remains portable across uv and pip.
        sdk_requirement = f"mcp-pal[pytest,storage] @ {sdk_wheel.as_uri()}"
        _run(
            [uv, "pip", "install", "--python", str(project_python), sdk_requirement],
            cwd=repo,
            env=isolated_env,
        )
        _probe_project(project_python, version, isolated_env, repo)
        database = repo / ".mcp-pal" / "executions.sqlite"
        port = _free_port()
        model = original_env.get("MCP_PAL_LIVE_OPENCODE_MODEL", DEFAULT_MODEL)
        live_env = dict(isolated_env)
        live_env.update(
            {
                # The key is needed by the CLI-launched pytest child. It is
                # explicitly removed from the Playwright environment below.
                "OPENCODE_API_KEY": api_key,
                "MCP_PAL_RUN_LIVE_OPENCODE": "1",
                "MCP_PAL_LIVE_OPENCODE_MODEL": model,
            }
        )
        command = cli_test_command(executable, project_python, database, port)
        print(
            "live-ui-gate: launching exactly one live OpenCode pytest target",
            flush=True,
        )
        child = Child(command, repo, live_env)
        origin = f"http://127.0.0.1:{port}"
        history_url, direct_url, run_id = _wait_for_links(child, origin)
        _assert_execution_in_history(_json_get(f"{origin}/api/v2/executions"), run_id)
        assert_report(_json_get(execution_report_url(origin, run_id)), model, run_id)
        for route in (history_url, direct_url):
            status, content_type, body = _http(route)
            if (
                status != 200
                or "html" not in content_type.lower()
                or b"<html" not in body.lower()
            ):
                raise GateFailure("CLI UI route did not return the bundled SPA")
        run_playwright(
            playwright,
            selected_ui,
            browser_environment(original_env, origin, run_id, model),
        )
        interrupt(child)
        if child.process.returncode != 0:
            raise GateFailure(
                f"CLI did not preserve pytest success (exit {child.process.returncode})"
            )
        shutil.rmtree(temp_path)
        temp_path = None
        print("live OpenCode and bundled UI gate passed", flush=True)
    except (GateFailure, KeyboardInterrupt) as exc:
        failure = "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
        print(f"live-ui-gate: {failure}", file=sys.stderr)
        return_code = 2
    except (OSError, ValueError) as exc:
        failure = "operational failure"
        print(
            f"live-ui-gate: {failure}: {redact(str(exc), original_env)}",
            file=sys.stderr,
        )
        return_code = 2
    else:
        return_code = 0
    finally:
        terminate(child)
        if temp_path is not None:
            if failure is not None:
                diagnostics = [f"reason: {failure}"]
                if child is not None:
                    diagnostics.extend(f"[cli] {line}" for line in child.lines)
                (temp_path / "gate-diagnostics.txt").write_text(
                    safe_diagnostics("\n".join(diagnostics) + "\n", original_env),
                    encoding="utf-8",
                )
            print(f"live-ui-gate: diagnostics retained at {temp_path}", file=sys.stderr)
    return return_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--ui-dir", type=Path, default=UI_ROOT)
    args = parser.parse_args(argv)
    try:
        return check(args.release_dir, args.version, args.ui_dir)
    except GateFailure as exc:
        print(f"live-ui-gate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
