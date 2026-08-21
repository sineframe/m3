import asyncio, json, os, stat, tempfile, time, sys
from pathlib import Path
from fastapi.testclient import TestClient
from mcp_pal.api import create_app
from mcp_pal.config import Settings
from mcp_pal.domain.events import derive_mcp_assertion, derive_mcp_summary, normalize_events
from mcp_pal.harness.claude_cli import ClaudeCodeRunner, RunSpec
from mcp_pal.harness.opencode_cli import OpenCodeRunner, opencode_config
import mcp_pal.harness.opencode_cli as opencode_module
from mcp_pal.harness.base import HarnessResult
from mcp_pal.trace.normalized import build_opencode_trace

def fake(path, body):
    path.write_text("#!/usr/bin/env python3\n"+body); path.chmod(path.stat().st_mode | stat.S_IXUSR); return str(path)

def spec(script, mode="mcp_only", timeout=2):
    return RunSpec("exact prompt\n", "model-x", {"mcpServers":{"draw":{"command":"ignored"},"other":{"command":"ignored"}}}, "draw", mode, timeout, 5, .5)

def test_commands_and_exact_prompt_all_modes(tmp_path):
    runner=ClaudeCodeRunner("claude")
    for mode in ("mcp_only","mcp_read_only","full"):
        cmd=runner.build_command(spec("",mode),"/tmp/mcp.json")
        assert "--mcp-config" in cmd and "--model" in cmd and "--expected-output" not in cmd
        if mode == "mcp_only": assert cmd[cmd.index("--tools")+1] == ""
        if mode == "full": assert "--tools" in cmd and cmd[cmd.index("--tools")+1] == "default" and "--dangerously-skip-permissions" in cmd

def test_fake_claude_metadata_mixed_blocks_and_malformed(tmp_path):
    script=fake(tmp_path/"claude.py", """
import json,sys
assert sys.stdin.read() == 'exact prompt\\n'
print(json.dumps({'type':'system','subtype':'init','tools':[{'name':'mcp__draw__create'}]}))
print(json.dumps({'type':'assistant','message':{'content':[{'type':'thinking','thinking':'private'},{'type':'text','text':'hello'},{'type':'tool_use','id':'id-1','name':'mcp__draw__create','input':{}}]}}))
print(json.dumps({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'id-1','content':[{'type':'text','text':'ok'}]}]}}))
print('not-json')
print(json.dumps({'type':'result','result':'final','total_cost_usd':0.13,'num_turns':2,'session_id':'session-1'}))
""")
    result=asyncio.run(ClaudeCodeRunner(script).run(spec(script)))
    assert result.status == "completed" and result.final_text == "final"
    assert result.cost_usd == .13 and result.turns == 2 and result.session_id == "session-1"
    assert isinstance(result.events[3],str) and any(t == "thinking" for t,_ in result.normalized)
    assert derive_mcp_assertion(result.events,"draw") == "passed"
    summary=derive_mcp_summary(result.events,"draw"); assert summary["selected_server_call_count"] == 1 and summary["success_count"] == 1

def test_opencode_config_command_and_native_events(tmp_path):
    profile={"mcpServers":{"draw":{"command":"node","args":["server.js"],"env":{"TOKEN":"${DRAW_TOKEN}"}},"other":{"command":"ignored"}}}
    config=opencode_config(profile,"draw","mcp_only")
    assert config["mcp"] == {"draw":{"type":"local","command":["node","server.js"],"enabled":True,"environment":{"TOKEN":"{env:DRAW_TOKEN}"}}}
    assert config["tools"] == {"*":False,"draw_*":True}
    runner=OpenCodeRunner("opencode")
    command=runner.build_command(RunSpec("prompt","opencode/model",profile,"draw"))
    assert command[:3] == ["opencode","--pure","run"] and "--format" in command and "--model" in command
    assert "prompt" not in command

    script=fake(tmp_path/"opencode.py", """
import json,os,sys
assert sys.stdin.read() == 'exact prompt\\n'
config=json.load(open(os.environ['OPENCODE_CONFIG']))
assert list(config['mcp']) == ['draw']
print(json.dumps({'type':'step_start','sessionID':'ses-1','part':{'type':'step-start'}}))
print(json.dumps({'type':'reasoning','sessionID':'ses-1','part':{'type':'reasoning','text':'thought'}}))
print(json.dumps({'type':'tool_use','sessionID':'ses-1','part':{'type':'tool','callID':'call-1','tool':'draw_create','state':{'status':'completed','input':{'x':1},'output':'ok'}}}))
print(json.dumps({'type':'text','sessionID':'ses-1','part':{'type':'text','text':'done'}}))
print(json.dumps({'type':'step_finish','sessionID':'ses-1','part':{'type':'step-finish','cost':0.02,'tokens':{'input':2,'output':1}}}))
""")
    result=asyncio.run(OpenCodeRunner(script).run(RunSpec("exact prompt\n","opencode/model",profile,"draw")))
    assert result.status == "completed" and result.final_text == "done" and result.session_id == "ses-1"
    assert result.cost_usd == .02 and result.turns == 1
    assert derive_mcp_assertion(result.events,"draw") == "passed"
    assert {event for event,_ in result.normalized} >= {"step_start","thinking","tool_call","tool_result","assistant_text","step_finish"}

