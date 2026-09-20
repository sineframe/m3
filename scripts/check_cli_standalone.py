"""Run the production CLI through an isolated, two-environment smoke test.

This gate is intentionally a plain Python script.  It is run against the
three wheels produced by the release build and never installs the checkout as
an editable package.  The CLI tool environment and the project test
environment are separate on purpose: the former owns the command and web app,
while the latter owns pytest and the SDK test code.
"""

from __future__ import annotations

import argparse
import email
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


class StandaloneGateError(RuntimeError):
    """A bounded, user-facing standalone gate failure."""


_MAX_DIAGNOSTICS = 6_000
_PROCESS_TIMEOUT = 180
_UI_TIMEOUT = 120
_SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
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
        if upper in _REMOVED_ENV_NAMES or any(
            part in upper for part in _SECRET_NAME_PARTS
        ):
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
    return (
        directory
        / ("Scripts" if os.name == "nt" else "bin")
        / (f"{name}.exe" if os.name == "nt" else name)
    )


def wheel_paths(
    release_dir: str | os.PathLike[str], version: str
) -> tuple[Path, Path, Path]:
    """Return exactly the CLI, SDK, and app wheels for ``version``."""

    release = Path(release_dir).expanduser().resolve()
    if not release.is_dir():
        raise StandaloneGateError("release directory is unavailable")
    expected = tuple(
        release / f"{prefix}-{version}-py3-none-any.whl"
        for prefix in ("m3_cli", "m3", "m3_app")
    )
    if (
        any(not path.is_file() for path in expected)
        or len(tuple(release.glob("*.whl"))) != 3
    ):
        raise StandaloneGateError(
            "release directory must contain exactly the three versioned wheels"
        )
    if len(set(expected)) != 3:
        raise StandaloneGateError("release wheel names are not unique")
    return expected


def assert_bundled_ui(wheel: Path) -> None:
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise StandaloneGateError("CLI wheel is not a readable wheel archive") from exc
    if "m3_cli/ui/index.html" not in names or not any(
        name.startswith("m3_cli/ui/assets/") and not name.endswith("/")
        for name in names
    ):
        raise StandaloneGateError(
            "CLI wheel does not contain the bundled production UI"
        )


def _wheel_requirements(
    wheels: tuple[Path, ...], *, extras: frozenset[str]
) -> tuple[str, ...]:
    """Read third-party requirements from local wheels without installing them.

    Local M3 distributions are installed in a separate ``--no-index`` pass.
    This second list deliberately contains only third-party requirements, so
    uv may resolve those from its configured package index without ever
    falling back to a checkout package or an unbuilt local distribution.
    """

    requirements: set[str] = set()
    local_names = {"m3", "m3-app", "m3-cli"}
    for wheel in wheels:
        try:
            with zipfile.ZipFile(wheel) as archive:
                metadata_name = next(
                    name
                    for name in archive.namelist()
                    if name.endswith(".dist-info/METADATA")
                )
                metadata = email.message_from_bytes(archive.read(metadata_name))
        except (OSError, StopIteration, zipfile.BadZipFile) as exc:
            raise StandaloneGateError(
                f"could not read requirements from {wheel.name}"
            ) from exc
        for raw in metadata.get_all("Requires-Dist") or ():
            requirement, _, marker = raw.partition(";")
            marker = marker.strip()
            if marker and "extra" in marker:
                if not any(
                    re.search(
                        rf"extra\s*==\s*['\"]{re.escape(extra)}['\"]",
                        marker,
                    )
                    for extra in extras
                ):
                    continue
                # The requested extras are selected explicitly by this gate;
                # retaining ``extra == ...`` would make uv skip the package
                # when it resolves this standalone third-party list.
                marker = ""
            requirement = requirement.strip()
            package = re.match(r"[A-Za-z0-9][A-Za-z0-9_.-]*", requirement)
            if (
                package is None
                or package.group(0).lower().replace("_", "-") in local_names
            ):
                continue
            requirements.add(requirement + (f"; {marker}" if marker else ""))
    return tuple(sorted(requirements))


