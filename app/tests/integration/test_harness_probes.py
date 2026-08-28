import time
import asyncio, json, stat, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from mcp_pal_app.api import create_app
from mcp_pal_app.settings import Settings
from acp_fixture import probe_agent as _probe_agent

def test_protocol_probe_is_async_and_persists_latest_status(tmp_path):
    client=TestClient(create_app(Settings(database_path=str(tmp_path/'db.sqlite')))); p=client.post('/api/v1/harness-profiles',json={'name':'probe','manifest':{'command':'definitely-missing'},'trusted_unsandboxed':True}).json(); started=client.post(f"/api/v1/harness-profiles/{p['id']}/probe",params={'kind':'protocol'}); assert started.status_code==202
    for _ in range(50):
        probes=client.get(f"/api/v1/harness-profiles/{p['id']}/probes").json()
        if probes and probes[0]['status'] not in {'queued','running'}: break
        time.sleep(.01)
    assert probes[0]['status']=='failed' and probes[0]['evidence']['local_ready'] is False

def test_full_probe_requires_wire_nonce_and_response(tmp_path):
    agent=tmp_path/'agent.py'; agent.write_text("""#!/usr/bin/env python3
import json,sys
def s(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 r=json.loads(line); m=r.get('method')
 if m=='initialize': s({'jsonrpc':'2.0','id':r['id'],'result':{'protocolVersion':1,'agentInfo':{'name':'probe','version':'1'}}})
 elif m=='session/new': s({'jsonrpc':'2.0','id':r['id'],'result':{'sessionId':'s'}})
 elif m=='session/prompt': s({'jsonrpc':'2.0','id':r['id'],'result':{'stopReason':'end_turn'}})
"""); agent.chmod(stat.S_IXUSR|stat.S_IRUSR)
    from mcp_pal.harness.acp import full_probe
    evidence=asyncio.run(full_probe({'command':str(agent)}))
    assert evidence['status']=='failed' and evidence['calls']==[]

def test_api_full_probe_requires_verified_protocol(tmp_path):
    client=TestClient(create_app(Settings(database_path=str(tmp_path/'db.sqlite'))))
    profile=client.post('/api/v1/harness-profiles',json={'name':'no-protocol','manifest':{'command':'missing-agent'},'trusted_unsandboxed':True}).json()
    full=client.post(f"/api/v1/harness-profiles/{profile['id']}/probe",params={'kind':'full'})
    assert full.status_code==422
    from mcp_pal_app.persistence.models import HarnessProbe
    db=client.app.state.session_factory(); db.add(HarnessProbe(revision_id=profile['current_revision_id'],kind='protocol',status='failed',evidence={},agent_identity=None)); db.commit(); db.close()
    failed=client.post(f"/api/v1/harness-profiles/{profile['id']}/probe",params={'kind':'full'})
    assert failed.status_code==422

def test_capabilities_readiness_and_probe_dimensions_are_isolated(tmp_path):
    from mcp_pal_app.persistence.models import HarnessProbe
    client=TestClient(create_app(Settings(database_path=str(tmp_path/'db.sqlite')))); p=client.post('/api/v1/harness-profiles',json={'name':'missing','manifest':{'command':'missing-agent'},'trusted_unsandboxed':True}).json()
    caps=client.get('/api/v1/capabilities').json(); descriptor=next(x for x in caps['harnesses'] if x.get('profile_id')==p['id']); assert descriptor['local_ready'] is False
    db=client.app.state.session_factory(); db.add(HarnessProbe(revision_id=p['current_revision_id'],kind='protocol',status='verified',evidence={'modes':{'current_mode_id':'mode-a','available_modes':[{'id':'mode-a'}]},'config_options':[{'id':'a','type':'boolean'}]},agent_identity=None)); db.commit(); db.close()
    first=client.post(f"/api/v1/harness-profiles/{p['id']}/probe",params={'kind':'full','transport':'stdio','mode_id':'mode-a','session_config':'{"a":true}'}); assert first.status_code==202
    probes=client.get(f"/api/v1/harness-profiles/{p['id']}/probes").json(); assert probes[0]['mode_id']=='mode-a' and probes[0]['session_config']=={'a':True}