def test_opencode_stdio_wrapper_captures_real_jsonrpc_roundtrip(tmp_path):
    fixture = Path(__file__).parents[1] / 'fixtures' / 'mcp_echo_server.py'
    script = fake(tmp_path / 'opencode-relay.py', f"""
import json, os, subprocess, sys
config=json.load(open(os.environ['OPENCODE_CONFIG']))
command=config['mcp']['draw']['command']
server=subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def rpc(identifier, method, params=None):
    request={{'jsonrpc':'2.0','id':identifier,'method':method}}
    if params is not None: request['params']=params
    server.stdin.write(json.dumps(request)+'\\n'); server.stdin.flush()
    return json.loads(server.stdout.readline())
rpc(1, 'initialize', {{'protocolVersion':'2024-11-05','capabilities':{{}},'clientInfo':{{'name':'fake','version':'1'}}}})
rpc(2, 'tools/list')
response=rpc(3, 'tools/call', {{'name':'echo','arguments':{{'text':'wire hello'}}}})
server.terminate(); server.wait()
print(json.dumps({{'type':'tool_use','sessionID':'wire','part':{{'type':'tool','callID':'call-wire','tool':'draw_echo','state':{{'status':'completed','input':{{'text':'wire hello'}},'output':response['result']}}}}}}), flush=True)
print(json.dumps({{'type':'text','sessionID':'wire','part':{{'type':'text','text':'done'}}}}), flush=True)
print(json.dumps({{'type':'step_finish','sessionID':'wire','part':{{'type':'step-finish'}}}}), flush=True)
""")
    profile={"mcpServers":{"draw":{"command":sys.executable,"args":[str(fixture)]}}}
    result=asyncio.run(OpenCodeRunner(script).run(RunSpec('prompt','model',profile,'draw',timeout_seconds=3)))
    assert result.status == 'completed'
    assert result.transport == 'stdio'
    methods=[event.get('payload',{}).get('method') for event in result.protocol_events]
    assert 'tools/call' in methods
    request=next(event for event in result.protocol_events if event.get('payload',{}).get('method') == 'tools/call')
    response=next(event for event in result.protocol_events if event.get('payload',{}).get('id') == request['payload']['id'] and event.get('direction') == 'server_to_client')
    assert request['payload']['params']['name'] == 'echo'
    assert response['payload']['result']['content'][0]['text'] == 'wire hello'
    trace=build_opencode_trace(events=result.event_records, protocol_events=result.protocol_events, selected_server='draw', transport=result.transport, status=result.status, session_id=result.session_id)
    call=trace['mcp_calls'][0]
    assert call['wire_request']['method'] == 'tools/call'
    assert call['wire_response']['result']['content'][0]['text'] == 'wire hello'
    assert isinstance(call['server_latency_ms'], (int, float)) and call['server_latency_ms'] >= 0
    assert call['provenance']['wire'] is True and call['limitations'] == [] and trace['limitations'] == []

