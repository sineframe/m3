"""Gate 4 black-box coverage: a real Uvicorn process and real ACP/MCP children.

This deliberately does not use FastAPI's TestClient.  It catches import,
environment, subprocess, and shutdown regressions at the public boundary.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http(base: str, path: str, method: str = "GET", body: dict | None = None) -> dict:
    request = Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def _wait(base: str, path: str, predicate, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = _http(base, path)
            if predicate(last):
                return last
        except (OSError, HTTPError):
            pass
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}: {last}")


@pytest.mark.skipif(os.name == "nt", reason="POSIX subprocess cleanup assertions")
def test_gate4_real_uvicorn_protocol_full_run_and_cleanup(tmp_path):
    port = _port()
    db = tmp_path / "gate4.sqlite"
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(db)
    env["ANTHROPIC_API_KEY"] = ""
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    before = {str(path) for path in Path(tempfile.gettempdir()).glob("mcp-pal-acp-*")}
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mcp_pal.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}/api/v1"
    try:
        _wait(base, "/health", lambda value: "status" in value)
        harness = _http(base, "/harness-profiles", "POST", {
            "name": "reference",
            "description": "keyless Gate 4 fixture",
            "trusted_unsandboxed": True,
            "manifest": {
                "command": sys.executable,
                "args": ["-m", "mcp_pal.bridge.reference", "--target", sys.executable, "--target-args-json", "[\"-m\",\"mcp_pal.fixtures.structured_cli\"]"],
                "env": {},
            },
        })
        echo = _http(base, "/profiles", "POST", {
            "name": "echo",
            "mcp_json": {"mcpServers": {"echo": {"command": sys.executable, "args": ["-m", "mcp_pal.fixtures.echo_server"]}}},
        })
        profile_id = harness["id"]
        protocol_job = _http(base, f"/harness-profiles/{profile_id}/probe?kind=protocol", "POST")
        protocol = _wait(base, f"/harness-profiles/{profile_id}/probes", lambda value: value and value[0]["id"] == protocol_job["id"] and value[0]["status"] not in {"queued", "running"})[0]
        assert protocol["status"] == "verified"
        full_job = _http(base, f"/harness-profiles/{profile_id}/probe?kind=full&transport=stdio", "POST")
        full = _wait(base, f"/harness-profiles/{profile_id}/probes", lambda value: value and value[0]["id"] == full_job["id"] and value[0]["status"] not in {"queued", "running"})[0]
        assert full["status"] == "verified"
        nonce = full["evidence"]["nonce"]
        probe_mode = full["mode_id"]
        probe_config = full["session_config"]
        run = _http(base, "/runs", "POST", {
            "harness": "acp", "harness_revision_id": harness["current_revision_id"], "model": "agent-default", "tool_mode": "agent_default",
            "prompt": "Call echo with nonce-123", "expected_output": "nonce-123", "profile_revision_id": echo["current_revision_id"], "enabled_server": "echo",
            "agent_mode_id": probe_mode, "session_config": probe_config,
        })
        state = _wait(base, f"/runs/{run['id']}", lambda value: value["status"] not in {"queued", "running"})
        report = _http(base, f"/runs/{run['id']}/report")
        assert state["status"] == "completed"
        assert state["harness"] == "acp" and state["model"] == "agent-default"
        assert state["final_output"] == "nonce-123"
        assert report["assertions"]["mcp"]["status"] == "passed"
        assert report["trace"]["schema"] == "acp.v1"
        methods = {frame.get("payload", {}).get("method") for frame in report["trace"]["acp_protocol_events"]}
        assert {"initialize", "session/new", "session/prompt", "session/update"} <= methods
        assert any(frame.get("payload", {}).get("result", {}).get("stopReason") == "end_turn" for frame in report["trace"]["acp_protocol_events"])
        assert any(frame.get("payload", {}).get("method") == "session/set_config_option" for frame in report["trace"]["acp_protocol_events"])
        call = report["trace"]["mcp_calls"][0]
        assert call["arguments"] == {"text": "nonce-123"}
        assert call["result"]["content"] == [{"type": "text", "text": "nonce-123"}]
        assert call["server_latency_ms"] >= 0
        assert {"thought", "tool", "message"} <= {span.get("type") for span in report["trace"]["spans"]}
        persisted = json.dumps(report, sort_keys=True)
        assert "TEAM_OPENAI_KEY" not in persisted and "sk-test" not in persisted
        snapshot = report["run"]["harness_snapshot"]
        assert snapshot["revision_id"] == harness["current_revision_id"]
        assert snapshot["manifest"]["command"] == sys.executable
        assert report["profile_revision"]["id"] == echo["current_revision_id"]
        assert snapshot["verification"]["full_probe_id"] == full["id"]
        assert snapshot["verification"]["full_probe_dimensions"] == {"transport": "stdio", "mode_id": probe_mode, "session_config": probe_config}
        assert report["trace"]["result_metadata"]["full_probe_id"] == full["id"]
        assert state["effective_model"] == probe_config["model"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    after = {str(path) for path in Path(tempfile.gettempdir()).glob("mcp-pal-acp-*")}
    assert after <= before


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group assertions")
def test_gate4_uvicorn_cancellation_kills_agent_and_mcp_child_then_worker_recovers(tmp_path):
    port = _port()
    db = tmp_path / "cancel.sqlite"
    child_pid = tmp_path / "mcp.pid"
    agent_pid = tmp_path / "agent.pid"
    child = tmp_path / "hanging_mcp.py"
    child.write_text(
        "#!/usr/bin/env python3\nimport os,time\n"
        f"open({str(child_pid)!r}, 'w').write(str(os.getpid()))\n"
        "while True: time.sleep(1)\n"
    )
    agent = tmp_path / "hanging_agent.py"
    agent.write_text(
        "#!/usr/bin/env python3\nimport json,os,subprocess,sys,time\n"
        f"open({str(agent_pid)!r}, 'w').write(str(os.getpid()))\n"
        "child=None\n"
        "def send(x): print(json.dumps(x),flush=True)\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line); m=r.get('method'); p=r.get('params') or {}\n"
        " if m=='initialize': send({'jsonrpc':'2.0','id':r['id'],'result':{'protocolVersion':1}})\n"
        " elif m=='session/new':\n"
        "  c=p['mcpServers'][0]; child=subprocess.Popen([sys.executable,sys.argv[1]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)\n"
        "  send({'jsonrpc':'2.0','id':r['id'],'result':{'sessionId':'hanging'}})\n"
        " elif m=='session/prompt':\n"
        "  while True: time.sleep(1)\n"
    )
    agent.chmod(0o755)
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(db)
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mcp_pal.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    base = f"http://127.0.0.1:{port}/api/v1"

    def dead(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            pid = int(path.read_text())
            os.kill(pid, 0)
            # A killed child can remain as a short-lived zombie until its
            # parent is reaped; it is no longer an alive worker process.
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False).stdout.strip()
            if state.startswith("Z") or not state:
                return True
        except (ProcessLookupError, PermissionError):
            return True
        return False

    try:
        _wait(base, "/health", lambda value: "status" in value)
        hanging = _http(base, "/harness-profiles", "POST", {"name": "hanging", "trusted_unsandboxed": True, "manifest": {"command": str(agent), "args": [str(child)], "env": {}}})
        healthy = _http(base, "/harness-profiles", "POST", {"name": "healthy", "trusted_unsandboxed": True, "manifest": {"command": sys.executable, "args": ["-m", "mcp_pal.bridge.reference", "--target", sys.executable, "--target-args-json", "[\"-m\",\"mcp_pal.fixtures.structured_cli\"]"], "env": {}}})
        echo = _http(base, "/profiles", "POST", {"name": "hang-echo", "mcp_json": {"mcpServers": {"echo": {"command": str(child)}}}})
        healthy_echo = _http(base, "/profiles", "POST", {"name": "healthy-echo", "mcp_json": {"mcpServers": {"echo": {"command": sys.executable, "args": ["-m", "mcp_pal.fixtures.echo_server"]}}}})
        body = {"harness": "acp", "harness_revision_id": hanging["current_revision_id"], "model": "agent-default", "tool_mode": "agent_default", "prompt": "hang", "expected_output": "never", "profile_revision_id": echo["current_revision_id"], "enabled_server": "echo"}
        run = _http(base, "/runs", "POST", body)
        _wait(base, f"/runs/{run['id']}", lambda value: value["status"] == "running")
        _wait(base, f"/runs/{run['id']}", lambda value: agent_pid.exists() and child_pid.exists())
        cancelled = _http(base, f"/runs/{run['id']}/cancel", "POST")
        assert cancelled["status"] in {"cancelled", "running"}
        state = _wait(base, f"/runs/{run['id']}", lambda value: value["status"] not in {"queued", "running"}, timeout=10)
        assert state["status"] == "cancelled"
        report = _http(base, f"/runs/{run['id']}/report")
        assert report["trace"]["schema"] == "acp.v1" and report["trace"]["capture_status"] == "partial"
        assert any(event.get("payload", {}).get("method") == "session/cancel" for event in report["trace"]["protocol_events"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (dead(agent_pid) and dead(child_pid)):
            time.sleep(0.05)
        assert dead(agent_pid) and dead(child_pid)
        healthy_body = {**body, "harness_revision_id": healthy["current_revision_id"], "profile_revision_id": healthy_echo["current_revision_id"], "prompt": "Call echo with nonce-123", "expected_output": "nonce-123"}
        good = _http(base, "/runs", "POST", healthy_body)
        final = _wait(base, f"/runs/{good['id']}", lambda value: value["status"] not in {"queued", "running"}, timeout=15)
        assert final["status"] == "completed" and final["final_output"] == "nonce-123"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