def test_probe_latest_dimension_and_identity_downgrade(tmp_path):
    from mcp_pal_app.persistence.models import HarnessProbe
    client=TestClient(create_app(Settings(database_path=str(tmp_path/'db.sqlite')))); p=client.post('/api/v1/harness-profiles',json={'name':'x','manifest':{'command':'echo'},'trusted_unsandboxed':True}).json()
    db=client.app.state.session_factory(); rev=p['current_revision_id']
    db.add_all([HarnessProbe(revision_id=rev,kind='protocol',status='verified',evidence={'modes':['m1'],'config_options':['x']},agent_identity={'name':'a'}),HarnessProbe(revision_id=rev,kind='full',status='verified',transport='stdio',mode_id='a',session_config={'z':1},evidence={},agent_identity={'name':'a'}),HarnessProbe(revision_id=rev,kind='full',status='verified',transport='stdio',mode_id='b',session_config={'z':2},evidence={},agent_identity={'name':'b'}),HarnessProbe(revision_id=rev,kind='full',status='failed',transport='stdio',mode_id='b',session_config={'z':2},evidence={},agent_identity={'name':'a'})]); db.commit(); db.close()
    descriptor=next(x for x in client.get('/api/v1/capabilities').json()['harnesses'] if x.get('profile_id')==p['id'])
    assert descriptor['protocol_verified'] and descriptor['full_verified'] and descriptor['agent_modes']==['m1'] and not any('Identity changed' in w for w in descriptor['warnings'])

def _profile_with_probes(tmp_path, probes):
    from mcp_pal_app.persistence.models import HarnessProbe
    client=TestClient(create_app(Settings(database_path=str(tmp_path/'db.sqlite')))); p=client.post('/api/v1/harness-profiles',json={'name':'x','manifest':{'command':'echo'},'trusted_unsandboxed':True}).json(); db=client.app.state.session_factory(); base=datetime(2020,1,1,tzinfo=timezone.utc)
    for i,kw in enumerate(probes): db.add(HarnessProbe(revision_id=p['current_revision_id'],created_at=base+timedelta(seconds=i),**kw))
    db.commit(); db.close(); return client,p

def test_latest_failed_protocol_hides_options(tmp_path):
    client,p=_profile_with_probes(tmp_path,[{'kind':'protocol','status':'verified','evidence':{'modes':['m']},'agent_identity':None},{'kind':'protocol','status':'failed','evidence':{},'agent_identity':None}]); d=next(x for x in client.get('/api/v1/capabilities').json()['harnesses'] if x.get('profile_id')==p['id']); assert not d['protocol_verified'] and d['agent_modes']==[]

def test_identity_mismatch_downgrades_full_and_missing_identity_warns(tmp_path):
    client,p=_profile_with_probes(tmp_path,[{'kind':'protocol','status':'verified','evidence':{},'agent_identity':{'name':'a'}},{'kind':'full','status':'verified','evidence':{},'agent_identity':{'name':'b'},'mode_id':'m'}]); d=next(x for x in client.get('/api/v1/capabilities').json()['harnesses'] if x.get('profile_id')==p['id']); assert not d['full_verified'] and any('Identity changed' in w for w in d['warnings'])
    (tmp_path/'second').mkdir(); client,p=_profile_with_probes(tmp_path/'second',[{'kind':'protocol','status':'verified','evidence':{},'agent_identity':None},{'kind':'full','status':'verified','evidence':{},'agent_identity':None,'mode_id':'m'}]); d=next(x for x in client.get('/api/v1/capabilities').json()['harnesses'] if x.get('profile_id')==p['id']); assert d['full_verified'] and any('identity unavailable' in w.lower() for w in d['warnings'])

