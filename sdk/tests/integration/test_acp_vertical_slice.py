import asyncio, json, os, stat, sys
from pathlib import Path
from mcp_pal.harness.acp import AcpHarnessRunner
from mcp_pal.harness.base import AcpRunSpec

def executable(path: Path, body: str):
    path.write_text("#!/usr/bin/env python3\n"+body); path.chmod(path.stat().st_mode|stat.S_IXUSR); return str(path)

def test_keyless_real_acp_to_mcp_round_trip(tmp_path):
    echo=Path(__file__).parents[1]/"fixtures"/"mcp_echo_server.py"
    agent=executable(tmp_path/"agent.py", f"""
import json,subprocess,sys
server=None
def send(x): print(json.dumps(x,separators=(',',':')),flush=True)
for line in sys.stdin:
 r=json.loads(line); method=r.get('method'); ident=r.get('id'); p=r.get('params') or {{}}
 if method=='initialize': send({{'jsonrpc':'2.0','id':ident,'result':{{'protocolVersion':1,'agentInfo':{{'name':'fixture','version':'1'}},'agentCapabilities':{{}}}}}})
 elif method=='session/new':
  cfg=p['mcpServers'][0]; server=subprocess.Popen([cfg['command'],*cfg.get('args',[])],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
  def rpc(i,m,params=None):
   q={{'jsonrpc':'2.0','id':i,'method':m}}; q.update({{'params':params}} if params is not None else {{}}); server.stdin.write(json.dumps(q)+'\\n'); server.stdin.flush(); return json.loads(server.stdout.readline())
  rpc(1,'initialize',{{'protocolVersion':'2024-11-05','capabilities':{{}},'clientInfo':{{'name':'fixture','version':'1'}}}}); rpc(2,'tools/list')
  send({{'jsonrpc':'2.0','id':ident,'result':{{'sessionId':'session-1','modes':{{'currentModeId':'default','availableModes':[{{'id':'default','name':'Default'}}]}}}}}})
 elif method=='session/prompt':
  out=rpc(3,'tools/call',{{'name':'echo','arguments':{{'text':'nonce-123'}}}})
  for update in [{{'sessionUpdate':'agent_thought_chunk','content':{{'type':'text','text':'thinking'}}}},{{'sessionUpdate':'tool_call','toolCallId':'call-1','title':'echo'}},{{'sessionUpdate':'tool_call_update','toolCallId':'call-1','status':'completed'}},{{'sessionUpdate':'agent_message_chunk','content':{{'type':'text','text':'nonce-123'}}}}]:
   send({{'jsonrpc':'2.0','method':'session/update','params':{{'sessionId':'session-1','update':update}}}})
  send({{'jsonrpc':'2.0','id':ident,'result':{{'stopReason':'end_turn'}}}}); server.terminate(); server.wait()
""")
    spec=AcpRunSpec("call echo", "agent-default", {"mcpServers":{"echo":{"command":sys.executable,"args":[str(echo)]}}}, "echo", {"schema_version":"mcp-pal.harness.v1","protocol":"acp","protocol_version":1,"command":agent}, timeout_seconds=10)
    callbacks=[]
    async def callback(raw, kind, payload): callbacks.append((kind,payload))
    result=asyncio.run(AcpHarnessRunner().run(spec, on_event=callback))
    assert result.status=="completed", result.error
    assert result.final_text=="nonce-123"
    assert any(kind=="acp_update" and "thought" in str(payload).lower() for kind,payload in callbacks)
    assert any(kind=="acp_update" and "tool_call" in str(payload).lower() for kind,payload in callbacks)
    assert any(x.get("payload",{}).get("method")=="tools/call" for x in result.protocol_events)
    assert all("nonce-123" in json.dumps(x) for x in result.protocol_events if x.get("payload",{}).get("method")=="tools/call")
    call=next(x for x in result.protocol_events if x.get("payload",{}).get("method")=="tools/call")
    response=next(x for x in result.protocol_events if x.get("payload",{}).get("id")==call["payload"]["id"] and x.get("direction")=="server_to_client")
    assert response["payload"]["result"]["content"][0]["text"]=="nonce-123"
    assert response["offset_ms"] >= call["offset_ms"] >= 0

def test_acp_timeout_and_cancel_reap_agent(tmp_path):
    pid_file=tmp_path/"agent.pid"
    agent=executable(tmp_path/"hang.py", """
import json,time,sys,os
open(%r,'w').write(str(os.getpid()))
for line in sys.stdin:
 r=json.loads(line)
 if r.get('method')=='initialize': print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'protocolVersion':1}}),flush=True)
 elif r.get('method')=='session/new': print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'sessionId':'s'}}),flush=True)
 elif r.get('method')=='session/prompt': time.sleep(30)
""" % str(pid_file))
    spec=AcpRunSpec("hang","agent-default",{"mcpServers":{"echo":{"command":"echo"}}},"echo",{"command":agent},timeout_seconds=1)
    runner=AcpHarnessRunner(); result=asyncio.run(runner.run(spec)); assert result.status=="timed_out"
    pid=int(pid_file.read_text())
    try: os.kill(pid,0)
    except ProcessLookupError: pass
    else: raise AssertionError("timed out ACP process still alive")
    runner=AcpHarnessRunner()
    pid_file.unlink()
    async def cancel():
        cancel_spec=AcpRunSpec(**{**spec.__dict__, "timeout_seconds":2})
        task=asyncio.create_task(runner.run(cancel_spec)); await asyncio.sleep(.15); runner.request_cancel(); return await task
    result=asyncio.run(cancel()); assert result.status=="cancelled" and result.exit_code != 0
    cancelled_pid=int(pid_file.read_text())
    try: os.kill(cancelled_pid,0)
    except ProcessLookupError: pass
    else: raise AssertionError("cancelled ACP process still alive")
    healthy=executable(tmp_path/"healthy.py", """
import json,sys
for line in sys.stdin:
 r=json.loads(line); m=r.get('method')
 if m=='initialize': print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'protocolVersion':1}}),flush=True)
 elif m=='session/new': print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'sessionId':'ok'}}),flush=True)
 elif m=='session/prompt': print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'stopReason':'end_turn'}}),flush=True)
""")
    healthy_spec=AcpRunSpec("ok","agent-default",{"mcpServers":{"echo":{"command":"echo"}}},"echo",{"command":healthy},timeout_seconds=2)
    assert asyncio.run(AcpHarnessRunner().run(healthy_spec)).status=="completed"

def test_acp_missing_mcp_environment_fails_before_workspace(tmp_path, monkeypatch):
    import mcp_pal.harness.acp as module
    called=[]
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **kwargs: called.append(1) or str(tmp_path/"leak"))
    spec=AcpRunSpec("p","agent-default",{"mcpServers":{"echo":{"command":"echo","env":{"TOKEN":"${MISSING_ACP_TOKEN}"}}}},"echo",{"command":sys.executable})
    result=asyncio.run(AcpHarnessRunner().run(spec))
    assert result.error == "acp_environment_missing: MISSING_ACP_TOKEN"
    assert not called
