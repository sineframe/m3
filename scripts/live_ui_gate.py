"""Run the manual live OpenCode and bundled-UI merge gate.

This gate performs one paid provider call. It installs the production CLI
wheel into an isolated uv tool environment and runs the selected real pytest
target in a separate project virtual environment. The checkout is never
installed as an editable package.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from queue import Empty, Queue
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT.parent / "mcppal-ui"
TARGET = "sdk/examples/nondeterministic/test_live_agent_selection.py::test_selected_agents_choose_shipping_tool"
UI_TARGET = "live-opencode.spec.ts"
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


def _run_streaming(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float = PROCESS_TIMEOUT,
    label: str = "command",
    output_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a bounded command while emitting redacted output as it arrives."""

    output_file = output_path.open("w", encoding="utf-8") if output_path else None
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
        process = subprocess.Popen(
            command,
            **kwargs,
        )
    except OSError as exc:
        if output_file:
            output_file.close()
        raise GateFailure(f"could not start {command[0]}") from exc
    print("+", _display(command), flush=True)

    output: list[str] = []
    lines: Queue[str] = Queue()

    def read_output() -> None:
        if process.stdout is None:
            return
        try:
            for line in process.stdout:
                lines.put(line)
        except (OSError, ValueError):
            return

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    while process.poll() is None or not lines.empty():
        if process.poll() is None and time.monotonic() >= deadline:
            terminate_process(process)
            reader.join(timeout=1)
            if output_file:
                output_file.close()
            detail = safe_diagnostics("\n".join(output), env)
            suffix = f"\n{detail}" if detail else ""
            raise GateFailure(f"{label} timed out after {timeout:.0f}s{suffix}")
        try:
            line = lines.get(timeout=0.2)
        except Empty:
            continue
        clean = redact(line.rstrip("\r\n"), env)
        output.append(clean)
        if output_file:
            output_file.write(clean + "\n")
            output_file.flush()
        print(f"[{label}] {clean}", flush=True)
    if output_file:
        output_file.close()
    reader.join(timeout=1)
    result = subprocess.CompletedProcess(
        command, process.returncode, "\n".join(output), None
    )
    if result.returncode != 0:
        detail = safe_diagnostics(result.stdout, env)
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
        Path("nondeterministic/test_live_agent_selection.py"),
        Path("servers/example_mcp_server.py"),
        Path("live_agent_loop.py"),
    ):
        target = (
            destination / "live_agent_loop.py"
            if relative.name == "live_agent_loop.py"
            else destination / relative
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    (repo / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    e2e: end-to-end tests\n    live: external provider tests\n",
        encoding="utf-8",
    )


def _write_ui_probe(destination: Path) -> Path:
    """Write a provider-neutral UI probe in the disposable gate directory."""

    target = destination / UI_TARGET
    try:
        (destination / "node_modules").symlink_to(
            UI_ROOT / "node_modules", target_is_directory=True
        )
    except OSError as exc:
        raise GateFailure("could not prepare disposable Playwright probe") from exc
    target.write_text(
        """import { expect, test } from '@playwright/test';
const executionId = process.env.MCP_PAL_LIVE_EXECUTION_ID;
if (!process.env.MCP_PAL_LIVE_UI_BASE_URL || !executionId) throw new Error('live UI variables are required');
test('renders the persisted selected execution', async ({ page }) => {
  await page.goto('/history');
  const row = page.locator('tr').filter({ hasText: executionId });
  await expect(row).toHaveCount(1);
  await expect(row.locator('[data-label="Outcome"]')).toHaveText('completed');
  await expect(page.locator('.history-state--error')).toHaveCount(0);
  await row.getByRole('button', { name: `Open ${executionId}` }).click();
  await expect(page).toHaveURL(new RegExp(`/playground/run/${executionId}$`));
  await expect(page.getByText('Completed', { exact: true }).first()).toBeVisible();
  const filter = page.getByRole('textbox', { name: 'Filter activity' });
  await filter.fill('shipping_quote');
  const toolRow = page.locator('.activity-row--tool_call');
  await expect(toolRow).toHaveCount(1);
  await expect(toolRow.first()).toBeVisible();
  await toolRow.getByRole('button', { name: 'Expand Tool call details' }).click();
  await expect(toolRow.getByRole('button', { name: 'Load inputs, outputs & evidence' })).toBeVisible();
});
""",
        encoding="utf-8",
    )
    (destination / "playwright.config.cjs").write_text(
        "module.exports = { testDir: '.', use: { baseURL: process.env.MCP_PAL_LIVE_UI_BASE_URL } };\n",
        encoding="utf-8",
    )
    return target


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
    executable: Path,
    project_python: Path,
    database: Path,
    port: int,
    env_file: Path,
    *,
    opencode_model: str = DEFAULT_MODEL,
    codex_model: str = "gpt-5.6-sol",
    providers: tuple[str, ...] = ("opencode", "codex"),
) -> list[str]:
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
        "--env-file",
        str(env_file),
    ]
    models = {"opencode": opencode_model, "codex": codex_model}
    for provider in providers:
        if provider not in models:
            raise ValueError(f"unsupported live provider {provider!r}")
        command.extend(("--harness", f"{provider}={models[provider]}"))
    command.extend(
        [
            "--trials",
            "1",
            "--",
            "-q",
            TARGET,
        ]
    )
    return command