def test_canonical_config_order_is_one_dimension_and_failed_latest_invalidates(tmp_path):
    client,p=_profile_with_probes(tmp_path,[{'kind':'full','status':'verified','evidence':{},'session_config':{'a':1,'b':2},'mode_id':'m'},{'kind':'full','status':'failed','evidence':{},'session_config':{'b':2,'a':1},'mode_id':'m'}]); d=next(x for x in client.get('/api/v1/capabilities').json()['harnesses'] if x.get('profile_id')==p['id']); assert not d['full_verified']


@pytest.mark.parametrize("transport", ["stdio", "http", "sse"])
def test_full_probe_real_transport_requires_exact_nonce_update_and_completion(tmp_path, transport):
    from mcp_pal.harness.acp import full_probe
    evidence = asyncio.run(full_probe({"command": _probe_agent(tmp_path / f"agent-{transport}.py")}, mode_id="mode-a", session_config={"quality": "high"}, transport=transport))
    assert evidence["status"] == "verified", evidence
    assert evidence["transport"] == transport
    assert evidence["prompt_completed"] and evidence["tool_update"] and evidence["message_update"]
    assert evidence["applied_mode"] and evidence["applied_config"] == {"quality": True}
    call = evidence["calls"][0]
    assert call["arguments"] == {"text": evidence["nonce"]}
    assert call["result"]["content"] == [{"type": "text", "text": evidence["nonce"]}]
    assert isinstance(call["latency_ms"], (int, float)) and call["latency_ms"] >= 0


@pytest.mark.parametrize("behavior", ["no_identity", "split_message"])
def test_full_probe_accepts_optional_identity_and_streamed_message_chunks(tmp_path, behavior):
    from mcp_pal.harness.acp import full_probe
    evidence=asyncio.run(full_probe({"command":_probe_agent(tmp_path/f"agent-{behavior}.py",behavior)},transport="stdio"))
    assert evidence["status"] == "verified", evidence
    assert evidence["identity_available"] is (behavior != "no_identity")


@pytest.mark.parametrize("behavior", ["wrong_args", "wrong_result", "no_update", "wrong_stop"])
def test_full_probe_rejects_structural_failures(tmp_path, behavior):
    from mcp_pal.harness.acp import full_probe
    evidence = asyncio.run(full_probe({"command": _probe_agent(tmp_path / f"agent-{behavior}.py", behavior)}, transport="stdio"))
    assert evidence["status"] == "failed"


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_api_full_probe_runs_real_local_transport_and_cleans_service(tmp_path, transport):
    client = TestClient(create_app(Settings(database_path=str(tmp_path / f"{transport}.sqlite"))))
    profile = client.post("/api/v1/harness-profiles", json={"name": transport, "manifest": {"command": _probe_agent(tmp_path / f"api-{transport}.py")}, "trusted_unsandboxed": True}).json()
    protocol_job=client.post(f"/api/v1/harness-profiles/{profile['id']}/probe", params={"kind":"protocol"})
    assert protocol_job.status_code == 202
    for _ in range(200):
        protocol_rows=client.get(f"/api/v1/harness-profiles/{profile['id']}/probes").json()
        if protocol_rows and protocol_rows[0]["status"] not in {"queued","running"}: break
        time.sleep(.02)
    assert protocol_rows[0]["status"] == "verified", protocol_rows
    descriptor=next(x for x in client.get('/api/v1/capabilities').json()['harnesses'] if x.get('profile_id')==profile['id'])
    assert descriptor['agent_modes'][0]['name'] == 'Mode A' and descriptor['current_agent_mode_id'] == 'default'
    started = client.post(f"/api/v1/harness-profiles/{profile['id']}/probe", params={"kind": "full", "transport": transport, "mode_id": "mode-a", "session_config": '{"quality":"high"}'})
    assert started.status_code == 202
    for _ in range(200):
        probes = client.get(f"/api/v1/harness-profiles/{profile['id']}/probes").json()
        if probes and probes[0]["status"] not in {"queued", "running"}:
            break
        time.sleep(.02)
    assert probes[0]["status"] == "verified", probes
    assert probes[0]["evidence"]["transport"] == transport
    assert probes[0]["evidence"]["applied_config"] == {"quality": True}