def _install_wheels(
    uv: str,
    python: Path,
    wheels: tuple[Path, ...],
    release_dir: Path,
    env: Mapping[str, str],
    *,
    extras: frozenset[str],
) -> None:
    """Install local wheels with no index, then resolve only third parties."""

    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--no-index",
            "--find-links",
            str(release_dir),
            "--no-deps",
            *(str(wheel) for wheel in wheels),
        ],
        cwd=python.parent,
        env=dict(env),
    )
    requirements = _wheel_requirements(wheels, extras=extras)
    if requirements:
        _run(
            [uv, "pip", "install", "--python", str(python), *requirements],
            cwd=python.parent,
            env=dict(env),
        )


def _tool_probe(
    tool_python: Path,
    expected_version: str,
    env: Mapping[str, str],
    source_root: Path,
) -> None:
    code = r"""
import importlib.metadata as metadata
import importlib.util
import json
import sys
from importlib import resources
from m3 import MCPTestKit

required = {
    name: importlib.util.find_spec(name) is not None
    for name in ("m3_cli", "m3", "m3_app")
}
forbidden = {
    name: importlib.util.find_spec(name) is None
    for name in ("pytest", "streamlit", "requests")
}
ui = resources.files("m3_cli").joinpath("ui")
payload = {
    "required": required,
    "forbidden": forbidden,
    "version": metadata.version("m3"),
    "ui": ui.joinpath("index.html").is_file() and any(item.is_file() for item in ui.joinpath("assets").iterdir()),
    "source_absent": all("__SOURCE_ROOT__" not in item for item in sys.path),
    "kit_imported": MCPTestKit.__name__ == "MCPTestKit",
}
print(json.dumps(payload, sort_keys=True))
""".replace("__SOURCE_ROOT__", str(source_root))
    result = _run([str(tool_python), "-c", code], cwd=tool_python.parent, env=dict(env))
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise StandaloneGateError(
            "CLI tool environment returned an invalid package check"
        ) from exc
    if payload.get("required") != {
        "m3_cli": True,
        "m3": True,
        "m3_app": True,
    }:
        raise StandaloneGateError(
            "CLI tool environment is missing a required M3 package"
        )
    if payload.get("forbidden") != {
        "pytest": True,
        "streamlit": True,
        "requests": True,
    }:
        raise StandaloneGateError(
            "CLI tool environment contains a project-only package"
        )
    if (
        payload.get("version") != expected_version
        or payload.get("ui") is not True
        or payload.get("source_absent") is not True
        or payload.get("kit_imported") is not True
    ):
        raise StandaloneGateError(
            "CLI tool environment has the wrong version, source import, or bundled UI"
        )


def _project_probe(
    project_python: Path,
    cwd: Path,
    expected_version: str,
    env: Mapping[str, str],
    source_root: Path,
) -> None:
    code = r"""
import importlib.metadata as metadata
import importlib.util
import json
import sys
from m3 import MCPTestKit

required = {}
for name in ("pytest", "m3", "m3.pytest_plugin"):
    required[name] = importlib.util.find_spec(name) is not None
try:
    from m3.storage import SQLiteExecutionStore
except Exception:
    required["SQLiteExecutionStore"] = False
else:
    required["SQLiteExecutionStore"] = True
forbidden = {name: importlib.util.find_spec(name) is None for name in ("m3_cli", "m3_app")}
probe_path = "__PROBE_PATH__"
store = SQLiteExecutionStore(probe_path)
store.close()
reopened = SQLiteExecutionStore(probe_path)
reopened.close()
import os
os.unlink(probe_path)
print(json.dumps({"required": required, "forbidden": forbidden, "version": metadata.version("m3"), "source_absent": all("__SOURCE_ROOT__" not in item for item in sys.path), "kit_imported": MCPTestKit.__name__ == "MCPTestKit"}, sort_keys=True))
""".replace("__SOURCE_ROOT__", str(source_root)).replace(
        "__PROBE_PATH__", str(cwd / "m3-probe.sqlite")
    )
    result = _run([str(project_python), "-c", code], cwd=cwd, env=dict(env))
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise StandaloneGateError(
            "project environment returned an invalid package check"
        ) from exc
    required = payload.get("required")
    if not isinstance(required, dict) or not all(
        required.get(name)
        for name in (
            "pytest",
            "m3",
            "m3.pytest_plugin",
            "SQLiteExecutionStore",
        )
    ):
        raise StandaloneGateError(
            "project environment is missing pytest, SDK, plugin, or SQLite storage"
        )
    if payload.get("forbidden") != {"m3_cli": True, "m3_app": True}:
        raise StandaloneGateError(
            "project environment contains the standalone CLI or app"
        )
    if (
        payload.get("version") != expected_version
        or payload.get("source_absent") is not True
        or payload.get("kit_imported") is not True
    ):
        raise StandaloneGateError("project SDK version or source import check failed")