def _dotenv_values(path: Path) -> dict[str, str]:
    try:
        from dotenv import dotenv_values

        values = dotenv_values(str(path), interpolate=False)
    except Exception as exc:
        raise GateFailure("explicit provider env file could not be read") from exc
    return {
        str(key): str(value)
        for key, value in values.items()
        if key and value is not None
    }


def _assert_secret_absent(value: Any, secrets: tuple[str, ...], label: str) -> None:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    if any(secret and secret in text for secret in secrets):
        raise GateFailure(f"provider credential value leaked into {label}")


def provider_secret_values(
    providers: tuple[str, ...],
    ambient: Mapping[str, str],
    dotenv: Mapping[str, str],
) -> tuple[str, ...]:
    keys = {"opencode": "OPENCODE_API_KEY", "codex": "OPENAI_API_KEY"}
    return tuple(
        dict.fromkeys(
            value
            for provider in providers
            for value in (
                ambient.get(keys[provider], ""),
                dotenv.get(keys[provider], ""),
            )
            if value
        )
    )


def _post_json(url: str, payload: Mapping[str, Any]) -> Any:
    try:
        request = Request(
            url,
            data=json.dumps(dict(payload)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise GateFailure(f"local API returned HTTP {response.status}")
            return json.loads(response.read())
    except HTTPError as exc:
        raise GateFailure(f"local API returned HTTP {exc.code}") from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise GateFailure("local API returned invalid JSON") from exc


def assert_aggregate(
    payload: Any, providers: tuple[str, ...], models: Mapping[str, str]
) -> None:
    aggregate = payload.get("aggregate") if isinstance(payload, dict) else None
    groups = aggregate.get("groups") if isinstance(aggregate, dict) else None
    if not isinstance(groups, list) or len(groups) < len(providers):
        raise GateFailure("evaluation aggregate response is malformed")
    group_keys = {
        str(group.get("key", {}).get("metadata.harness_config"))
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("key"), dict)
    }
    expected_configs = {f"{provider}:{models[provider]}" for provider in providers}
    if not expected_configs.issubset(group_keys):
        raise GateFailure(
            "evaluation aggregate is missing a selected provider configuration"
        )


class Child:
    """Capture bounded, redacted output from the CLI process."""

    def __init__(
        self,
        command: list[str],
        cwd: Path,
        env: Mapping[str, str],
        *,
        label: str = "cli",
        output_path: Path | None = None,
    ) -> None:
        output_file = output_path.open("w", encoding="utf-8") if output_path else None
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
            if output_file:
                output_file.close()
            raise GateFailure("could not start the standalone CLI") from exc
        self.env = dict(env)
        self.label = label
        self.output_file = output_file
        self.lines: deque[str] = deque(maxlen=600)
        self._lock = threading.Lock()
        self._capture_thread = threading.Thread(target=self._capture, daemon=True)
        self._capture_thread.start()

    def _capture(self) -> None:
        if self.process.stdout is None:
            return
        try:
            for line in self.process.stdout:
                clean = redact(line.rstrip("\r\n"), self.env)
                with self._lock:
                    self.lines.append(clean[:MAX_DIAGNOSTICS])
                if self.output_file:
                    self.output_file.write(clean + "\n")
                    self.output_file.flush()
                print(f"[{self.label}] {clean}", flush=True)
        except (OSError, ValueError):
            return
        finally:
            if self.output_file:
                self.output_file.close()

    def text(self) -> str:
        with self._lock:
            return "\n".join(self.lines)

    def alive(self) -> bool:
        return self.process.poll() is None

    def join(self, timeout: float = 1.0) -> None:
        self._capture_thread.join(timeout)


def _descendant_pids(root_pid: int) -> set[int]:
    """Find descendants before terminating a process group.

    A provider may detach a helper with ``setsid``. Such a process no longer
    belongs to the parent's process group, but it is still discoverable by its
    parent PID until the parent exits. Capture the tree first so cleanup can
    terminate those helpers explicitly.
    """

    if os.name != "posix":
        return set()
    try:
        listing = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid="], text=True, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    children: dict[int, set[int]] = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, parent = (int(value) for value in fields)
        except ValueError:
            continue
        children.setdefault(parent, set()).add(pid)
    descendants: set[int] = set()
    pending = list(children.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        if pid in descendants or pid == root_pid:
            continue
        descendants.add(pid)
        pending.extend(children.get(pid, ()))
    return descendants


def _live_pids(pids: set[int]) -> set[int]:
    """Return captured PIDs that are still running, excluding zombies."""

    if os.name != "posix" or not pids:
        return set()
    try:
        listing = subprocess.check_output(
            ["ps", "-axo", "pid=,state="], text=True, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    live: set[int] = set()
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        if pid in pids and not fields[1].startswith("Z"):
            live.add(pid)
    return live


def terminate_process(process: subprocess.Popen[Any] | None) -> None:
    """Terminate a process and all descendants it owns."""

    if process is None:
        return
    descendants = _descendant_pids(process.pid)
    for pid in descendants:
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
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
    for pid in _live_pids(descendants):
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def terminate(child: Child | None) -> None:
    if child is None:
        return
    terminate_process(child.process)


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


def parse_ui_links(
    output: str, origin: str
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Return history URL, direct URL, and decoded run ID from CLI output."""

    history = re.findall(r"(?m)^MCP-Pal UI: (https?://[^\s]+)$", output)
    if len(history) != 1 or history[0] != f"{origin}/history":
        raise GateFailure("CLI UI history link does not match the selected origin")
    direct = re.findall(r"(?m)^Run: (https?://[^\s]+)$", output)
    if not direct:
        raise GateFailure("CLI UI process did not print a direct run link")
    run_ids: list[str] = []
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
    for link in direct:
        parsed = urlsplit(link)
        prefix = "/playground/run/"
        if not parsed.path.startswith(prefix):
            raise GateFailure("CLI direct link does not use the playground run route")
        encoded_id = parsed.path[len(prefix) :]
        run_id = unquote(encoded_id)
        if not encoded_id or not run_id:
            raise GateFailure("CLI direct link has an empty run ID")
        run_ids.append(run_id)
    return history[0], tuple(direct), tuple(run_ids)


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
    if not isinstance(items, list):
        raise GateFailure("executions API returned invalid items")
    matches = []
    for candidate in items:
        if not isinstance(candidate, dict):
            continue
        snapshot = candidate.get("snapshot", candidate)
        if isinstance(snapshot, dict) and snapshot.get("execution_id") == run_id:
            matches.append(candidate)
    if len(matches) != 1:
        raise GateFailure("expected one persisted record for each live execution")
    item = matches[0]
    snapshot = item.get("snapshot", item)
    execution_id = snapshot.get("execution_id") if isinstance(snapshot, dict) else None
    if execution_id != run_id:
        raise GateFailure("history execution ID does not match the CLI direct link")


def assert_sqlite_persistence(
    database: str | os.PathLike[str], execution_id: str
) -> None:
    """Verify the live execution was durably recorded without reading payloads."""

    required_tables = {
        "v2_executions",
        "v2_test_runs",
        "v2_test_results",
        "v2_sessions",
        "v2_turns",
        "v2_events",
    }
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise GateFailure("live execution database was not created")
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise GateFailure(
            "live execution database could not be opened read-only"
        ) from exc
    try:
        found_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing_tables = sorted(required_tables - found_tables)
        if missing_tables:
            raise GateFailure("live execution database is missing required v2 tables")
        execution = connection.execute(
            "SELECT run_id,snapshot_json,specification_json "
            "FROM v2_executions WHERE id=?",
            (execution_id,),
        ).fetchone()
        if (
            execution is None
            or not isinstance(execution[0], str)
            or not execution[0]
            or execution[1] is None
            or execution[2] is None
        ):
            raise GateFailure("live execution row is missing its pytest run mapping")
        run_id = execution[0]
        checks = (
            (
                "v2_test_runs",
                "SELECT 1 FROM v2_test_runs WHERE run_id=? LIMIT 1",
            ),
            (
                "v2_test_results",
                "SELECT 1 FROM v2_test_results WHERE run_id=? LIMIT 1",
            ),
            (
                "v2_sessions",
                "SELECT 1 FROM v2_sessions WHERE execution_id=? LIMIT 1",
            ),
            (
                "v2_turns",
                "SELECT 1 FROM v2_turns AS t JOIN v2_sessions AS s "
                "ON s.id=t.session_id WHERE s.execution_id=? "
                "AND t.result_json IS NOT NULL "
                "AND json_extract(t.snapshot_json, '$.lifecycle')='finished' "
                "AND json_extract(t.snapshot_json, '$.outcome')='completed' "
                "LIMIT 1",
            ),
            (
                "v2_events",
                "SELECT 1 FROM v2_events WHERE execution_id=? LIMIT 1",
            ),
        )
        for table, query in checks:
            value = (
                run_id if table in {"v2_test_runs", "v2_test_results"} else execution_id
            )
            if connection.execute(query, (value,)).fetchone() is None:
                raise GateFailure(
                    f"live execution persistence is incomplete in {table}"
                )
    except sqlite3.Error as exc:
        raise GateFailure("live execution database could not be inspected") from exc
    finally:
        connection.close()


def _pytest_run_id(database: Path, execution_id: str) -> str:
    try:
        with sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro", uri=True
        ) as connection:
            row = connection.execute(
                "SELECT run_id FROM v2_executions WHERE id=?", (execution_id,)
            ).fetchone()
    except sqlite3.Error as exc:
        raise GateFailure("live execution database could not be inspected") from exc
    if row is None or not row[0]:
        raise GateFailure("live execution is missing its pytest run mapping")
    return str(row[0])


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "state" in value:
        return value.get("value") if value.get("state") == "observed" else None
    return value


def assert_report(
    envelope: Any,
    expected_model: str,
    expected_id: str,
    expected_harness: str = "opencode",
) -> None:
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
    expected_kind = {
        "claude": "claude_code",
        "claude_code": "claude_code",
        "claude-code": "claude_code",
    }.get(expected_harness, expected_harness)
    if not isinstance(runtime, dict) or runtime.get("kind") != expected_kind:
        raise GateFailure(f"{expected_kind} runtime/model context is missing")
    spec = root.get("spec") if isinstance(root.get("spec"), dict) else {}
    harness = spec.get("harness") if isinstance(spec, dict) else {}
    if (
        harness.get("model") if isinstance(harness, dict) else None
    ) != expected_model and _unwrap(runtime.get("model_id")) != expected_model:
        raise GateFailure(f"{expected_kind} model context is missing")
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
    source: Mapping[str, str],
    origin: str,
    run_id: str,
) -> dict[str, str]:
    """Build Playwright's environment without provider credentials."""

    env = clean_environment(source)
    env.update(
        {
            "MCP_PAL_LIVE_UI_BASE_URL": origin,
            "MCP_PAL_LIVE_EXECUTION_ID": run_id,
        }
    )
    return env


def run_playwright(
    playwright: Path, cwd: Path, env: Mapping[str, str], target: Path | None = None
) -> None:
    try:
        result = subprocess.run(
            [
                str(playwright),
                "test",
                *(
                    ("--config", str(target.parent / "playwright.config.cjs"))
                    if target
                    else ()
                ),
                str(target or "e2e/live-opencode.spec.ts"),
            ],
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


def _wait_for_links(
    child: Child, origin: str
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
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
    process_timeout: float = PROCESS_TIMEOUT,
) -> int:
    if not math.isfinite(process_timeout) or process_timeout <= 0:
        raise GateFailure("--process-timeout must be a positive finite number")
    if (release_dir is None) != (version is None):
        raise GateFailure("--release-dir and --version must be provided together")
    selected_ui = Path(ui_dir).expanduser().resolve()
    original_env = dict(os.environ)
    provider_text = original_env.get("MCP_PAL_LIVE_PROVIDERS", "opencode,codex")
    providers = tuple(
        item.strip().lower() for item in provider_text.split(",") if item.strip()
    )
    if not providers or any(item not in {"opencode", "codex"} for item in providers):
        raise GateFailure("MCP_PAL_LIVE_PROVIDERS must contain opencode and/or codex")
    install_env = clean_environment(original_env)
    if not (selected_ui.is_dir() and (selected_ui / "package.json").is_file()):
        raise GateFailure("UI directory or package.json is unavailable")
    playwright = _check_browser_prerequisites(selected_ui, install_env)
    uv = shutil.which("uv")
    if uv is None:
        raise GateFailure("uv is required for the isolated live gate")
    env_file = ROOT.parent / "mcp-pal" / ".env"
    if not env_file.is_file():
        raise GateFailure("explicit OpenCode env file is unavailable")
    dotenv_file_values = _dotenv_values(env_file)
    api_key = os.environ.get("OPENCODE_API_KEY", "").strip()
    selected_opencode_model = original_env.get(
        "MCP_PAL_LIVE_OPENCODE_MODEL", DEFAULT_MODEL
    )
    if "opencode" in providers:
        if not selected_opencode_model.startswith("opencode/"):
            raise GateFailure(
                "the live UI gate supports OpenCode models with the opencode/ provider prefix"
            )
        if not api_key and not dotenv_file_values.get("OPENCODE_API_KEY", "").strip():
            raise GateFailure(
                "OPENCODE_API_KEY is not set in the selected environment or explicit env file"
            )
        if shutil.which("opencode") is None:
            raise GateFailure("OpenCode executable is not installed")
    if "codex" in providers:
        if shutil.which("codex") is None:
            raise GateFailure("Codex executable is not installed")
        codex_home = Path(original_env.get("CODEX_HOME", Path.home() / ".codex"))
        if (
            not os.environ.get("OPENAI_API_KEY", "").strip()
            and not dotenv_file_values.get("OPENAI_API_KEY", "").strip()
            and not (codex_home / "auth.json").is_file()
        ):
            raise GateFailure(
                "Codex credentials are unavailable: set OPENAI_API_KEY in the environment or explicit env file, or log in to Codex"
            )

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
                timeout=process_timeout,
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
                timeout=process_timeout,
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
        model = selected_opencode_model
        live_env = dict(isolated_env)
        for variable in (
            "MCP_PAL_RUN_LIVE_UI",
            "MCP_PAL_RUN_LIVE_OPENCODE",
            "MCP_PAL_LIVE_PROVIDERS",
            "MCP_PAL_LIVE_OPENCODE_MODEL",
            "MCP_PAL_LIVE_CODEX_MODEL",
        ):
            live_env.pop(variable, None)
        live_env.update(
            {
                # The key is needed by the CLI-launched pytest child. It is
                # explicitly removed from the Playwright environment below.
                "OPENCODE_API_KEY": api_key
                or dotenv_file_values.get("OPENCODE_API_KEY", ""),
            }
        )
        if "opencode" in providers:
            _run_streaming(
                [
                    str(project_python),
                    "sdk/examples/live_agent_loop.py",
                    "--model",
                    model,
                ],
                cwd=repo,
                env=live_env,
                timeout=process_timeout,
                label="normal-python-live-agent",
                output_path=temp_path / "live-agent-loop.log",
            )
        codex_model = original_env.get("MCP_PAL_LIVE_CODEX_MODEL", "gpt-5.6-sol")
        command = cli_test_command(
            executable,
            project_python,
            database,
            port,
            env_file,
            opencode_model=model,
            codex_model=codex_model,
            providers=providers,
        )
        print(
            "live-ui-gate: launching live pytest targets for the selected providers",
            flush=True,
        )
        child = Child(command, repo, live_env)
        origin = f"http://127.0.0.1:{port}"
        history_url, direct_urls, run_ids = _wait_for_links(child, origin)
        if len(run_ids) != len(providers) or len(set(run_ids)) != len(run_ids):
            raise GateFailure(
                "live gate did not produce one distinct execution for each provider"
            )
        history_payload = _json_get(f"{origin}/api/v2/executions")
        expected_models = {"opencode": model, "codex": codex_model}
        reports: dict[str, Any] = {}
        for index, run_id in enumerate(run_ids):
            _assert_execution_in_history(history_payload, run_id)
            expected_model = expected_models[providers[index]]
            report_payload = _json_get(execution_report_url(origin, run_id))
            assert_report(report_payload, expected_model, run_id, providers[index])
            reports[run_id] = report_payload
        aggregate = _post_json(
            f"{origin}/api/v2/evaluations/aggregate",
            {
                "group_by": ["metadata.harness_config"],
                "filters": {"evaluator": "live.shipping.v1"},
            },
        )
        assert_aggregate(aggregate, providers, expected_models)
        feedback_run_id = _pytest_run_id(database, run_ids[0])
        feedback = _json_get(
            f"{origin}/api/v2/feedback/{quote(feedback_run_id, safe='')}"
        )
        feedback_body = feedback.get("feedback") if isinstance(feedback, dict) else None
        if (
            not isinstance(feedback_body, dict)
            or feedback.get("version") != "v2"
            or feedback_body.get("run_id") != feedback_run_id
            or not feedback_body.get("executions")
        ):
            raise GateFailure("feedback API response is malformed")
        feedback_execution_ids = {
            str(item.get("execution_id"))
            for item in feedback_body["executions"]
            if isinstance(item, dict) and item.get("execution_id")
        }
        if not set(run_ids).issubset(feedback_execution_ids):
            raise GateFailure("feedback API response is missing a selected execution")
        secrets = provider_secret_values(providers, original_env, dotenv_file_values)
        for run_id, payload in reports.items():
            _assert_secret_absent(payload, secrets, f"API report {run_id}")
        _assert_secret_absent(aggregate, secrets, "API aggregate")
        _assert_secret_absent(feedback, secrets, "API feedback")
        for route in (history_url, *direct_urls):
            status, content_type, body = _http(route)
            if (
                status != 200
                or "html" not in content_type.lower()
                or b"<html" not in body.lower()
            ):
                raise GateFailure("CLI UI route did not return the bundled SPA")
        ui_probe = _write_ui_probe(temp_path)
        for run_id in run_ids:
            run_playwright(
                playwright,
                selected_ui,
                browser_environment(
                    original_env,
                    origin,
                    run_id,
                ),
                ui_probe,
            )
        interrupt(child)
        if child.process.returncode != 0:
            raise GateFailure(
                f"CLI did not preserve pytest success (exit {child.process.returncode})"
            )
        for run_id in run_ids:
            assert_sqlite_persistence(database, run_id)
            raw_database = database.read_bytes().decode("utf-8", errors="ignore")
            _assert_secret_absent(raw_database, secrets, "SQLite execution store")
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
    parser.add_argument("--process-timeout", type=float, default=PROCESS_TIMEOUT)
    args = parser.parse_args(argv)
    try:
        return check(args.release_dir, args.version, args.ui_dir, args.process_timeout)
    except GateFailure as exc:
        print(f"live-ui-gate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