def test_opencode_embedded_env_refs_and_hostile_home_isolation(tmp_path):
    remote_profile={"mcpServers":{"draw":{"type":"http","url":"https://example.test/mcp","headers":{"Authorization":"Bearer ${DRAW_TOKEN}"}}}}
    config=opencode_config(remote_profile,"draw","mcp_only")
    assert config["mcp"]["draw"]["headers"]["Authorization"] == "Bearer {env:DRAW_TOKEN}"
    hostile=tmp_path/"home"; (hostile/".config/opencode").mkdir(parents=True)
    (hostile/".config/opencode/opencode.json").write_text('{"permission":{"*":"allow"}}')
    marker=tmp_path/"env.json"
    profile={"mcpServers":{"draw":{"command":"echo"}}}
    script=fake(tmp_path/"opencode-isolated.py", f"""
import json,os
assert os.environ['HOME'] != {str(hostile)!r}
assert os.environ['OPENCODE_CONFIG_DIR'].startswith(os.environ['HOME'])
config=json.load(open(os.environ['OPENCODE_CONFIG']))
assert config['permission']['*'] == 'deny'
open({str(marker)!r},'w').write('isolated')
print(json.dumps({{'type':'text','sessionID':'iso','part':{{'type':'text','text':'ok'}}}}))
print(json.dumps({{'type':'step_finish','sessionID':'iso','part':{{'type':'step-finish'}}}}))
""")
    old=os.environ.get("HOME")
    os.environ["HOME"]=str(hostile)
    try:
        result=asyncio.run(OpenCodeRunner(script,"secret-not-printed").run(RunSpec("prompt","open/model",profile,"draw")))
    finally:
        if old is None: os.environ.pop("HOME",None)
        else: os.environ["HOME"]=old
    assert result.status == "completed" and marker.exists()

def test_opencode_remote_proxy_rewrites_local_config_and_stops(tmp_path, monkeypatch):
    seen = tmp_path / "config.json"
    script = fake(tmp_path / "remote-opencode.py", f"""
import json, os
config=json.load(open(os.environ['OPENCODE_CONFIG']))
json.dump(config, open({str(seen)!r}, 'w'))
assert config['mcp']['draw']['url'] == 'http://127.0.0.1:9191/mcp'
assert 'headers' not in config['mcp']['draw']
print(json.dumps({{'type':'text','sessionID':'remote','part':{{'type':'text','text':'ok'}}}}))
print(json.dumps({{'type':'step_finish','sessionID':'remote','part':{{'type':'step-finish'}}}}))
""")
    class Proxy:
        instances = []
        def __init__(self, **kwargs): self.kwargs = kwargs; self.stopped = False; self.__class__.instances.append(self)
        async def start(self): return 'http://127.0.0.1:9191/mcp'
        async def stop(self): self.stopped = True
    monkeypatch.setattr(opencode_module, 'McpHttpProxy', Proxy)
    profile={"mcpServers":{"draw":{"type":"http","url":"https://upstream.example/mcp","headers":{"Authorization":"Bearer TOP-SECRET"}}}}
    result=asyncio.run(OpenCodeRunner(script).run(RunSpec('prompt','model',profile,'draw',timeout_seconds=2)))
    assert result.status == 'completed' and Proxy.instances[0].stopped
    assert Proxy.instances[0].kwargs['upstream_url'] == 'https://upstream.example/mcp'
    assert Proxy.instances[0].kwargs['configured_headers']['Authorization'] == 'Bearer TOP-SECRET'
    assert json.loads(seen.read_text())['mcp']['draw'].get('headers') is None

def test_opencode_remote_proxy_failure_is_stable_and_stops(tmp_path, monkeypatch):
    class Proxy:
        instance = None
        def __init__(self, **kwargs): Proxy.instance = self
        async def start(self): raise RuntimeError('upstream secret https://upstream.example')
        async def stop(self): self.stopped = True
    monkeypatch.setattr(opencode_module, 'McpHttpProxy', Proxy)
    profile={"mcpServers":{"draw":{"type":"sse","url":"https://upstream.example/sse","headers":{"Authorization":"Bearer SECRET"}}}}
    result=asyncio.run(OpenCodeRunner('missing-opencode').run(RunSpec('prompt','model',profile,'draw',timeout_seconds=1)))
    assert result.status == 'failed' and result.error == 'OpenCode transport setup failed'
    assert Proxy.instance.stopped and 'SECRET' not in (result.error or '') and 'upstream' not in (result.error or '')

def test_opencode_proxy_is_stopped_when_config_write_fails(tmp_path, monkeypatch):
    class Proxy:
        instance = None
        def __init__(self, **kwargs): Proxy.instance = self
        async def start(self): return 'http://127.0.0.1:9191/mcp'
        async def stop(self): self.stopped = True
    monkeypatch.setattr(opencode_module, 'McpHttpProxy', Proxy)
    monkeypatch.setattr(opencode_module.Path, 'write_text', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('nope')))
    profile={"mcpServers":{"draw":{"type":"http","url":"https://upstream.example/mcp"}}}
    result=asyncio.run(OpenCodeRunner('missing-opencode').run(RunSpec('prompt','model',profile,'draw',timeout_seconds=1)))
    assert result.status == 'failed' and result.error == 'OpenCode run setup failed' and Proxy.instance.stopped