def _write_dummy_test(repo: Path) -> None:
    tests = repo / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_m3_starter.py").write_text(
        """import pytest

from m3 import MCPTestKit
from m3.testing import FaultInjector
from m3.types import DirectSpec, Ping, ServerBinding

pytestmark = pytest.mark.m3(suite_name="standalone")


def test_stored_public_sdk_run() -> None:
    # In-process registrations are intentionally not serializable execution
    # specs. The public SDK's deterministic stdio fixture gives kit.run a
    # portable spec while still exercising a real MCP process boundary.
    server = FaultInjector().stdio_server(name="standalone-gate")
    spec = DirectSpec(
        servers=(ServerBinding(server=server),),
        operation=Ping(),
    )
    with MCPTestKit() as kit:
        result = kit.run(spec)
    assert result.snapshot.outcome.value == "completed"
""",
        encoding="utf-8",
    )


def _stored_run_ids(
    project_python: Path,
    repo: Path,
    database: Path,
    env: Mapping[str, str],
) -> list[str]:
    code = r"""
import json
import sys
from m3.storage import SQLiteExecutionStore

store = SQLiteExecutionStore(sys.argv[1])
try:
    page = store.list_executions(limit=100, offset=0)
    print(json.dumps([str(item.execution_id.root) for item in page.items]))
finally:
    store.close()
"""
    result = _run(
        [str(project_python), "-c", code, str(database)], cwd=repo, env=dict(env)
    )
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise StandaloneGateError("project store query returned invalid JSON") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StandaloneGateError("project store query returned invalid run IDs")
    return value


