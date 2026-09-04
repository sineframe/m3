"""Manual live OpenCode-to-browser merge gate.

The gate deliberately has hard prerequisites and performs exactly one live
provider test. It is kept as an explicit script so it is easy to audit and
never runs as part of the normal test or CI commands.
"""

from __future__ import annotations

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
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT.parent / "mcppal-ui"
TARGET = "sdk/examples/tests/test_live_opencode.py::test_live_opencode_uses_shipping_quote_and_captures_wire_evidence"
DEFAULT_MODEL = "opencode/big-pickle"
READY_TIMEOUT = 180.0
PLAYWRIGHT_TIMEOUT = 120.0


class GateFailure(RuntimeError):
    """A safe, user-facing gate failure (never contains secret values)."""


def redact(text: str, env: dict[str, str] | None = None) -> str:
    """Redact provider credentials and other secret-like environment values."""
    source = os.environ if env is None else env
    for key, value in source.items():
        if (
            value
            and len(value) >= 4
            and ("KEY" in key or "TOKEN" in key or "SECRET" in key or "PASSWORD" in key)
        ):
            text = text.replace(value, "[REDACTED]")
    return text


def free_loopback_ports() -> tuple[int, int]:
    """Reserve two distinct currently-free loopback port numbers."""
    sockets: list[socket.socket] = []
    try:
        for _ in range(2):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        ports = (
            int(sockets[0].getsockname()[1]),
            int(sockets[1].getsockname()[1]),
        )
        if ports[0] == ports[1]:
            raise GateFailure("could not select distinct loopback ports")
        return ports
    except OSError as exc:
        raise GateFailure("could not select free loopback ports") from exc
    finally:
        for sock in sockets:
            sock.close()


def _run_quiet(command: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateFailure(f"required command unavailable: {command[0]}") from exc
    if result.returncode != 0:
        raise GateFailure(f"required command failed: {command[0]}")
    return result.stdout + result.stderr


def check_prerequisites(ui_dir: Path = UI_ROOT) -> None:
    """Fail early unless every dependency needed by the manual gate is usable."""
    if not os.environ.get("OPENCODE_API_KEY", "").strip():
        raise GateFailure("OPENCODE_API_KEY is not set in the selected environment")
    opencode = shutil.which("opencode")
    if opencode is None:
        raise GateFailure("OpenCode executable is not installed")
    top_help = _run_quiet([opencode, "--help"]).lower()
    if "run" not in top_help or "--model" not in top_help or "--pure" not in top_help:
        raise GateFailure("OpenCode executable does not advertise required flags")
    run_help = _run_quiet([opencode, "run", "--help"]).lower()
    if not all(flag in run_help for flag in ("--model", "--format", "--thinking", "--pure")):
        raise GateFailure("OpenCode run command does not advertise required flags")
    for executable in ("node", "npm"):
        if shutil.which(executable) is None:
            raise GateFailure(f"{executable} is not installed")
        version = _run_quiet([executable, "--version"])
        if executable == "node":
            match = re.search(r"v(\d+)", version)
            if match is None or int(match.group(1)) < 24:
                raise GateFailure("UI requires Node.js 24 or newer")
    if not (ui_dir.is_dir() and (ui_dir / "package.json").is_file()):
        raise GateFailure("UI directory or package.json is unavailable")
    playwright = ui_dir / "node_modules" / ".bin" / "playwright"
    if not playwright.is_file():
        raise GateFailure("UI Playwright dependency is unavailable")
    _run_quiet([str(playwright), "--version"], ui_dir)
    node_probe = (
        "const p=require('playwright'); "
        "const fs=require('fs'); "
        "if(!fs.existsSync(p.chromium.executablePath())) process.exit(3)"
    )
    _run_quiet(["node", "-e", node_probe], ui_dir)


class Child:
    """A process-group child with bounded, redacted diagnostic output."""

    def __init__(self, name: str, command: list[str], env: dict[str, str], cwd: Path) -> None:
        self.name = name
        self.env = env
        self.lines: deque[str] = deque(maxlen=50)
        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": env,
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
        try:
            self.process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            raise GateFailure(f"could not start {name}") from exc
        import threading

        threading.Thread(target=self._capture, daemon=True).start()

    def _capture(self) -> None:
        if self.process.stdout is None:
            return
        try:
            for line in self.process.stdout:
                clean = redact(line.rstrip("\r\n"), self.env)
                self.lines.append(clean)
                print(f"[{self.name}] {clean}", flush=True)
        except (OSError, ValueError):
            return

    def alive(self) -> bool:
        return self.process.poll() is None


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


def terminate(child: Child | None) -> None:
    """TERM a process group, bounded-wait, KILL if needed, and reap it."""
    if child is None:
        return
    process = child.process
    if process.poll() is None:
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
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                _windows_kill_tree(process)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass


def interrupt(child: Child | None) -> None:
    """Request graceful Ctrl-C shutdown, then use the bounded group cleanup."""
    if child is None or child.process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(child.process.pid, signal.SIGINT)
        else:
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)
            child.process.send_signal(ctrl_break)
        child.process.wait(timeout=10)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        terminate(child)