def test_opencode_clean_exit_recovers_export_before_classification(tmp_path):
    script=fake(tmp_path/"opencode-early.py", """
import json,sys
if 'export' in sys.argv:
    print(json.dumps({'messages':[{'info':{'role':'assistant'},'parts':[{'type':'step-start'},{'type':'text','text':'recovered'},{'type':'step-finish','cost':0.02}]}]})); raise SystemExit(0)
if 'session' in sys.argv: raise SystemExit(0)
print(json.dumps({'type':'step_start','sessionID':'early','part':{'type':'step-start'}}))
""")
    result=asyncio.run(OpenCodeRunner(script).run(spec(script,timeout=2)))
    assert result.status == "completed" and result.final_text == "recovered"

def test_opencode_recovers_after_intermediate_tool_step(tmp_path):
    script=fake(tmp_path/"opencode-tool-early.py", """
import json,sys
if 'export' in sys.argv:
    print(json.dumps({'messages':[{'info':{'role':'assistant'},'parts':[{'type':'step-start'},{'type':'tool','callID':'call-1','tool':'draw_echo','state':{'status':'completed','input':{'text':'ok'},'output':'ok'}},{'type':'step-finish'}]},{'info':{'role':'assistant'},'parts':[{'type':'step-start'},{'type':'text','text':'final'},{'type':'step-finish'}]}]})); raise SystemExit(0)
if 'session' in sys.argv: raise SystemExit(0)
print(json.dumps({'type':'step_start','sessionID':'tool-early','part':{'type':'step-start'}}))
print(json.dumps({'type':'tool_use','sessionID':'tool-early','part':{'type':'tool','callID':'call-1','tool':'draw_echo','state':{'status':'completed','input':{'text':'ok'},'output':'ok'}}}))
print(json.dumps({'type':'step_finish','sessionID':'tool-early','part':{'type':'step-finish','reason':'tool-calls'}}))
""")
    result=asyncio.run(OpenCodeRunner(script).run(spec(script,timeout=2)))
    assert result.status == "completed" and result.final_text == "final"
    assert derive_mcp_assertion(result.events,"draw") == "passed"
    assert derive_mcp_summary(result.events,"draw")["selected_server_call_count"] == 1

def test_opencode_clean_exit_without_complete_trace_fails(tmp_path):
    script=fake(tmp_path/"opencode-incomplete.py", """
import json,sys
if 'session' in sys.argv: raise SystemExit(0)
print(json.dumps({'type':'step_start','sessionID':'incomplete','part':{'type':'step-start'}}))
""")
    result=asyncio.run(OpenCodeRunner(script).run(spec(script,timeout=2)))
    assert result.status == "failed" and "complete JSON trace" in (result.error or "")

def test_opencode_session_is_deleted_after_terminal_trace(tmp_path):
    marker=tmp_path/"deleted"
    script=fake(tmp_path/"opencode-delete.py", f"""
import json,os,sys
if 'session' in sys.argv:
    open({str(marker)!r},'w').write(sys.argv[-1]); raise SystemExit(0)
print(json.dumps({{'type':'text','sessionID':'delete-me','part':{{'type':'text','text':'ok'}}}}))
print(json.dumps({{'type':'step_finish','sessionID':'delete-me','part':{{'type':'step-finish'}}}}))
""")
    result=asyncio.run(OpenCodeRunner(script).run(spec(script,timeout=2)))
    assert result.status == "completed" and marker.read_text() == "delete-me"