def _stored_test_run_ids(
    project_python: Path,
    repo: Path,
    database: Path,
    env: Mapping[str, str],
) -> list[str]:
    """Read pytest manifest IDs for the CLI's baseline option."""

    code = r"""
import json
import sys
from m3.storage import SQLiteExecutionStore

store = SQLiteExecutionStore(sys.argv[1])
try:
    values = store.list_test_runs()
    print(json.dumps([str(item["run_id"]) for item in values if "run_id" in item]))
finally:
    store.close()
"""
    result = _run(
        [str(project_python), "-c", code, str(database)], cwd=repo, env=dict(env)
    )
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise StandaloneGateError(
            "project manifest query returned invalid JSON"
        ) from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StandaloneGateError("project manifest query returned invalid run IDs")
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
        raise StandaloneGateError(
            "CLI direct link does not match the selected loopback origin"
        )
    prefix = "/reports/runs/"
    if not parsed.path.startswith(prefix):
        raise StandaloneGateError("CLI direct link does not use the reports run route")
    encoded_suffix = parsed.path[len(prefix) :]
    if not encoded_suffix or unquote(encoded_suffix) != expected_id:
        raise StandaloneGateError(
            "CLI direct link does not identify the newly stored run"
        )
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
        raise StandaloneGateError(
            "the executions endpoint returned HTML or invalid JSON"
        ) from exc

    def contains(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                (
                    key in {"execution_id", "executionId", "id"}
                    and str(item) == expected_id
                )
                or contains(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(contains(item) for item in value)
        return False

    if not contains(payload):
        raise StandaloneGateError(
            "the executions API does not contain the newly stored run"
        )


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
    existing_ids: tuple[str, ...],
    verdict_run_id: str,
    ui_dir: Path | None = None,
) -> None:
    existing_manifest_ids = set(
        _stored_test_run_ids(project_python, repo, database, env)
    )
    command = [
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
            creationflags=(
                int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                if os.name == "nt"
                else 0
            ),
        )
    except OSError as exc:
        raise StandaloneGateError("could not start the CLI UI process") from exc
    output = _ManagedOutput(process)
    deadline = time.monotonic() + _UI_TIMEOUT
    while time.monotonic() < deadline:
        text = output.text()
        if re.search(r"(?m)^Run: http://127\.0\.0\.1:[0-9]+/reports/runs/", text):
            break
        if process.poll() is not None:
            break
        time.sleep(0.1)
    else:
        _terminate(process)
        raise StandaloneGateError("CLI UI process did not print a report run link")

    text = output.text()
    if "Run: " not in text:
        _terminate(process)
        raise StandaloneGateError(
            f"CLI UI process exited before printing a report link\n{_safe_diagnostics(text, env)}"
        )
    # The UI command prints links as soon as the server is ready; its pytest
    # child can still be committing the execution when those links appear.
    current_runs: list[str] = []
    storage_deadline = time.monotonic() + 30
    while time.monotonic() < storage_deadline:
        current_runs = _stored_run_ids(project_python, repo, database, env)
        if len(current_runs) >= len(existing_ids) + 1:
            break
        time.sleep(0.2)
    new_runs = [run_id for run_id in current_runs if run_id not in existing_ids]
    if len(current_runs) != len(existing_ids) + 1 or len(new_runs) != 1:
        _terminate(process)
        raise StandaloneGateError(
            "UI test did not create exactly one additional stored run"
        )
    expected_id = new_runs[0]
    new_manifest_ids = (
        set(_stored_test_run_ids(project_python, repo, database, env))
        - existing_manifest_ids
    )
    if len(new_manifest_ids) != 1:
        _terminate(process)
        raise StandaloneGateError(
            "UI test did not create exactly one test run manifest"
        )
    origin = f"http://127.0.0.1:{port}"
    try:
        direct_url = _parse_ui_links(text, origin, new_manifest_ids.pop())
    except StandaloneGateError:
        _terminate(process)
        raise
    status, content_type, body = _http(origin + "/api/v2/executions")
    if status != 200 or "json" not in content_type.lower():
        _terminate(process)
        raise StandaloneGateError("executions API did not return JSON")
    _assert_json_execution(body, expected_id)
    status, content_type, body = _http(
        f"{origin}/api/v2/feedback/{quote(verdict_run_id, safe='')}"
    )
    if status != 200 or "json" not in content_type.lower():
        _terminate(process)
        raise StandaloneGateError("Reports feedback API did not return JSON")
    try:
        served = json.loads(body)["feedback"]
        verdicts = {
            str(item["node_id"]).split("::")[-1]: item["verdict"]
            for item in served["tests"]
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _terminate(process)
        raise StandaloneGateError("Reports feedback API is malformed") from exc
    if served.get("summary", {}).get("failures") != 3 or verdicts != {
        "test_expected_tool_error": "passed",
        "test_failed_matcher": "failed_assertion",
        "test_protocol_error": "protocol_error",
        "test_setup_error": "setup_error",
        "test_plain_pass": "passed",
    }:
        _terminate(process)
        raise StandaloneGateError("Reports verdicts differ from installed feedback")
    status, content_type, body = _http(origin + "/history")
    if (
        status != 200
        or "html" not in content_type.lower()
        or b"<html" not in body.lower()
    ):
        _terminate(process)
        raise StandaloneGateError("history route did not return the bundled SPA")
    status, content_type, body = _http(direct_url)
    if (
        status != 200
        or "html" not in content_type.lower()
        or b"<html" not in body.lower()
    ):
        _terminate(process)
        raise StandaloneGateError("direct run route did not return the bundled SPA")
    if re.search(r"(?i)\b(?:node|npm|vite)\b", text):
        _terminate(process)
        raise StandaloneGateError("CLI UI process attempted to use a Node/Vite runtime")

    if ui_dir is not None:
        try:
            _run_playwright_contract(ui_dir, origin, expected_id, env)
            _run_playwright_verdict_contract(ui_dir, origin, verdict_run_id, env)
        except StandaloneGateError:
            _terminate(process)
            raise

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


def _run_playwright_contract(
    ui_dir: Path, origin: str, execution_id: str, env: Mapping[str, str]
) -> None:
    """Verify a real v2 report and its bundled UI rendering in Chromium."""

    node = shutil.which("node")
    if node is None or not (ui_dir / "node_modules" / "playwright").exists():
        raise StandaloneGateError("Node.js and UI Playwright must be installed")
    code = r"""
const { chromium } = require('playwright');
const [origin, executionId] = process.argv.slice(1);
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const report = await page.request.get(`${origin}/api/v2/executions/${encodeURIComponent(executionId)}/report`);
    if (!report.ok()) throw new Error(`v2 report returned ${report.status()}`);
    const body = await report.json();
    if (body.trace?.schema_id !== 'trace_view') throw new Error('v2 trace schema is not neutral');
    if (!body.report?.events?.every((event) => event.schema === 'event')) {
      throw new Error('v2 event schemas are not neutral');
    }
    await page.goto(`${origin}/history`);
    const row = page.locator('tr').filter({ hasText: executionId });
    await row.waitFor({ state: 'visible', timeout: 15000 });
    await row.getByRole('button', { name: `Open ${executionId}` }).click();
    await page.waitForURL(new RegExp(`/playground/run/${executionId}$`));
    await page.getByText('Completed', { exact: true }).first().waitFor({ state: 'visible' });
    console.log('Playwright v2 report and bundled UI contract passed');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    _run(
        [node, "-e", code, origin, execution_id],
        cwd=ui_dir,
        env=dict(env),
        timeout=_UI_TIMEOUT,
    )


def _run_playwright_verdict_contract(
    ui_dir: Path, origin: str, run_id: str, env: Mapping[str, str]
) -> None:
    """Read the installed UI's real Reports page for the mixed verdict run."""

    node = shutil.which("node")
    if node is None or not (ui_dir / "node_modules" / "playwright").exists():
        raise StandaloneGateError("Node.js and UI Playwright must be installed")
    code = r"""
const { chromium } = require('playwright');
const [origin, runId] = process.argv.slice(1);
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await page.goto(`${origin}/reports/runs/${encodeURIComponent(runId)}`);
    await page.getByRole('heading', { name: runId }).waitFor({ state: 'visible', timeout: 15000 });
    const expected = [
      ['test_expected_tool_error', 'Passed'],
      ['test_failed_matcher', 'Failed Assertion'],
      ['test_protocol_error', 'Protocol Error'],
      ['test_setup_error', 'Setup Error'],
      ['test_plain_pass', 'Passed'],
    ];
    for (const [name, verdict] of expected) {
      const row = page.getByRole('button', { name: new RegExp(`Test: .*${name}\\. Result: ${verdict}\\.`) });
      await row.waitFor({ state: 'visible', timeout: 15000 });
      if (name === 'test_expected_tool_error' && !(await row.getByText('Tool error result').isVisible())) {
        throw new Error('expected tool error is missing from Reports');
      }
    }
    const summary = page.getByRole('region', { name: 'Test run summary' });
    if (!(await summary.getByText('Failures').locator('..').getByText('3', { exact: true }).isVisible())) {
      throw new Error('Reports failure count differs from pytest');
    }
    console.log('Playwright installed Reports verdict contract passed');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    _run(
        [node, "-e", code, origin, run_id],
        cwd=ui_dir,
        env=dict(env),
        timeout=_UI_TIMEOUT,
    )


def _run_direct_pytest(
    project_python: Path,
    repo: Path,
    database: Path,
    env: Mapping[str, str],
) -> None:
    command = [
        str(project_python),
        "-m",
        "pytest",
        "-q",
        "-p",
        "m3.pytest_plugin",
        "--results-db",
        str(database),
        "--project-root",
        str(repo),
        "--harness",
        "opencode=fixture/model",
        "--credential-env",
        "VENDOR_API_KEY=M3_GATE_SOURCE",
        "--trials",
        "1",
        "--suite",
        "standalone",
        "--execution-timeout",
        "30",
        "tests/test_m3_starter.py",
    ]
    _run(command, cwd=repo, env=dict(env))


def _run_m3_test(
    executable: Path,
    project_python: Path,
    repo: Path,
    database: Path,
    baseline: str,
    env: Mapping[str, str],
) -> None:
    command = [
        str(executable),
        "test",
        "--python",
        str(project_python),
        "--project-root",
        str(repo),
        "--results-db",
        str(database),
        "--baseline",
        baseline,
        "--harness",
        "opencode=fixture/model",
        "--credential-env",
        "VENDOR_API_KEY=M3_GATE_SOURCE",
        "--trials",
        "1",
        "--suite",
        "standalone",
        "--execution-timeout",
        "30",
        "--",
        "-q",
    ]
    _run(command, cwd=repo, env=dict(env))


def _run_verdict_fixture(
    executable: Path,
    project_python: Path,
    repo: Path,
    database: Path,
    env: Mapping[str, str],
) -> str:
    """Check mixed pytest outcomes using only the installed release wheels."""

    before = set(_stored_test_run_ids(project_python, repo, database, env))
    fixture = repo / "tests" / "test_verdicts.py"
    shutil.copyfile(
        _CHECKOUT_ROOT / "scripts" / "fixtures" / "verdicts" / "test_verdicts.py",
        fixture,
    )
    command = [
        str(executable),
        "test",
        "--python",
        str(project_python),
        "--project-root",
        str(repo),
        "--results-db",
        str(database),
        "--",
        "-q",
        "tests/test_verdicts.py",
    ]
    print("+", _display(command), flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=str(repo),
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=_PROCESS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StandaloneGateError("installed verdict fixture could not run") from exc
    finally:
        fixture.unlink(missing_ok=True)
    terminal = result.stdout + "\n" + result.stderr
    if (
        result.returncode != 1
        or "2 failed, 2 passed, 1 error" not in terminal
        or "M3 verdicts: 2 passed, 1 failed assertion, 1 protocol error, 1 setup error"
        not in terminal
    ):
        raise StandaloneGateError(
            "installed CLI verdicts differ from pytest\n"
            + _safe_diagnostics(terminal, env)
        )
    new_runs = set(_stored_test_run_ids(project_python, repo, database, env)) - before
    if len(new_runs) != 1:
        raise StandaloneGateError("verdict fixture did not create one test run")
    run_id = new_runs.pop()
    path = repo / ".m3" / "reports" / run_id / "feedback.json"
    try:
        feedback = json.loads(path.read_text(encoding="utf-8"))
        tests = {
            str(item["node_id"]).split("::")[-1]: item for item in feedback["tests"]
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StandaloneGateError("installed verdict feedback is malformed") from exc
    expected = {
        "test_expected_tool_error": "passed",
        "test_failed_matcher": "failed_assertion",
        "test_protocol_error": "protocol_error",
        "test_setup_error": "setup_error",
        "test_plain_pass": "passed",
    }
    if (
        feedback.get("summary", {}).get("tests") != 5
        or feedback.get("summary", {}).get("failures") != 3
        or len(feedback.get("failures", ())) != 3
        or {name: item.get("verdict") for name, item in tests.items()} != expected
        or tests["test_expected_tool_error"].get("tool_result") != "tool_error"
        or not any(item.get("evaluations") for item in feedback["failures"])
    ):
        raise StandaloneGateError("installed verdict feedback differs from pytest")
    return run_id


def check(
    release_dir: str | os.PathLike[str],
    version: str,
    ui_dir: str | os.PathLike[str] | None = None,
) -> None:
    cli_wheel, sdk_wheel, app_wheel = wheel_paths(release_dir, version)
    assert_bundled_ui(cli_wheel)
    uv = shutil.which("uv")
    if uv is None:
        raise StandaloneGateError("uv is required for the standalone gate")

    with tempfile.TemporaryDirectory(prefix="m3-cli-standalone-") as temporary:
        # macOS commonly exposes the temporary directory through /var ->
        # /private/var. SQLite intentionally rejects symlinked database paths,
        # so use the physical temporary path throughout the gate.
        root = Path(temporary).resolve()
        env = _clean_environment()
        env["M3_GATE_SOURCE"] = "standalone-gate-source"
        env["PATH"] = env.get("PATH", "")
        release = Path(release_dir).resolve()

        # Keep this gate explicit about the supported floor and release Python.
        # The workflow runs the same wheel set through both environments.
        python_versions = tuple(
            value.strip()
            for value in os.environ.get("M3_GATE_PYTHONS", "3.10,3.13").split(",")
            if value.strip()
        )
        if not python_versions:
            raise StandaloneGateError("M3_GATE_PYTHONS must not be empty")
        browser_ui_dir = Path(ui_dir).resolve() if ui_dir is not None else None

        for python_version in python_versions:
            version_root = root / python_version.replace(".", "-")
            version_root.mkdir()
            version_repo = version_root / "repo"
            version_repo.mkdir()
            _run(["git", "init", "-q"], cwd=version_repo, env=dict(env))
            tool_env = version_root / "tool-env"
            project_env = version_repo / ".venv"
            _run(
                [uv, "venv", "--python", python_version, str(tool_env)],
                cwd=version_repo,
                env=dict(env),
            )
            tool_python = _python_path(tool_env)
            _install_wheels(
                uv,
                tool_python,
                (cli_wheel, sdk_wheel, app_wheel),
                release,
                env,
                extras=frozenset({"storage"}),
            )
            executable = _command_path(tool_env, "m3")
            if not executable.is_file():
                raise StandaloneGateError(
                    f"m3 executable is missing from the {python_version} tool environment"
                )
            _tool_probe(tool_python, version, env, _CHECKOUT_ROOT)
            _run([str(executable), "--help"], cwd=version_repo, env=dict(env))
            _run(
                [str(tool_python), "-m", "m3_cli", "--help"],
                cwd=version_repo,
                env=dict(env),
            )

            # Start from a genuinely fresh project, exercise init, then
            # replace only its skipped starter body with a deterministic test.
            _run(
                [
                    str(executable),
                    "init",
                    "--project-root",
                    str(version_repo),
                    "--project-name",
                    "standalone-gate",
                    "--suite",
                    "standalone",
                ],
                cwd=version_repo,
                env=dict(env),
            )
            _write_dummy_test(version_repo)
            _run(
                [uv, "venv", "--python", python_version, str(project_env)],
                cwd=version_repo,
                env=dict(env),
            )
            setup_env = dict(env)
            setup_env["M3_RELEASE_BASE_URL"] = release.as_uri()
            _run(
                [str(executable), "setup", "--project-root", str(version_repo)],
                cwd=version_repo,
                env=setup_env,
            )
            project_python = _python_path(project_env)
            _project_probe(project_python, version_repo, version, env, _CHECKOUT_ROOT)
            database = version_repo / ".m3" / "executions.sqlite"
            _run(
                [
                    str(executable),
                    "doctor",
                    "--python",
                    str(project_python),
                    "--project-root",
                    str(version_repo),
                    "--require",
                    "storage:sqlite",
                ],
                cwd=version_repo,
                env=dict(env),
            )

            # Direct pytest and the CLI each execute the same local MCP test.
            # Across these two invocations all eight public plugin options are
            # exercised, including baseline comparison on the second run.
            _run_direct_pytest(project_python, version_repo, database, env)
            first_runs = _stored_run_ids(project_python, version_repo, database, env)
            first_test_runs = _stored_test_run_ids(
                project_python, version_repo, database, env
            )
            if len(first_runs) != 1 or len(first_test_runs) != 1:
                raise StandaloneGateError(
                    f"expected one stored run and manifest after direct pytest ({python_version}), found {len(first_runs)} and {len(first_test_runs)}"
                )
            _run_m3_test(
                executable,
                project_python,
                version_repo,
                database,
                first_test_runs[0],
                env,
            )
            second_runs = _stored_run_ids(project_python, version_repo, database, env)
            second_test_runs = _stored_test_run_ids(
                project_python, version_repo, database, env
            )
            if len(second_runs) != 2 or first_runs[0] not in second_runs:
                raise StandaloneGateError(
                    f"CLI test did not persist/reload the baseline run ({python_version})"
                )
            if len(second_test_runs) != 2 or first_test_runs[0] not in second_test_runs:
                raise StandaloneGateError(
                    f"CLI test did not persist/reload the baseline manifest ({python_version})"
                )
            verdict_run_id = _run_verdict_fixture(
                executable, project_python, version_repo, database, env
            )
            before_ui_runs = _stored_run_ids(
                project_python, version_repo, database, env
            )
            port = _free_port()
            _run_ui_gate(
                executable,
                project_python,
                version_repo,
                database,
                port,
                env,
                tuple(before_ui_runs),
                verdict_run_id,
                browser_ui_dir if python_version == python_versions[-1] else None,
            )
            final_runs = _stored_run_ids(project_python, version_repo, database, env)
            if len(final_runs) != len(before_ui_runs) + 1 or not set(
                before_ui_runs
            ).issubset(final_runs):
                raise StandaloneGateError(
                    f"UI test did not preserve all installed-wheel runs ({python_version})"
                )
            print(
                f"isolated CLI standalone gate passed on Python {python_version}",
                flush=True,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--ui-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        check(args.release_dir, args.version, args.ui_dir)
    except StandaloneGateError as exc:
        print(f"CLI standalone gate failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
