import json
import stat
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from mcp_pal_app.api import create_app
from mcp_pal_app.settings import Settings


def exe(path, body):
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_acp_api_profile_snapshot_run_report(tmp_path):
    echo = Path(__file__).parents[1] / "fixtures" / "mcp_echo_server.py"
    agent = exe(
        tmp_path / "agent.py",
        """
import json,subprocess,sys
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 r=json.loads(line); m=r.get('method'); p=r.get('params') or {}
 if m=='initialize': send({'jsonrpc':'2.0','id':r['id'],'result':{'protocolVersion':1}})
 elif m=='session/new':
  c=p['mcpServers'][0]; s=subprocess.Popen([c['command'],*c.get('args',[])],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
  def rpc(i,m,p=None):
   q={'jsonrpc':'2.0','id':i,'method':m}; q['params']=p or {}; s.stdin.write(json.dumps(q)+'\\n'); s.stdin.flush(); return json.loads(s.stdout.readline())
  rpc(1,'initialize',{'protocolVersion':'2024-11-05'}); rpc(2,'tools/list'); send({'jsonrpc':'2.0','id':r['id'],'result':{'sessionId':'s'}})
 elif m=='session/prompt':
  rpc(3,'tools/call',{'name':'echo','arguments':{'text':'api-nonce'}}); send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'s','update':{'sessionUpdate':'agent_thought_chunk','content':{'type':'text','text':'thinking'}}}}); send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'s','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'api-nonce'}}}}); send({'jsonrpc':'2.0','id':r['id'],'result':{'stopReason':'end_turn'}}); s.terminate(); s.wait()
""",
    )
    settings = Settings(
        database_path=str(tmp_path / "db.sqlite"), run_timeout_seconds=5
    )
    with TestClient(create_app(settings)) as client:
        hp = client.post(
            "/api/v1/harness-profiles",
            json={
                "name": "agent",
                "manifest": {"command": agent},
                "trusted_unsandboxed": True,
            },
        ).json()
        mp = client.post(
            "/api/v1/profiles",
            json={
                "name": "mcp",
                "mcp_json": {
                    "mcpServers": {
                        "echo": {"command": sys.executable, "args": [str(echo)]}
                    }
                },
            },
        ).json()
        created = client.post(
            "/api/v1/runs",
            json={
                "harness": "acp",
                "harness_revision_id": hp["current_revision_id"],
                "model": "agent-default",
                "tool_mode": "agent_default",
                "prompt": "p",
                "expected_output": "api-nonce",
                "profile_revision_id": mp["current_revision_id"],
                "enabled_server": "echo",
            },
        )
        assert created.status_code == 202, created.text
        rid = created.json()["id"]
        for _ in range(200):
            state = client.get(f"/api/v1/runs/{rid}").json()
            if state["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        report = client.get(f"/api/v1/runs/{rid}/report").json()
    assert (
        state["status"] == "completed"
        and state["final_output"] == "api-nonce"
        and report["trace"]["schema"] == "acp.v2"
    )
    assert report["run"]["mcp_assertion"] == "passed"
    observed = report["run"]["harness_snapshot"]["verification"]["observed"]
    assert (
        observed["session_id"] == "s"
        and observed["configured_transport"] == "stdio"
        and observed["instrumented_transport"] == "stdio"
    )
    assert report["trace"]["mcp_calls"][0]["arguments"] == {"text": "api-nonce"}
    assert report["trace"]["mcp_calls"][0]["server_latency_ms"] >= 0
    kinds = {span["kind"] for span in report["trace"]["spans"]}
    assert {"model_turn", "tool_call", "mcp"} <= kinds
    assert not {"thinking", "text"} & kinds
    turn = next(
        span for span in report["trace"]["spans"] if span["kind"] == "model_turn"
    )
    assert {"thinking", "text", "tool_call"} <= {step["kind"] for step in turn["steps"]}
    assert report["trace"]["protocol_events"] and report["trace"]["mcp_protocol_events"]


def test_old_sqlite_schema_starts_with_additive_tables(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "create table mcp_profiles (id varchar(36) primary key, name varchar(200) not null, description text, archived boolean, current_revision_id varchar(36), created_at datetime, updated_at datetime)"
    )
    db.commit()
    db.close()
    client = TestClient(create_app(Settings(database_path=str(path))))
    assert client.get("/api/v1/health").status_code == 200


def test_acp_api_active_cancel_reaps_and_worker_recovers(tmp_path):
    pidfile = tmp_path / "pid"
    agent = exe(
        tmp_path / "hang.py",
        f"""
import json,os,time
open({str(pidfile)!r},'w').write(str(os.getpid()))
for line in __import__('sys').stdin:
 r=json.loads(line)
 if r.get('method')=='initialize': print(json.dumps({{'jsonrpc':'2.0','id':r['id'],'result':{{'protocolVersion':1}}}}),flush=True)
 elif r.get('method')=='session/new': print(json.dumps({{'jsonrpc':'2.0','id':r['id'],'result':{{'sessionId':'s'}}}}),flush=True)
 elif r.get('method')=='session/prompt': time.sleep(30)
""",
    )
    with TestClient(
        create_app(
            Settings(database_path=str(tmp_path / "db.sqlite"), run_timeout_seconds=20)
        )
    ) as client:
        hp = client.post(
            "/api/v1/harness-profiles",
            json={
                "name": "hang",
                "manifest": {"command": agent},
                "trusted_unsandboxed": True,
            },
        ).json()
        mp = client.post(
            "/api/v1/profiles",
            json={"name": "m", "mcp_json": {"mcpServers": {"e": {"command": "echo"}}}},
        ).json()
        body = {
            "harness": "acp",
            "harness_revision_id": hp["current_revision_id"],
            "model": "agent-default",
            "tool_mode": "agent_default",
            "prompt": "p",
            "expected_output": "x",
            "profile_revision_id": mp["current_revision_id"],
            "enabled_server": "e",
        }
        run = client.post("/api/v1/runs", json=body).json()
        rid = run["id"]
        for _ in range(100):
            if pidfile.exists():
                break
            time.sleep(0.02)
        assert client.post(f"/api/v1/runs/{rid}/cancel").status_code == 200
        for _ in range(100):
            state = client.get(f"/api/v1/runs/{rid}").json()
            if state["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
    assert state["status"] in {"cancelled", "failed"}
    try:
        __import__("os").kill(int(pidfile.read_text()), 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("ACP process survived API cancellation")


def test_acp_clone_preserves_snapshot_and_latest_override(tmp_path):
    from mcp_pal_app.persistence.models import HarnessProbe

    app = create_app(Settings(database_path=str(tmp_path / "db.sqlite")))
    client = TestClient(app)
    app.state.manager.submit = lambda _run_id: None
    a = exe(tmp_path / "a.py", "")
    hp = client.post(
        "/api/v1/harness-profiles",
        json={"name": "a", "manifest": {"command": a}, "trusted_unsandboxed": True},
    ).json()
    mp = client.post(
        "/api/v1/profiles",
        json={"name": "m", "mcp_json": {"mcpServers": {"e": {"command": "echo"}}}},
    ).json()
    db = client.app.state.session_factory()
    db.add(
        HarnessProbe(
            revision_id=hp["current_revision_id"],
            kind="protocol",
            status="verified",
            evidence={
                "config_options": [
                    {"id": "flag", "type": "select", "options": [{"value": "on"}]}
                ]
            },
            agent_identity={"name": "fixture", "version": "1"},
        )
    )
    db.commit()
    db.close()
    body = {
        "harness": "acp",
        "harness_revision_id": hp["current_revision_id"],
        "model": "agent-default",
        "tool_mode": "agent_default",
        "prompt": "p",
        "expected_output": "x",
        "profile_revision_id": mp["current_revision_id"],
        "enabled_server": "e",
        "session_config": {"flag": "on"},
    }
    original = client.post("/api/v1/runs", json=body).json()
    clone = client.post(f"/api/v1/runs/{original['id']}/clone", json={}).json()
    original_snapshot = client.get(f"/api/v1/runs/{original['id']}").json()[
        "harness_snapshot"
    ]
    clone_snapshot = client.get(f"/api/v1/runs/{clone['id']}").json()[
        "harness_snapshot"
    ]
    assert (
        client.get(f"/api/v1/runs/{clone['id']}").json()["harness"] == "acp"
        and clone_snapshot == original_snapshot
    )
    newer = client.post(
        f"/api/v1/harness-profiles/{hp['id']}/revisions",
        json={"name": "a", "manifest": {"command": a}, "trusted_unsandboxed": True},
    ).json()
    db = client.app.state.session_factory()
    db.add(
        HarnessProbe(
            revision_id=newer["id"],
            kind="protocol",
            status="verified",
            evidence={
                "config_options": [
                    {"id": "flag", "type": "select", "options": [{"value": "on"}]}
                ]
            },
            agent_identity={"name": "fixture", "version": "1"},
        )
    )
    db.commit()
    db.close()
    latest = client.post(
        f"/api/v1/runs/{original['id']}/clone",
        json={"use_latest_harness_revision": True},
    ).json()
    assert latest["id"] != clone["id"]


def test_acp_failure_persists_partial_trace(tmp_path):
    agent = exe(
        tmp_path / "bad.py",
        'print(\'{\\"jsonrpc\\":\\"2.0\\",\\"id\\":1,\\"result\\":{\\"protocolVersion\\":1}}\',flush=True); print(\'not-json\',flush=True)',
    )
    client = TestClient(create_app(Settings(database_path=str(tmp_path / "db.sqlite"))))
    hp = client.post(
        "/api/v1/harness-profiles",
        json={
            "name": "bad",
            "manifest": {"command": agent},
            "trusted_unsandboxed": True,
        },
    ).json()
    mp = client.post(
        "/api/v1/profiles",
        json={"name": "m", "mcp_json": {"mcpServers": {"e": {"command": "echo"}}}},
    ).json()
    run = client.post(
        "/api/v1/runs",
        json={
            "harness": "acp",
            "harness_revision_id": hp["current_revision_id"],
            "model": "agent-default",
            "tool_mode": "agent_default",
            "prompt": "p",
            "expected_output": "x",
            "profile_revision_id": mp["current_revision_id"],
            "enabled_server": "e",
        },
    ).json()
    for _ in range(100):
        report = client.get(f"/api/v1/runs/{run['id']}/report").json()
        if report["trace"]["available"]:
            break
        time.sleep(0.02)
    assert report["trace"]["available"]


def test_clone_server_override_recomputes_transport_verification(tmp_path):
    from mcp_pal_app.persistence.models import HarnessProbe

    app = create_app(Settings(database_path=str(tmp_path / "clone-server.sqlite")))
    client = TestClient(app)
    app.state.manager.submit = lambda _run_id: None
    agent = exe(tmp_path / "agent.py", "")
    hp = client.post(
        "/api/v1/harness-profiles",
        json={"name": "a", "manifest": {"command": agent}, "trusted_unsandboxed": True},
    ).json()
    mp = client.post(
        "/api/v1/profiles",
        json={
            "name": "m",
            "mcp_json": {
                "mcpServers": {
                    "local": {"command": "echo"},
                    "remote": {"type": "http", "url": "https://example.com/mcp"},
                }
            },
        },
    ).json()
    db = app.state.session_factory()
    db.add_all(
        [
            HarnessProbe(
                revision_id=hp["current_revision_id"],
                kind="protocol",
                status="verified",
                evidence={},
                agent_identity={"name": "a"},
            ),
            HarnessProbe(
                revision_id=hp["current_revision_id"],
                kind="full",
                status="verified",
                transport="stdio",
                session_config={},
                evidence={},
                agent_identity={"name": "a"},
            ),
        ]
    )
    db.commit()
    db.close()
    body = {
        "harness": "acp",
        "harness_revision_id": hp["current_revision_id"],
        "model": "agent-default",
        "tool_mode": "agent_default",
        "prompt": "p",
        "expected_output": "x",
        "profile_revision_id": mp["current_revision_id"],
        "enabled_server": "local",
    }
    original = client.post("/api/v1/runs", json=body).json()
    clone = client.post(
        f"/api/v1/runs/{original['id']}/clone", json={"enabled_server": "remote"}
    ).json()
    verification = clone["harness_snapshot"]["verification"]
    assert clone["enabled_server"] == "remote"
    assert verification["full_probe_dimensions"]["transport"] == "http"
    assert verification["full_probe_status"] == "unverified"


def test_grouped_select_is_valid_and_stale_full_probe_identity_is_downgraded(tmp_path):
    from mcp_pal_app.persistence.models import HarnessProbe

    app = create_app(Settings(database_path=str(tmp_path / "grouped.sqlite")))
    client = TestClient(app)
    app.state.manager.submit = lambda _run_id: None
    agent = exe(tmp_path / "agent.py", "")
    hp = client.post(
        "/api/v1/harness-profiles",
        json={"name": "a", "manifest": {"command": agent}, "trusted_unsandboxed": True},
    ).json()
    mp = client.post(
        "/api/v1/profiles",
        json={"name": "m", "mcp_json": {"mcpServers": {"e": {"command": "echo"}}}},
    ).json()
    grouped = {
        "id": "model",
        "name": "Model",
        "type": "select",
        "currentValue": "small",
        "options": [
            {
                "group": "models",
                "name": "Models",
                "options": [
                    {"value": "small", "name": "Small"},
                    {"value": "large", "name": "Large"},
                ],
            }
        ],
    }
    db = app.state.session_factory()
    db.add_all(
        [
            HarnessProbe(
                revision_id=hp["current_revision_id"],
                kind="protocol",
                status="verified",
                evidence={"config_options": [grouped]},
                agent_identity={"name": "new"},
            ),
            HarnessProbe(
                revision_id=hp["current_revision_id"],
                kind="full",
                status="verified",
                transport="stdio",
                session_config={"model": "large"},
                evidence={},
                agent_identity={"name": "old"},
            ),
        ]
    )
    db.commit()
    db.close()
    response = client.post(
        "/api/v1/runs",
        json={
            "harness": "acp",
            "harness_revision_id": hp["current_revision_id"],
            "model": "agent-default",
            "tool_mode": "agent_default",
            "prompt": "p",
            "expected_output": "x",
            "profile_revision_id": mp["current_revision_id"],
            "enabled_server": "e",
            "session_config": {"model": "large"},
        },
    )
    assert response.status_code == 202, response.text
    assert (
        response.json()["harness_snapshot"]["verification"]["full_probe_status"]
        == "identity_mismatch"
    )


def test_resolved_harness_secret_never_reaches_persisted_run_or_report(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PLAIN_SETTING", "persist-private-value")
    agent = exe(
        tmp_path / "secret-agent.py",
        """
import json,os,sys
for line in sys.stdin:
 r=json.loads(line); m=r.get('method')
 if m=='initialize': print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'protocolVersion':1}}),flush=True)
 elif m=='session/new': print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'sessionId':'s'}}),flush=True)
 elif m=='session/prompt':
  sys.stderr.write(os.environ['CHILD_VALUE']+'\\n'); sys.stderr.flush()
  print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'s','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':os.environ['CHILD_VALUE']}}}}),flush=True)
  print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'stopReason':'end_turn'}}),flush=True)
""",
    )
    with TestClient(
        create_app(
            Settings(
                database_path=str(tmp_path / "secret.sqlite"), run_timeout_seconds=3
            )
        )
    ) as client:
        hp = client.post(
            "/api/v1/harness-profiles",
            json={
                "name": "secret",
                "manifest": {
                    "command": agent,
                    "env": {"CHILD_VALUE": "${PLAIN_SETTING}"},
                },
                "trusted_unsandboxed": True,
            },
        ).json()
        mp = client.post(
            "/api/v1/profiles",
            json={"name": "m", "mcp_json": {"mcpServers": {"e": {"command": "echo"}}}},
        ).json()
        run = client.post(
            "/api/v1/runs",
            json={
                "harness": "acp",
                "harness_revision_id": hp["current_revision_id"],
                "model": "agent-default",
                "tool_mode": "agent_default",
                "prompt": "p",
                "expected_output": "x",
                "profile_revision_id": mp["current_revision_id"],
                "enabled_server": "e",
            },
        ).json()
        for _ in range(200):
            state = client.get(f"/api/v1/runs/{run['id']}").json()
            if state["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        report_response = client.get(f"/api/v1/runs/{run['id']}/report")
        report = report_response.json()
        events_response = client.get(f"/api/v1/runs/{run['id']}/events")
        events = events_response.json()
        api_run_response = client.get(f"/api/v1/runs/{run['id']}")
        api_run = api_run_response.json()
        raw_api = (
            report_response.content + events_response.content + api_run_response.content
        )
    persisted = json.dumps(
        {"state": state, "report": report, "events": events, "api_run": api_run}
    )
    assert "persist-private-value" not in persisted
    assert b"persist-private-value" not in raw_api
    assert "[REDACTED]" in persisted
    assert report["run"]["final_output"] == "[REDACTED]"
    assert report["stderr"] == "[REDACTED]\n"