def test_fake_opencode_api_persists_normalized_trace_and_report(tmp_path):
    settings=Settings(database_path=str(tmp_path/"api.db"), opencode_model_ids=["open/model"], opencode_executable="missing")
    app=create_app(settings)
    class FakeRunner:
        async def run(self, spec, on_event=None, cancel_event=None):
            raw={"type":"tool_use","sessionID":"fake-session","part":{"type":"tool","callID":"c1","tool":"draw_echo","state":{"status":"completed","input":{"text":"hello"},"output":{"text":"hello"}}}}
            protocol=[
                {"offset_ms":4,"transport":"stdio","direction":"client_to_server","payload":{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"echo","arguments":{"text":"hello","api_key":"SUPER-SECRET"}}}},
                {"offset_ms":9,"transport":"stdio","direction":"server_to_client","payload":{"jsonrpc":"2.0","id":7,"result":{"content":[{"type":"text","text":"hello"}]}}},
            ]
            if on_event:
                for kind,payload in normalize_events(raw,spec.enabled_server): await on_event(raw,kind,payload)
            return HarnessResult(status="completed", events=[raw], event_records=[{"raw_event":raw,"offset_ms":4,"type":"tool_use"}], final_text="done", session_id="fake-session", protocol_events=protocol)
        def request_cancel(self): pass
    app.state.manager.runner_for=lambda harness: FakeRunner()
    with TestClient(app) as client:
        profile=client.post("/api/v1/profiles",json={"name":"fake","mcp_json":{"mcpServers":{"draw":{"command":"echo"}}}}).json()
        run=client.post("/api/v1/runs",json={"harness":"opencode","model":"open/model","prompt":"p","expected_output":"done","profile_revision_id":profile["current_revision_id"],"enabled_server":"draw"}).json()
        for _ in range(40):
            state=client.get(f"/api/v1/runs/{run['id']}").json()
            if state["status"] not in {"queued","running"}: break
            time.sleep(.01)
        report=client.get(f"/api/v1/runs/{run['id']}/report").json(); trace=report["trace"]
        assert state["status"]=="completed" and trace["available"] and trace["schema"]=="opencode.v1"
        assert trace["mcp_calls_schema"]=="mcp.v1" and trace["summary"]["transport"]=="stdio"
        call=trace["mcp_calls"][0]
        assert {call[k] for k in ("server","tool","status")}=={"draw","echo","completed"}
        assert call["arguments"]=={"text":"hello"} and call["result"]=={"text":"hello"}
        assert call["wire_request"]["method"] == "tools/call"
        assert call["wire_response"]["result"]["content"][0]["text"] == "hello"
        assert call["server_latency_ms"] == 5 and call["provenance"]["wire"] is True
        assert call["limitations"] == []
        assert call["wire_request"]["params"]["arguments"]["api_key"] == "[REDACTED]"
        assert not trace["limitations"]
        assert "SUPER-SECRET" not in json.dumps(report)

def test_opencode_provider_credentials_are_injected_without_exposing_values(tmp_path):
    marker=tmp_path/"credential-presence.json"
    script=fake(tmp_path/"opencode-env.py", f"""
import json,os
json.dump({{'openrouter':bool(os.environ.get('OPENROUTER_API_KEY')),'anthropic':bool(os.environ.get('ANTHROPIC_API_KEY')),'opencode_go':bool(os.environ.get('OPENCODE_API_KEY'))}},open({str(marker)!r},'w'))
print(json.dumps({{'type':'text','sessionID':'env','part':{{'type':'text','text':'ok'}}}}))
print(json.dumps({{'type':'step_finish','sessionID':'env','part':{{'type':'step-finish'}}}}))
""")
    runner=OpenCodeRunner(script,provider_api_keys={"openrouter":"router-key","anthropic":"anthropic-key","opencode-go":"go-key"})
    result=asyncio.run(runner.run(spec(script,timeout=2)))
    assert result.status == "completed" and json.loads(marker.read_text()) == {"openrouter":True,"anthropic":True,"opencode_go":True}

def test_opencode_zero_output_run_leaves_no_session_data(tmp_path):
    marker=tmp_path/"data-path"
    script=fake(tmp_path/"opencode-empty.py", f"""
import os
open({str(marker)!r},'w').write(os.environ['XDG_DATA_HOME'])
""")
    result=asyncio.run(OpenCodeRunner(script).run(spec(script,timeout=2)))
    assert result.status == "failed"
    assert not os.path.exists(marker.read_text())

def test_opencode_saved_auth_is_copied_into_isolated_data_store(tmp_path):
    original=tmp_path/"original-data"; auth=original/"opencode/auth.json"; auth.parent.mkdir(parents=True); auth.write_text("credential")
    marker=tmp_path/"auth-presence"
    script=fake(tmp_path/"opencode-saved-auth.py", f"""
import json,os,stat
path=os.path.join(os.environ['XDG_DATA_HOME'],'opencode','auth.json')
json.dump({{'exists':os.path.isfile(path),'mode':stat.S_IMODE(os.stat(path).st_mode) if os.path.isfile(path) else 0,'isolated':os.environ['XDG_DATA_HOME'] != {str(original)!r}}},open({str(marker)!r},'w'))
print(json.dumps({{'type':'text','sessionID':'saved','part':{{'type':'text','text':'ok'}}}}))
print(json.dumps({{'type':'step_finish','sessionID':'saved','part':{{'type':'step-finish'}}}}))
""")
    old=os.environ.get("XDG_DATA_HOME"); os.environ["XDG_DATA_HOME"]=str(original)
    try: result=asyncio.run(OpenCodeRunner(script).run(spec(script,timeout=2)))
    finally:
        if old is None: os.environ.pop("XDG_DATA_HOME",None)
        else: os.environ["XDG_DATA_HOME"]=old
    assert result.status == "completed" and json.loads(marker.read_text()) == {"exists":True,"mode":0o600,"isolated":True}

