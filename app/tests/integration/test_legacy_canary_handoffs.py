"""Deterministic legacy harness handoff and canary regressions."""

import asyncio
import json
import os
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen

import pytest

from mcp_pal.harness.acp import AcpHarnessRunner, _Client
from mcp_pal.harness.base import AcpRunSpec, RunSpec
from mcp_pal_app.harness_claude_cli import ClaudeCodeRunner
from mcp_pal_app.harness_opencode_cli import OpenCodeRunner
from mcp_pal.transport.capture_proxy import write_stdio_handoff
from mcp_pal.transport.http_proxy import McpHttpProxy


def _executable(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_handoff_has_safe_baseline_and_no_ambient_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_PAL_HANDOFF_REF", "resolved-handoff-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-provider-secret")
    path = tmp_path / "handoff.json"
    canaries = write_stdio_handoff(
        path,
        {"ANTHROPIC_API_KEY": "literal-handoff-secret", "DEBUG": "1", "REF_VALUE": "${MCP_PAL_HANDOFF_REF}"},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["environment"]["PATH"]
    assert payload["environment"]["ANTHROPIC_API_KEY"] == "literal-handoff-secret"
    assert payload["environment"]["DEBUG"] == "1"
    assert payload["environment"]["REF_VALUE"] == "resolved-handoff-secret"
    assert "OPENAI_API_KEY" not in payload["environment"]
    assert set(payload["canaries"]) == canaries
    assert "literal-handoff-secret" in canaries
    assert "1" not in canaries
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for name in ("", "BAD=NAME", "BAD\x00NAME"):
        invalid_path = tmp_path / ("invalid-" + str(len(name)) + ".json")
        with pytest.raises(ValueError, match="environment name"):
            write_stdio_handoff(invalid_path, {name: "value"})
        assert not invalid_path.exists()


@pytest.mark.asyncio
async def test_http_proxy_registers_reference_under_non_sensitive_header(tmp_path, monkeypatch):
    monkeypatch.setenv("NON_SENSITIVE_REF", "non-sensitive-resolved-secret")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": self.headers.get("X-Vendor", "")}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=upstream.serve_forever, daemon=True).start()
    capture = tmp_path / "capture.jsonl"
    canaries: set[str] = set()
    proxy = McpHttpProxy(
        upstream_url=f"http://127.0.0.1:{upstream.server_port}/mcp",
        configured_headers={"X-Vendor": "Bearer ${NON_SENSITIVE_REF}"},
        transport="streamable_http",
        capture_path=str(capture),
        baseline_ns=0,
        allow_private=True,
        secrets=canaries,
    )
    try:
        endpoint = await proxy.start()
        request = Request(endpoint, data=b'{"jsonrpc":"2.0","id":1}', headers={"Content-Type": "application/json"})
        with await asyncio.to_thread(urlopen, request) as response:
            assert "non-sensitive-resolved-secret" in response.read().decode()
    finally:
        await proxy.stop()
        upstream.shutdown()
        upstream.server_close()
    assert "non-sensitive-resolved-secret" in canaries
    assert b"non-sensitive-resolved-secret" not in capture.read_bytes()
    monkeypatch.delenv("MISSING_PROXY_REF", raising=False)
    missing_capture = tmp_path / "missing-capture.jsonl"
    with pytest.raises(ValueError, match="^MCP environment reference unavailable$"):
        McpHttpProxy(
            upstream_url="https://example.test/mcp",
            configured_headers={"X-Vendor": "Bearer ${MISSING_PROXY_REF}"},
            transport="streamable_http",
            capture_path=str(missing_capture),
            baseline_ns=0,
        )
    assert not missing_capture.exists()


@pytest.mark.asyncio
async def test_acp_client_observes_canaries_added_after_construction():
    canaries: set[str] = set()
    observed: list[object] = []
    client = _Client(
        [],
        [],
        callback=lambda payload, *_args: observed.append(payload),
        secrets=canaries,
    )
    canaries.add("late-acp-canary")

    await client.session_update("session", {"text": "late-acp-canary"})

    assert observed == [{"text": "[REDACTED]"}]


def test_claude_legacy_config_handoff_and_result_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_HANDOFF_REF", "claude-resolved-secret")
    marker = tmp_path / "claude-config.json"
    claude = _executable(tmp_path / "claude.py", f'''\
import json, os, sys
config_path = sys.argv[sys.argv.index("--mcp-config") + 1]
config = json.load(open(config_path))
json.dump(config, open({str(marker)!r}, "w"))
server = config["mcpServers"]["x"]
assert server["env"] == {{}}
assert "--env-file" in server["args"]
assert os.path.exists(server["args"][server["args"].index("--env-file") + 1])
print(json.dumps({{"type":"result","result":"literal-claude-secret claude-resolved-secret"}}), flush=True)
sys.stderr.write("literal-claude-secret claude-resolved-secret\\n")
''')
    spec = RunSpec(
        "prompt", "model", {"mcpServers": {"x": {"command": "echo", "env": {"ANTHROPIC_API_KEY": "literal-claude-secret", "R": "${CLAUDE_HANDOFF_REF}"}}}}, "x", timeout_seconds=3
    )
    result = asyncio.run(ClaudeCodeRunner(claude).run(spec))
    evidence = json.dumps((result.events, result.event_records, result.final_text, result.stderr))
    assert result.status == "completed"
    assert "literal-claude-secret" not in evidence
    assert "claude-resolved-secret" not in evidence
    config = json.loads(marker.read_text())
    assert config["mcpServers"]["x"]["env"] == {}
    handoff = config["mcpServers"]["x"]["args"][config["mcpServers"]["x"]["args"].index("--env-file") + 1]
    assert not os.path.exists(handoff)


def test_opencode_legacy_config_handoff_and_result_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_HANDOFF_REF", "opencode-resolved-secret")
    marker = tmp_path / "opencode-config.json"
    opencode = _executable(tmp_path / "opencode.py", f'''\
import json, os
config = json.load(open(os.environ["OPENCODE_CONFIG"]))
json.dump(config, open({str(marker)!r}, "w"))
server = config["mcp"]["x"]
assert server["environment"] == {{}}
assert "--env-file" in server["command"]
assert os.path.exists(server["command"][server["command"].index("--env-file") + 1])
print(json.dumps({{"type":"step_start","sessionID":"s","part":{{"type":"step-start"}}}}))
print(json.dumps({{"type":"text","sessionID":"s","part":{{"type":"text","text":"literal-opencode-secret opencode-resolved-secret"}}}}))
print(json.dumps({{"type":"step_finish","sessionID":"s","part":{{"type":"step-finish"}}}}))
''')
    spec = RunSpec(
        "prompt", "model", {"mcpServers": {"x": {"command": "echo", "env": {"ANTHROPIC_API_KEY": "literal-opencode-secret", "R": "${OPENCODE_HANDOFF_REF}"}}}}, "x", timeout_seconds=3
    )
    result = asyncio.run(OpenCodeRunner(opencode).run(spec))
    evidence = json.dumps((result.events, result.event_records, result.final_text, result.stderr))
    assert result.status == "completed"
    assert "literal-opencode-secret" not in evidence
    assert "opencode-resolved-secret" not in evidence
    config = json.loads(marker.read_text())
    assert config["mcp"]["x"]["environment"] == {}
    handoff = config["mcp"]["x"]["command"][config["mcp"]["x"]["command"].index("--env-file") + 1]
    assert not os.path.exists(handoff)


def test_acp_legacy_config_handoff_and_event_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("ACP_HANDOFF_REF", "acp-resolved-secret")
    marker = tmp_path / "acp-config.json"
    agent = _executable(tmp_path / "agent.py", f'''\
import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        print(json.dumps({{"jsonrpc":"2.0","id":request["id"],"result":{{"protocolVersion":1}}}}), flush=True)
    elif method == "session/new":
        server = request["params"]["mcpServers"][0]
        json.dump({{"server":server}}, open({str(marker)!r}, "w"))
        assert server["env"] == []
        assert "--env-file" in server["args"]
        assert os.path.exists(server["args"][server["args"].index("--env-file") + 1])
        print(json.dumps({{"jsonrpc":"2.0","id":request["id"],"result":{{"sessionId":"s"}}}}), flush=True)
    elif method == "session/prompt":
        print(json.dumps({{"jsonrpc":"2.0","id":request["id"],"result":{{"stopReason":"end_turn"}}}}), flush=True)
''')
    spec = AcpRunSpec(
        "prompt", "model", {"mcpServers": {"x": {"command": "echo", "env": {"ANTHROPIC_API_KEY": "literal-acp-secret", "R": "${ACP_HANDOFF_REF}"}}}}, "x", {"command": agent}, timeout_seconds=3
    )
    result = asyncio.run(AcpHarnessRunner().run(spec))
    evidence = json.dumps((result.event_records, result.protocol_events, result.final_text, result.stderr))
    assert result.status == "completed"
    assert "literal-acp-secret" not in evidence
    assert "acp-resolved-secret" not in evidence
    server = json.loads(marker.read_text())["server"]
    assert server["env"] == []
    handoff = server["args"][server["args"].index("--env-file") + 1]
    assert not os.path.exists(handoff)
