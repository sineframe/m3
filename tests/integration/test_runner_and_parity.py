import asyncio, json, os, stat, tempfile, time
from pathlib import Path
from fastapi.testclient import TestClient
from mcp_pal.api import create_app
from mcp_pal.config import Settings
from mcp_pal.domain.events import derive_mcp_assertion, derive_mcp_summary, normalize_events
from mcp_pal.harness.claude_cli import ClaudeCodeRunner, RunSpec

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