def test_opencode_harness_limits_are_not_reported_as_enforced(tmp_path):
    settings=Settings(database_path=str(tmp_path/"limits.db"),opencode_api_key="key",opencode_executable="opencode",opencode_model_ids=["open/model"],claude_model_ids=["claude/model"])
    app=create_app(settings); app.state.manager.submit=lambda _: None; client=TestClient(app)
    caps=client.get("/api/v1/capabilities").json()
    assert caps["limits_by_harness"]["opencode"] == {"timeout_seconds":120,"max_turns":None,"max_budget_usd":None}
    profile=client.post("/api/v1/profiles",json={"name":"x","mcp_json":{"mcpServers":{"draw":{"command":"x"}}}}).json()
    run=client.post("/api/v1/runs",json={"harness":"opencode","model":"open/model","prompt":"p","expected_output":"e","profile_revision_id":profile["current_revision_id"],"enabled_server":"draw"}).json()
    assert run["max_turns"] is None and run["max_budget_usd"] is None

def test_opencode_readiness_accepts_non_zen_saved_provider_auth(tmp_path):
    script=fake(tmp_path/"opencode-auth.py", """
import sys
if 'auth' in sys.argv: print('Credentials\\n● OpenRouter api'); raise SystemExit(0)
if '--help' in sys.argv: print('run --format --model --thinking --pure'); raise SystemExit(0)
""")
    settings=Settings(database_path=str(tmp_path/"auth.db"),opencode_api_key=None,openrouter_api_key=None,opencode_executable=script,opencode_model_ids=["openrouter/model"])
    health=TestClient(create_app(settings)).get("/api/v1/health").json()
    assert health["harnesses"]["opencode"]["ready"]

def test_opencode_go_saved_auth_is_provider_aware(tmp_path):
    script=fake(tmp_path/"opencode-go-auth.py", """
import sys
if 'auth' in sys.argv: print('Credentials\\n● OpenCode Go api'); raise SystemExit(0)
if '--help' in sys.argv: print('run --format --model --thinking --pure'); raise SystemExit(0)
""")
    settings=Settings(database_path=str(tmp_path/"go-auth.db"),opencode_api_key=None,opencode_executable=script,opencode_model_ids=["opencode-go/model"])
    health=TestClient(create_app(settings)).get("/api/v1/health").json()
    assert health["harnesses"]["opencode"]["ready"]

def test_hanging_opencode_export_is_killed_and_reaped(tmp_path):
    pid_marker=tmp_path/"export-pid"
    script=fake(tmp_path/"opencode-export-hang.py", f"""
import os,sys,time
if 'export' in sys.argv:
    open({str(pid_marker)!r},'w').write(str(os.getpid()))
    time.sleep(20)
""")
    async def run_export():
        runner=OpenCodeRunner(script)
        started=time.monotonic()
        value=await runner._export_completed_session('ses-hang',os.environ.copy(),str(tmp_path))
        return value,time.monotonic()-started
    value,elapsed=asyncio.run(run_export())
    assert value is None and elapsed < 8
    pid=int(pid_marker.read_text())
    try: os.kill(pid,0)
    except ProcessLookupError: pass
    else: raise AssertionError("timed-out export subprocess is still alive")