def ready(url: str) -> bool:
    try:
        with urlopen(Request(url, method="GET"), timeout=1) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def wait_ready(child: Child, url: str, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not child.alive():
            return False
        if ready(url):
            return True
        time.sleep(0.1)
    return False


def _json_get(url: str) -> Any:
    try:
        with urlopen(Request(url, method="GET"), timeout=5) as response:
            if not (200 <= response.status < 300):
                raise GateFailure(f"API returned HTTP {response.status}")
            body = response.read(2_000_000)
    except (OSError, URLError) as exc:
        raise GateFailure("could not query the local API") from exc
    try:
        return json.loads(body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GateFailure("local API returned invalid JSON") from exc


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "state" in value:
        if value.get("state") == "observed" and "value" in value:
            return value["value"]
        return None
    return value


def assert_report(
    envelope: Any,
    expected_model: str,
    expected_execution_id: str | None = None,
) -> str:
    """Validate the shipping-quote contract and return the execution ID."""
    root = envelope if isinstance(envelope, dict) else {}
    report = root.get("report") if isinstance(root.get("report"), dict) else root
    snapshot = report.get("snapshot") if isinstance(report, dict) else None
    if not isinstance(snapshot, dict):
        raise GateFailure("report snapshot is missing")
    if snapshot.get("lifecycle") != "finished" or snapshot.get("outcome") != "completed":
        raise GateFailure("live execution did not finish successfully")
    trace = root.get("trace") if isinstance(root.get("trace"), dict) else {}
    runtime = trace.get("runtime") if isinstance(trace, dict) else {}
    if not isinstance(runtime, dict) or runtime.get("kind") != "opencode":
        raise GateFailure("OpenCode runtime/model context is missing")
    spec = root.get("spec") if isinstance(root.get("spec"), dict) else {}
    harness = spec.get("harness") if isinstance(spec, dict) else {}
    harness_model = harness.get("model") if isinstance(harness, dict) else None
    runtime_model = _unwrap(runtime.get("model_id"))
    if harness_model != expected_model and runtime_model != expected_model:
        raise GateFailure("OpenCode runtime/model context is missing")
    timeline = trace.get("timeline") if isinstance(trace, dict) else []
    if not isinstance(timeline, list):
        raise GateFailure("trace timeline is missing")
    tool_calls = [item for item in timeline if isinstance(item, dict) and item.get("kind") == "tool_call"]
    if len(tool_calls) != 1:
        raise GateFailure("expected exactly one tool-call entry")
    candidate = tool_calls[0]
    if _unwrap(candidate.get("tool")) != "shipping_quote":
        raise GateFailure("expected one shipping_quote tool call")
    arguments = _unwrap(candidate.get("arguments"))
    if not isinstance(arguments, dict) or arguments != {"weight_kg": 2, "zone": "local"}:
        raise GateFailure("expected exactly one shipping_quote call with required arguments")
    if _unwrap(candidate.get("tool_status")) != "success":
        raise GateFailure("shipping_quote call was not successful")
    result = _unwrap(candidate.get("result"))
    structured = result.get("structured_content") if isinstance(result, dict) else None
    structured = _unwrap(structured)
    if not isinstance(structured, dict) or structured.get("currency") != "USD":
        raise GateFailure("shipping_quote result is not structured as USD")
    wire = candidate.get("wire")
    if not isinstance(wire, dict) or wire.get("state") != "observed":
        raise GateFailure("correlated wire evidence was not observed")
    execution_id = snapshot.get("execution_id") or root.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise GateFailure("execution ID is missing")
    if expected_execution_id is not None and execution_id != expected_execution_id:
        raise GateFailure("report execution ID does not match history")
    return execution_id


def _diagnostic(name: str, child: Child | None, reason: str) -> None:
    print(f"live-ui-gate: {name} failed ({reason})", file=sys.stderr)
    if child is not None and child.lines:
        print(f"live-ui-gate: last {len(child.lines)} {name} lines:", file=sys.stderr)
        for line in child.lines:
            print(f"[{name}] {redact(line, child.env)}", file=sys.stderr)


def _write_diagnostics(path: Path, child: Child | None, reason: str) -> None:
    lines = [f"reason: {reason}"]
    if child is not None:
        lines.extend(f"[cli] {line}" for line in child.lines)
    path.write_text(redact("\n".join(lines) + "\n"), encoding="utf-8")


def run_playwright(playwright: Path, cwd: Path, env: dict[str, str]) -> int:
    """Run the browser assertion with an outer lifecycle timeout."""

    try:
        result = subprocess.run(
            [str(playwright), "test", "e2e/live-opencode.spec.ts"],
            cwd=cwd,
            env=env,
            timeout=PLAYWRIGHT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateFailure("Playwright live UI assertion timed out") from exc
    return int(result.returncode)


def run() -> int:
    cli: Child | None = None
    temp_path: Path | None = None
    failure_reason: str | None = None
    try:
        selected_ui = Path(os.environ.get("MCP_PAL_UI_DIR", str(UI_ROOT))).expanduser().resolve()
        check_prerequisites(selected_ui)
        api_port, ui_port = free_loopback_ports()
        temp_path = Path(tempfile.mkdtemp(prefix="mcp-pal-live-ui-"))
        database = temp_path / "executions.sqlite"
        env = os.environ.copy()
        env["MCP_PAL_RUN_LIVE_OPENCODE"] = "1"
        env["MCP_PAL_LIVE_OPENCODE_MODEL"] = os.environ.get(
            "MCP_PAL_LIVE_OPENCODE_MODEL", DEFAULT_MODEL
        )
        command = [
            shutil.which("mcp-pal") or "mcp-pal",
            "test",
            "--ui",
            "--results-db",
            str(database),
            "--api-port",
            str(api_port),
            "--ui-port",
            str(ui_port),
            "--ui-dir",
            str(selected_ui),
            "--",
            "-q",
            TARGET,
        ]
        print("live-ui-gate: launching one live OpenCode execution", flush=True)
        api_url = f"http://127.0.0.1:{api_port}"
        # The CLI owns API/UI descendants; keep its output bounded and sanitized.
        cli = Child("cli", command, env, ROOT)
        deadline = time.monotonic() + READY_TIMEOUT
        history_url: str | None = None
        while time.monotonic() < deadline and cli.alive():
            for line in reversed(cli.lines):
                if line.startswith("mcp-pal history: "):
                    history_url = line.split("mcp-pal history: ", 1)[1].strip()
                    break
            if history_url:
                break
            time.sleep(0.2)
        if not history_url:
            _diagnostic("cli", cli, "history readiness timeout or early exit")
            raise GateFailure("CLI did not become history-ready")
        # Query the same API origin announced by the CLI after it has started.
        page = _json_get(api_url + "/api/v2/executions")
        page_obj = page.get("page", page) if isinstance(page, dict) else {}
        items = page_obj.get("items", []) if isinstance(page_obj, dict) else []
        if not isinstance(items, list) or len(items) != 1:
            raise GateFailure("expected exactly one persisted execution")
        item = items[0] if isinstance(items[0], dict) else {}
        snapshot = item.get("snapshot", item)
        execution_id = snapshot.get("execution_id") if isinstance(snapshot, dict) else None
        if not isinstance(execution_id, str) or not execution_id:
            raise GateFailure("execution ID is missing from history")
        report = _json_get(api_url + f"/api/v2/executions/{execution_id}/report")
        assert_report(
            report,
            env["MCP_PAL_LIVE_OPENCODE_MODEL"],
            expected_execution_id=execution_id,
        )
        browser_env = {
            key: value
            for key, value in env.items()
            if key in {"PATH", "HOME", "USER", "TMPDIR", "TEMP", "TMP", "SystemRoot"}
        }
        browser_env.update(
            {
                "MCP_PAL_LIVE_UI_BASE_URL": history_url.rsplit("/history", 1)[0],
                "MCP_PAL_LIVE_EXECUTION_ID": execution_id,
                "MCP_PAL_LIVE_MODEL": env["MCP_PAL_LIVE_OPENCODE_MODEL"],
            }
        )
        playwright = selected_ui / "node_modules" / ".bin" / "playwright"
        if run_playwright(playwright, selected_ui, browser_env) != 0:
            raise GateFailure("Playwright live UI assertion failed")
        interrupt(cli)
        cli_code = cli.process.returncode
        if cli_code != 0:
            raise GateFailure("CLI did not exit cleanly after the browser test")
        shutil.rmtree(temp_path)
        temp_path = None
        return 0
    except (GateFailure, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            failure_reason = "interrupted"
            print("live-ui-gate: interrupted", file=sys.stderr)
        else:
            failure_reason = str(exc)
            print(f"live-ui-gate: {failure_reason}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        failure_reason = "operational failure"
        print(f"live-ui-gate: {failure_reason}", file=sys.stderr)
        return 2
    finally:
        terminate(cli)
        if temp_path is not None:
            if failure_reason is not None:
                _write_diagnostics(temp_path / "gate-diagnostics.txt", cli, failure_reason)
            print(f"live-ui-gate: diagnostics retained at {temp_path}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(run())
