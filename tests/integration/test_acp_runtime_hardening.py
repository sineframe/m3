"""Focused subprocess regressions for ACP runtime safety guarantees."""
import asyncio
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mcp_pal.harness.acp import AcpHarnessRunner, protocol_probe
from mcp_pal.harness.base import AcpRunSpec


def executable(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def spec(agent: str, **kwargs) -> AcpRunSpec:
    return AcpRunSpec(
        "prompt", "agent-default", {"mcpServers": {"x": {"command": "echo"}}}, "x", {"command": agent}, timeout_seconds=kwargs.pop("timeout_seconds", .2), **kwargs
    )


def lifecycle_agent(path: Path, phase: str) -> str:
    return executable(path, f'''
import json,sys,time
for line in sys.stdin:
 r=json.loads(line); m=r.get("method")
 if m=="initialize":
  {'time.sleep(10)' if phase == 'initialize' else 'print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":1}}),flush=True)'}
 elif m=="session/new":
  {'time.sleep(10)' if phase == 'session/new' else 'print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"sessionId":"s","modes":{"currentModeId":"default","availableModes":[{"id":"default","name":"Default"}]},"configOptions":[{"type":"boolean","id":"known","name":"Known","currentValue":False} ]}}),flush=True)'}
 elif m=="session/set_mode" and {phase!r}=="mode": time.sleep(10)
 elif m=="session/set_config_option" and {phase!r}=="config": time.sleep(10)
 elif m=="session/prompt": time.sleep(10)
''')


@pytest.mark.parametrize("phase", ["initialize", "session/new", "mode", "config", "prompt"])
def test_one_global_deadline_covers_every_lifecycle_phase(tmp_path, phase):
    agent = lifecycle_agent(tmp_path / f"{phase.replace('/', '_')}.py", phase)
    run = spec(agent, agent_mode_id="default" if phase == "mode" else None, session_config={"known": True} if phase == "config" else {})
    result = asyncio.run(AcpHarnessRunner().run(run))
    assert result.status == "timed_out"
    assert result.error_code == "timed_out"
    expected_phase = {"mode":"set_session_mode", "config":"set_config_option"}.get(phase, phase)
    assert result.error_phase == expected_phase


def test_stderr_is_drained_bounded_and_captured(tmp_path):
    agent = executable(tmp_path / "stderr.py", '''
import json,sys
sys.stderr.write("X"*300000); sys.stderr.flush()
for line in sys.stdin:
 r=json.loads(line); m=r.get("method")
 if m=="initialize": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":1}}),flush=True)
 elif m=="session/new": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"sessionId":"s"}}),flush=True)
 elif m=="session/prompt": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"stopReason":"end_turn"}}),flush=True)
''')
    result = asyncio.run(AcpHarnessRunner().run(spec(agent, timeout_seconds=3)))
    assert result.status == "completed"
    assert len(result.stderr) == 64 * 1024


def test_acp_timestamps_are_utc_and_monotonic(tmp_path):
    agent = lifecycle_agent(tmp_path / "ok.py", "none")
    # Replace the hanging prompt with a tiny successful implementation.
    Path(agent).write_text(Path(agent).read_text().replace('elif m=="session/prompt": time.sleep(10)', 'elif m=="session/prompt": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"stopReason":"end_turn"}}),flush=True)'))
    result = asyncio.run(AcpHarnessRunner().run(spec(agent, timeout_seconds=3)))
    assert result.status == "completed"
    offsets = []
    for frame in result.event_records:
        assert datetime.fromisoformat(frame["received_at"]).utcoffset() == timezone.utc.utcoffset(None)
        assert datetime.fromisoformat(frame["occurred_at"]).utcoffset() == timezone.utc.utcoffset(None)
        offsets.append(frame["offset_ms"])
    assert offsets == sorted(offsets)


def test_session_env_literals_and_refs_are_delivered_but_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("ACP_REF_SECRET", "resolved-ref-secret")
    marker = tmp_path / "env.json"
    agent = executable(tmp_path / "env.py", f'''
import json,sys
for line in sys.stdin:
 r=json.loads(line); m=r.get("method")
 if m=="initialize": print(json.dumps({{"jsonrpc":"2.0","id":r["id"],"result":{{"protocolVersion":1}}}}),flush=True)
 elif m=="session/new":
  open({str(marker)!r},"w").write(json.dumps(r["params"]["mcpServers"][0]["env"]))
  print(json.dumps({{"jsonrpc":"2.0","id":r["id"],"result":{{"sessionId":"s"}}}}),flush=True)
 elif m=="session/prompt": print(json.dumps({{"jsonrpc":"2.0","id":r["id"],"result":{{"stopReason":"end_turn"}}}}),flush=True)
''')
    run = AcpRunSpec("prompt", "agent-default", {"mcpServers": {"x": {"command":"echo", "env":{"LITERAL":"literal-secret", "REF":"${ACP_REF_SECRET}"}}}}, "x", {"command":agent}, timeout_seconds=3)
    result = asyncio.run(AcpHarnessRunner().run(run))
    delivered = json.loads(marker.read_text())
    assert {entry["name"]: entry["value"] for entry in delivered} == {"LITERAL":"literal-secret", "REF":"resolved-ref-secret"}
    evidence = json.dumps(result.event_records)
    assert "literal-secret" not in evidence and "resolved-ref-secret" not in evidence
    assert {entry["name"] for frame in result.event_records for entry in frame.get("payload",{}).get("params",{}).get("mcpServers", [{}])[0].get("env", []) if isinstance(entry,dict)} == {"LITERAL","REF"}


def test_protocol_events_redact_selected_env_secrets_after_wire_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("WIRE_REF_SECRET", "wire-reference-secret")
    server = executable(tmp_path / "secret_mcp.py", '''
import json,os,sys
for line in sys.stdin:
 r=json.loads(line);m=r.get("method")
 if m=="initialize": out={"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"secret","version":"1"}}
 elif m=="tools/call": out={"content":[{"type":"text","text":"ordinary-wire-result literal-wire-secret "+os.environ.get("WIRE_LITERAL","")+" "+os.environ.get("WIRE_REF","")}],"isError":False}
 else: out={"tools":[{"name":"echo","inputSchema":{"type":"object"}}]} if m=="tools/list" else {}
 print(json.dumps({"jsonrpc":"2.0","id":r.get("id"),"result":out}),flush=True)
''')
    agent = executable(tmp_path / "wire_agent.py", '''
import json,os,subprocess,sys
server=None
def rpc(i,m,p=None):
 q={"jsonrpc":"2.0","id":i,"method":m};
 if p is not None:q["params"]=p
 server.stdin.write((json.dumps(q)+"\\n").encode());server.stdin.flush();return json.loads(server.stdout.readline())
for line in sys.stdin:
 r=json.loads(line);m=r.get("method")
 if m=="initialize": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":1}}),flush=True)
 elif m=="session/new":
  cfg=r["params"]["mcpServers"][0]; e=os.environ.copy();e.update({x["name"]:x["value"] for x in cfg.get("env",[])})
  server=subprocess.Popen([cfg["command"]]+cfg.get("args",[]),stdin=subprocess.PIPE,stdout=subprocess.PIPE,env=e)
  print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"sessionId":"s"}}),flush=True)
 elif m=="session/prompt":
  rpc(1,"initialize",{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"a","version":"1"}});rpc(2,"tools/list")
  out=rpc(3,"tools/call",{"name":"echo","arguments":{}})["result"]["content"][0]["text"]
  print(json.dumps({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":out}}}}),flush=True)
  print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"stopReason":"end_turn"}}),flush=True)
''')
    run = AcpRunSpec("p", "agent-default", {"mcpServers":{"x":{"command":server,"env":{"WIRE_LITERAL":"literal-wire-secret","WIRE_REF":"${WIRE_REF_SECRET}"}}}}, "x", {"command":agent}, timeout_seconds=3)
    result = asyncio.run(AcpHarnessRunner().run(run))
    assert result.status == "completed", result.error
    evidence = json.dumps(result.protocol_events)
    assert "ordinary-wire-result" in evidence
    assert "literal-wire-secret" not in evidence
    assert "wire-reference-secret" not in evidence


@pytest.mark.parametrize("field", ["env", "headers", "url"])
def test_missing_selected_reference_preflight_before_process(tmp_path, monkeypatch, field):
    marker = tmp_path / "started"
    agent = executable(tmp_path / "agent.py", f'open({str(marker)!r},"w").write("started")')
    server = {"command":"echo"} if field == "env" else {"type":"http", "url":"http://example.test/${MISSING_REF}", "headers":{"Authorization":"${MISSING_REF}"}}
    if field == "env": server = {"command":"echo", "env":{"TOKEN":"${MISSING_REF}"}}
    import mcp_pal.harness.acp as runtime
    called = []
    monkeypatch.setattr(runtime.tempfile, "mkdtemp", lambda **kwargs: called.append(True) or str(tmp_path / "workspace"))
    run = AcpRunSpec("p", "agent-default", {"mcpServers":{"x":server}}, "x", {"command":agent})
    result = asyncio.run(AcpHarnessRunner().run(run))
    assert result.error_code == "acp_environment_missing" and result.error_phase == "preflight"
    assert not called and not marker.exists()


@pytest.mark.parametrize("body,code", [("print('ordinary diagnostic not-json',flush=True)", "acp_malformed_stdout"), ("import sys;sys.exit(0)", "acp_early_exit"), ("import sys;sys.exit(7)", "acp_early_exit")])
def test_malformed_and_clean_or_nonzero_early_exit_are_structured(tmp_path, body, code):
    result = asyncio.run(AcpHarnessRunner().run(spec(executable(tmp_path / "bad.py", body), timeout_seconds=2)))
    assert result.status == "failed"
    assert result.error_code == code
    assert result.error_phase == "initialize"
    if code == "acp_malformed_stdout":
        assert "ordinary diagnostic" in json.dumps(result.event_records)
        assert len(json.dumps(result.event_records)) < 10_000


def test_stderr_and_malformed_excerpt_redact_known_values_keep_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_SECRET", "manifest-resolved-secret")
    agent = executable(tmp_path / "diagnostics.py", '''
import os,sys
sys.stderr.write("ordinary stderr manifest-resolved-secret literal-resolved-secret\\n");sys.stderr.flush()
print("ordinary stdout manifest-resolved-secret",flush=True)
''')
    run = AcpRunSpec("p", "agent-default", {"mcpServers":{"x":{"command":"echo","env":{"L":"literal-resolved-secret"}}}}, "x", {"command":agent,"env":{"S":"${HARNESS_SECRET}"}}, timeout_seconds=2)
    result = asyncio.run(AcpHarnessRunner().run(run))
    assert "ordinary stderr" in result.stderr
    assert "manifest-resolved-secret" not in result.stderr
    assert "literal-resolved-secret" not in result.stderr
    excerpt = json.dumps(result.event_records)
    assert "ordinary stdout" in excerpt
    assert "manifest-resolved-secret" not in excerpt
    probe_agent = executable(tmp_path / "probe.py", '''
import json,os,sys
sys.stderr.write(os.environ.get("HARNESS_SECRET", "") + " ordinary-probe-stderr\\n");sys.stderr.flush()
for line in sys.stdin:
 r=json.loads(line);m=r.get("method")
 if m=="initialize": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":1}}),flush=True)
 elif m=="session/new": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"sessionId":"s"}}),flush=True)
''')
    probe = asyncio.run(protocol_probe({"command":probe_agent,"env":{"S":"${HARNESS_SECRET}"}}))
    assert "ordinary-probe-stderr" in probe["stderr"]
    assert "manifest-resolved-secret" not in probe["stderr"]


def test_optional_auth_methods_allowed_but_auth_request_fails(tmp_path):
    optional = executable(tmp_path / "optional.py", '''
import json,sys
for line in sys.stdin:
 r=json.loads(line); m=r.get("method")
 if m=="initialize": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":1,"authMethods":[{"id":"oauth"}]}}),flush=True)
 elif m=="session/new": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"sessionId":"s"}}),flush=True)
 elif m=="session/prompt": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"stopReason":"end_turn"}}),flush=True)
''')
    assert asyncio.run(AcpHarnessRunner().run(spec(optional, timeout_seconds=2))).status == "completed"
    auth = executable(tmp_path / "auth.py", '''
import json,sys
for line in sys.stdin:
 r=json.loads(line); m=r.get("method")
 if m=="initialize":
  print(json.dumps({"jsonrpc":"2.0","id":99,"method":"authenticate","params":{}}),flush=True)
  print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":1}}),flush=True)
''')
    result = asyncio.run(AcpHarnessRunner().run(spec(auth, timeout_seconds=2)))
    assert result.error_code == "acp_auth_required"


def test_failure_reaps_agent_spawned_child(tmp_path):
    child_pid = tmp_path / "child.pid"
    agent = executable(tmp_path / "parent.py", f'''
import os,subprocess,sys
child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])
open({str(child_pid)!r},"w").write(str(child.pid))
print("not-json",flush=True)
import time; time.sleep(.3)
''')
    result = asyncio.run(AcpHarnessRunner().run(spec(agent, timeout_seconds=3)))
    assert result.error_code == "acp_malformed_stdout"
    child = int(child_pid.read_text())
    with pytest.raises(ProcessLookupError): os.kill(child, 0)


@pytest.mark.parametrize("method,code", [("request_permission", "acp_interaction_required"), ("create_elicitation", "acp_interaction_required"), ("read_text_file", "acp_interaction_required"), ("create_terminal", "acp_interaction_required")])
def test_interaction_requests_are_structured(tmp_path, method, code):
    agent = executable(tmp_path / f"{method}.py", f'''
import json,sys
pending=None
for line in sys.stdin:
 r=json.loads(line); m=r.get("method")
 if pending is not None and m is None:
  print(json.dumps({{"jsonrpc":"2.0","id":pending,"result":{{"stopReason":"end_turn"}}}}),flush=True); pending=None; continue
 if m=="initialize": print(json.dumps({{"jsonrpc":"2.0","id":r["id"],"result":{{"protocolVersion":1}}}}),flush=True)
 elif m=="session/new": print(json.dumps({{"jsonrpc":"2.0","id":r["id"],"result":{{"sessionId":"s"}}}}),flush=True)
 elif m=="session/prompt":
  pending=r["id"]
  print(json.dumps({{"jsonrpc":"2.0","id":77,"method":"{method}","params":{{}}}}),flush=True)
''')
    result = asyncio.run(AcpHarnessRunner().run(spec(agent, timeout_seconds=2)))
    assert result.error_code == code


def test_stale_mode_and_config_options_are_rejected(tmp_path):
    agent = lifecycle_agent(tmp_path / "stale.py", "none")
    stale_mode = asyncio.run(AcpHarnessRunner().run(spec(agent, agent_mode_id="missing", timeout_seconds=2)))
    assert stale_mode.error_code == "acp_stale_option" and stale_mode.error_phase == "set_session_mode"
    stale_config = asyncio.run(AcpHarnessRunner().run(spec(agent, session_config={"missing": True}, timeout_seconds=2)))
    assert stale_config.error_code == "acp_stale_option" and stale_config.error_phase == "set_config_option"


def test_empty_live_dimensions_reject_historical_requests(tmp_path):
    agent = executable(tmp_path / "empty.py", '''
import json,sys
for line in sys.stdin:
 r=json.loads(line); m=r.get("method")
 if m=="initialize": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":1}}),flush=True)
 elif m=="session/new": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"sessionId":"s"}}),flush=True)
''')
    mode = asyncio.run(AcpHarnessRunner().run(spec(agent, agent_mode_id="old", timeout_seconds=2)))
    config = asyncio.run(AcpHarnessRunner().run(spec(agent, session_config={"old": True}, timeout_seconds=2)))
    assert mode.error_code == "acp_stale_option"
    assert config.error_code == "acp_stale_option"


def test_precancel_is_not_erased_by_run_start(tmp_path):
    agent = lifecycle_agent(tmp_path / "hang.py", "prompt")
    runner = AcpHarnessRunner()
    runner.request_cancel()
    result = asyncio.run(runner.run(spec(agent, timeout_seconds=2)))
    assert result.status == "cancelled"


def test_updates_final_text_and_errors_redact_exact_manifest_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("INNOCUOUS_VALUE", "arbitrary-private-value")
    agent = executable(tmp_path / "secret.py", '''
import json,os,sys
secret=os.environ["CHILD_VALUE"]
for line in sys.stdin:
 r=json.loads(line); m=r.get("method")
 if m=="initialize": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":1}}),flush=True)
 elif m=="session/new": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"sessionId":"s"}}),flush=True)
 elif m=="session/prompt":
  print(json.dumps({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":secret}}}}),flush=True)
  print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"stopReason":"end_turn"}}),flush=True)
''')
    seen=[]
    async def capture(raw, kind, payload): seen.append((raw, payload))
    run = spec(agent, timeout_seconds=2)
    run.manifest = {"command":agent,"env":{"CHILD_VALUE":"${INNOCUOUS_VALUE}"}}
    result = asyncio.run(AcpHarnessRunner().run(run, capture))
    persisted = json.dumps({"final":result.final_text,"events":seen})
    assert "arbitrary-private-value" not in persisted
    assert "[REDACTED]" in persisted


def test_probe_metadata_and_protocol_errors_redact_exact_manifest_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDINARY_SETTING", "probe-private-value")
    metadata_agent = executable(tmp_path / "probe-secret.py", '''
import json,os,sys
for line in sys.stdin:
 r=json.loads(line); m=r.get("method")
 if m=="initialize": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":1,"agentInfo":{"name":os.environ["CHILD_VALUE"],"version":"1"}}}),flush=True)
 elif m=="session/new": print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"sessionId":"s"}}),flush=True)
''')
    manifest={"command":metadata_agent,"env":{"CHILD_VALUE":"${ORDINARY_SETTING}"}}
    probe=asyncio.run(protocol_probe(manifest))
    assert "probe-private-value" not in json.dumps(probe)
    assert probe["agent_info"]["name"] == "[REDACTED]"

    error_agent = executable(tmp_path / "error-secret.py", '''
import json,os,sys
for line in sys.stdin:
 r=json.loads(line)
 print(json.dumps({"jsonrpc":"2.0","id":r["id"],"error":{"code":-32000,"message":"boom "+os.environ["CHILD_VALUE"]}}),flush=True)
''')
    failed=asyncio.run(AcpHarnessRunner().run(AcpRunSpec("p","agent-default",{"mcpServers":{"x":{"command":"echo"}}},"x",{"command":error_agent,"env":{"CHILD_VALUE":"${ORDINARY_SETTING}"}},timeout_seconds=2)))
    assert "probe-private-value" not in (failed.error or "")
    assert "[REDACTED]" in (failed.error or "")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group assertions")
def test_parent_early_exit_reaps_child_and_does_not_wait_forever_on_inherited_stderr(tmp_path):
    child_pid = tmp_path / "early-child.pid"
    agent = executable(tmp_path / "early-parent.py", f'''
import subprocess,sys
child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])
open({str(child_pid)!r},"w").write(str(child.pid))
''')
    started = __import__("time").monotonic()
    result = asyncio.run(AcpHarnessRunner().run(spec(agent, timeout_seconds=2)))
    assert __import__("time").monotonic() - started < 4
    assert result.error_code == "acp_early_exit"
    child = int(child_pid.read_text())
    state = __import__("subprocess").run(["ps","-o","stat=","-p",str(child)],capture_output=True,text=True,check=False).stdout.strip()
    assert not state or state.startswith("Z")