def test_api_dispatches_opencode_and_validates_models(tmp_path):
    script=fake(tmp_path/"opencode-api.py", """
import json,sys
if '--help' in sys.argv: print('run --format --model --thinking --pure'); raise SystemExit(0)
print(json.dumps({'type':'text','sessionID':'s','part':{'type':'text','text':'open response'}}))
print(json.dumps({'type':'step_finish','sessionID':'s','part':{'type':'step-finish','cost':0,'tokens':{}}}))
""")
    settings=Settings(database_path=str(tmp_path/"open.db"),anthropic_api_key=None,claude_executable="/missing",claude_model_ids=["claude-model"],opencode_api_key="key",opencode_executable=script,opencode_model_ids=["open/model"],run_timeout_seconds=5)
    app=create_app(settings); client=TestClient(app)
    capabilities=client.get("/api/v1/capabilities").json()
    assert capabilities["harnesses"] == ["claude-code","opencode"]
    assert capabilities["models_by_harness"]["opencode"] == ["open/model"]
    assert client.get("/api/v1/health").json()["harnesses"]["opencode"]["ready"]
    profile=client.post("/api/v1/profiles",json={"name":"x","mcp_json":{"mcpServers":{"draw":{"command":"x"}}}}).json()
    body={"harness":"opencode","model":"open/model","prompt":"p","expected_output":"e","profile_revision_id":profile["current_revision_id"],"enabled_server":"draw"}
    created=client.post("/api/v1/runs",json=body); assert created.status_code == 202
    run_id=created.json()["id"]
    for _ in range(100):
        run=client.get(f"/api/v1/runs/{run_id}").json()
        if run["status"] not in ("queued","running"): break
        time.sleep(.02)
    assert run["status"] == "completed" and run["harness"] == "opencode" and run["claude_result"] == "open response"
    assert client.post("/api/v1/runs",json={**body,"model":"claude-model"}).status_code == 422

def test_opencode_recovers_completed_session_when_run_process_hangs(tmp_path):
    script=fake(tmp_path/"opencode-hang.py", """
import json,sys,time
if 'export' in sys.argv:
    print(json.dumps({'messages':[{'info':{'role':'assistant'},'parts':[{'type':'step-start'},{'type':'tool','callID':'c','tool':'draw_echo','state':{'status':'completed','input':{'text':'ok'},'output':'ok'}},{'type':'step-finish'}]},{'info':{'role':'assistant'},'parts':[{'type':'step-start'},{'type':'text','text':'ok'},{'type':'step-finish','cost':0.01}]}]})); raise SystemExit(0)
if 'session' in sys.argv:
    raise SystemExit(0)
print(json.dumps({'type':'step_start','sessionID':'recovered-session','part':{'type':'step-start'}}),flush=True)
time.sleep(20)
""")
    started=time.monotonic()
    result=asyncio.run(OpenCodeRunner(script,"key").run(spec(script,timeout=10)))
    assert time.monotonic()-started < 8 and result.status == "completed" and result.final_text == "ok"
    assert derive_mcp_assertion(result.events,"draw") == "passed"

def test_partial_and_complete_tool_call_counts_once():
    partial={"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"id-1","name":"mcp__draw__create","input":{}}}}
    complete={"type":"assistant","message":{"content":[{"type":"tool_use","id":"id-1","name":"mcp__draw__create","input":{}}]}}
    result={"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"id-1","content":"ok"}]}}
    summary=derive_mcp_summary([partial,complete,result],"draw")
    assert summary["selected_server_call_count"] == 1 and summary["success_count"] == 1

def test_timeout_and_cancel_reap(tmp_path):
    script=fake(tmp_path/"slow.py", """
import time
print('\\n', flush=True); time.sleep(20)
""")
    started=time.monotonic(); result=asyncio.run(ClaudeCodeRunner(script).run(spec(script,timeout=0.1))); assert result.status == "timed_out"; assert time.monotonic()-started < 4
    async def cancelled():
        event=asyncio.Event(); task=asyncio.create_task(ClaudeCodeRunner(script).run(spec(script,timeout=10),cancel_event=event)); await asyncio.sleep(.1); event.set(); return await task
    result=asyncio.run(cancelled()); assert result.status == "cancelled"

def test_api_active_cancel_is_cancelled(tmp_path):
    script=fake(tmp_path/"api-slow.py", "import time; time.sleep(20)")
    settings=Settings(database_path=str(tmp_path/"db.sqlite"),anthropic_api_key="key",claude_executable=script,claude_model_ids=["m"],run_timeout_seconds=10)
    client=TestClient(create_app(settings)); p=client.post("/api/v1/profiles",json={"name":"x","mcp_json":{"mcpServers":{"draw":{"command":"x"}}}}).json()
    run=client.post("/api/v1/runs",json={"model":"m","prompt":"p","expected_output":"e","profile_revision_id":p["current_revision_id"],"enabled_server":"draw"}).json(); run_id=run["id"]
    for _ in range(30):
        state=client.get(f"/api/v1/runs/{run_id}").json()
        if state["status"] == "running": break
        time.sleep(.02)
    assert client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 200
    for _ in range(100):
        state=client.get(f"/api/v1/runs/{run_id}").json()
        if state["status"] not in ("queued","running"): break
        time.sleep(.02)
    assert state["status"] == "cancelled"

def test_completed_api_run_persists_claude_trace_and_transport(tmp_path):
    script=fake(tmp_path/"trace.py", """
import json
print(json.dumps({'type':'stream_event','event':{'type':'message_start','message':{'id':'msg-1','model':'m','usage':{'input_tokens':4}}}}))
print(json.dumps({'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}}}))
print(json.dumps({'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'done'}}}))
print(json.dumps({'type':'stream_event','event':{'type':'message_delta','usage':{'output_tokens':2}}}))
print(json.dumps({'type':'stream_event','event':{'type':'message_stop'}}))
print(json.dumps({'type':'assistant','message':{'id':'msg-1','content':[{'type':'text','text':'done'}],'usage':{'input_tokens':4,'output_tokens':2}}}))
print(json.dumps({'type':'result','result':'done','duration_ms':7,'duration_api_ms':6,'num_turns':1,'total_cost_usd':0.01}))
""")
    settings=Settings(database_path=str(tmp_path/"trace.sqlite"),anthropic_api_key="key",claude_executable=script,claude_model_ids=["m"])
    client=TestClient(create_app(settings))
    profile=client.post("/api/v1/profiles",json={"name":"trace","mcp_json":{"mcpServers":{"draw":{"command":"ignored"}}}}).json()
    created=client.post("/api/v1/runs",json={"model":"m","prompt":"p","expected_output":"done","profile_revision_id":profile["current_revision_id"],"enabled_server":"draw"}).json()
    for _ in range(100):
        state=client.get(f"/api/v1/runs/{created['id']}").json()
        if state["status"] not in {"queued","running"}: break
        time.sleep(.02)
    report=client.get(f"/api/v1/runs/{created['id']}/report").json()
    assert report["trace"]["available"] is True
    assert report["trace"]["summary"]["transport"] == "stdio"
    assert report["trace"]["summary"]["total_tokens"] == 6
    assert len([span for span in report["trace"]["spans"] if span["kind"] == "model_turn"]) == 1

def test_readiness_and_active_delete_conflict(tmp_path):
    help_script=fake(tmp_path/"help.py", "import sys; print('--print --bare --output-format --verbose --strict-mcp-config --mcp-config --no-session-persistence')")
    settings=Settings(database_path=str(tmp_path/"db.sqlite"),anthropic_api_key="key",claude_executable=help_script,claude_model_ids=["m"])
    app=create_app(settings); client=TestClient(app)
    assert client.get("/api/v1/health").json()["ready"]
    p=client.post("/api/v1/profiles",json={"name":"x","mcp_json":{"mcpServers":{"draw":{"command":"x"}}}}).json(); rev=p["current_revision_id"]
    app.state.manager.submit=lambda _: None
    run=client.post("/api/v1/runs",json={"model":"m","prompt":"p","expected_output":"e","profile_revision_id":rev,"enabled_server":"draw"}).json()
    assert client.delete(f"/api/v1/runs/{run['id']}").status_code == 409
    assert client.delete("/api/v1/runs",params={"confirm":"true"}).status_code == 409

def test_pagination_filters_and_clone_validation(tmp_path):
    settings=Settings(database_path=str(tmp_path/"db.sqlite"),anthropic_api_key="key",claude_executable="/missing",claude_model_ids=["m"])
    app=create_app(settings); client=TestClient(app); app.state.manager.submit=lambda _: None
    p=client.post("/api/v1/profiles",json={"name":"x","mcp_json":{"mcpServers":{"draw":{"command":"x"}}}}).json(); rev=p["current_revision_id"]
    for i in range(3):
        assert client.post("/api/v1/runs",json={"model":"m","prompt":f"p{i}","expected_output":"e","profile_revision_id":rev,"enabled_server":"draw"}).status_code == 202
    assert len(client.get("/api/v1/runs",params={"limit":2,"offset":1,"profile_id":p["id"],"model":"m"}).json()) == 2
    old=client.get("/api/v1/runs").json()[0]
    assert client.post(f"/api/v1/runs/{old['id']}/clone",json={"model":"not-configured"}).status_code == 422
    assert client.post(f"/api/v1/runs/{old['id']}/clone",json={"prompt":"   "}).status_code == 422
