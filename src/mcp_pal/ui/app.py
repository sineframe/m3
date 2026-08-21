"""Streamlit client for the MCP Testing Platform API."""
import json, os, re, time
import requests
import streamlit as st
from mcp_pal.ui.health import health_state

API = os.getenv("MCP_PAL_API_URL", "http://localhost:8000/api/v1")
MAX_PREVIEW = 4000

def api(method, path, **kwargs):
    try:
        r=requests.request(method, API+path, timeout=10, **kwargs)
        if r.status_code >= 400: st.error(r.text); return None
        return r.json() if r.content else None
    except Exception as e: st.error(f"Backend unavailable: {e}"); return None

def truncate(value, limit=MAX_PREVIEW):
    text=value if isinstance(value,str) else json.dumps(value, ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + f"… ({len(text)-limit} more characters)"

def linkify(text):
    return re.sub(r"(https?://[^\s\]\)]+)", r"[\1](\1)", str(text))

def render_report(report):
    """Render a report while keeping large raw values available by download."""
    run=report.get("run",{}); assertions=report.get("assertions",{})
    c1,c2,c3=st.columns(3); c1.metric("Lifecycle",assertions.get("lifecycle",run.get("status"))); c2.metric("MCP assertion",assertions.get("mcp",{}).get("status")); c3.metric("Semantic assertion",assertions.get("semantic",{}).get("status"))
    if report.get("high_risk"): st.error("HIGH RISK: full tool mode used unrestricted auto-approval.")
    st.warning(report.get("warning","SQLite and reports contain unredacted data."))
    left,right=st.columns(2); left.subheader("Final Claude response"); left.markdown(linkify(truncate(run.get("claude_result") or ""))); right.subheader("Expected output"); right.write(run.get("expected_output", ""))
    st.subheader("MCP activity summary"); st.json(report.get("mcp_summary",{}))
    events=report.get("events",[])
    with st.expander(f"Normalized timeline ({len(events)} events)", expanded=False):
        for event in events:
            st.caption(f"#{event.get('sequence')} · {event.get('timestamp')} · {event.get('event_type')}")
            st.markdown(linkify(truncate(event.get("payload",{}))))
    with st.expander("Raw Claude events", expanded=False):
        for event in events: st.code(truncate(event.get("raw_event",{})))
    with st.expander("Thinking payloads", expanded=False):
        for event in events:
            if event.get("event_type") == "thinking": st.code(truncate(event.get("payload",{})))
    with st.expander("stderr", expanded=False): st.code(report.get("stderr") or "")

st.set_page_config(page_title="MCP Testing Platform", page_icon="🧪", layout="wide")
if "page" not in st.session_state: st.session_state["page"]="New Run"
def _new_run_page(): pass
def _profiles_page(): pass
def _history_page(): pass
navigation = st.navigation([
    st.Page(_new_run_page, title="New Run", icon="▶️"),
    st.Page(_profiles_page, title="MCP Profiles", icon="🧩"),
    st.Page(_history_page, title="Run History", icon="🕘"),
])
navigation.run()
page=navigation.title
st.session_state["page"] = page
try:
    health_payload=api("GET","/health")
    health=health_state(health_payload)
except Exception:
    health=health_state(request_error=True)
if health["connected"]:
    st.caption("● Backend connected" + (" · runner ready" if health["runner_ready"] else " · runner setup required"))
else:
    st.error("Backend unavailable")
caps=api("GET","/capabilities") or {"models":[]}

if page == "MCP Profiles":
    st.title("MCP Profiles")
    with st.form("new_profile"):
        name=st.text_input("Name"); desc=st.text_input("Description")
        default={"mcpServers":{"example":{"command":"npx","args":["-y","your-server"]}}}
        raw=st.text_area("MCP JSON", json.dumps(default, indent=2), height=260)
        if st.form_submit_button("Create profile"):
            try: api("POST","/profiles",json={"name":name,"description":desc,"mcp_json":json.loads(raw)}); st.rerun()
            except Exception as e: st.error(f"Invalid JSON: {e}")
    profiles=api("GET","/profiles",params={"include_archived":True}) or []
    for p in profiles:
        with st.expander(f"{p['name']} {'(archived)' if p['archived'] else ''}"):
            st.write(p.get("description","")); full=api("GET",f"/profiles/{p['id']}")
            if full:
                validation=full.get("validation",{})
                for warning in validation.get("warnings",[]): st.warning(warning)
                if validation.get("missing_environment_variables"): st.warning("Missing environment variables: "+", ".join(validation["missing_environment_variables"]))
                revs=full.get("revisions",[]); st.write(f"{len(revs)} revision(s)")
                sel=st.selectbox("Revision",revs,key=f"rev-{p['id']}",format_func=lambda x:f"Revision {x['revision_number']} · {x['created_at']}") if revs else None
                if sel: st.json(sel.get("mcp_json",{}))
            col1,col2=st.columns(2)
            if p['archived']:
                if col1.button("Restore",key=f"restore-{p['id']}"): api("POST",f"/profiles/{p['id']}/restore"); st.rerun()
            else:
                if col1.button("Archive",key=f"archive-{p['id']}"): api("POST",f"/profiles/{p['id']}/archive"); st.rerun()
                with col2.form(f"revision-{p['id']}"):
                    edit=st.text_area("New revision JSON", json.dumps((sel or {}).get("mcp_json",{}),indent=2))
                    if st.form_submit_button("Save revision"):
                        try: api("POST",f"/profiles/{p['id']}/revisions",json={"mcp_json":json.loads(edit)}); st.rerun()
                        except Exception as e: st.error(str(e))

elif page == "New Run":
    st.title("New Run")
    prefill=st.session_state.get("prefill",{})
    if prefill and st.button("Clear clone draft"):
        st.session_state["prefill"]={}; st.rerun()
    profiles=api("GET","/profiles") or []; options=[]
    for p in profiles:
        full=api("GET",f"/profiles/{p['id']}")
        options += [(p,r) for r in (full or {}).get("revisions",[])]
    if not options: st.info("Create an MCP profile first.")
    else:
        use_latest=st.checkbox("Use latest profile revision", value=False) if prefill else False
        if use_latest:
            profile_for_clone=next((x for x,r0 in options if r0["id"]==prefill.get("profile_revision_id")),None)
            latest_id=profile_for_clone.get("current_revision_id") if profile_for_clone else None
            if latest_id: prefill["profile_revision_id"]=latest_id
        default_index=next((i for i,x in enumerate(options) if x[1]["id"]==prefill.get("profile_revision_id")),0)
        p,r=st.selectbox("Profile and revision",options,index=default_index,format_func=lambda x:f"{x[0]['name']} · revision {x[1]['revision_number']}")
        servers=list((r.get("mcp_json") or {}).get("mcpServers",{})); default_server=prefill.get("enabled_server") if prefill.get("enabled_server") in servers else (servers[0] if servers else None)
        with st.form("run"):
            models=caps.get("models",[]); model=prefill.get("model") if prefill.get("model") in models else (models[0] if models else "")
            model=st.selectbox("Model",models,index=models.index(model) if model in models else 0); server=st.selectbox("Enabled server",servers,index=servers.index(default_server) if default_server else 0)
            modes=["mcp_only","mcp_read_only","full"]; mode=st.selectbox("Tool mode",modes,index=modes.index(prefill.get("tool_mode")) if prefill.get("tool_mode") in modes else 0)
            if mode == "full": st.error("HIGH RISK: full mode grants unrestricted auto-approval.")
            prompt=st.text_area("Prompt",value=prefill.get("prompt",""),height=180); expected=st.text_area("Expected output (required)",value=prefill.get("expected_output",""),height=100)
            if not health["runner_ready"]:
                with st.container(border=True):
                    st.subheader("Runner setup required")
                    for message in health["messages"]: st.warning(message)
            submitted=st.form_submit_button("Run",disabled=not health["runner_ready"])
            if submitted:
                if not prompt.strip() or not expected.strip(): st.error("Prompt and expected output are required")
                else:
                    result=api("POST","/runs",json={"model":model,"prompt":prompt,"expected_output":expected,"profile_revision_id":r['id'],"enabled_server":server,"tool_mode":mode})
                    if result: st.session_state["active_run"]=result['id']; st.session_state["prefill"]={}; st.rerun()
        active=st.session_state.get("active_run")
        if active:
            run=api("GET",f"/runs/{active}")
            if run and run['status'] in ("queued","running"):
                st.info(f"Status: {run['status']} · elapsed {run.get('elapsed_seconds',0):.1f}s · queue position {run.get('queue_position') or 'active'}")
                if st.button("Cancel"): api("POST",f"/runs/{active}/cancel"); st.rerun()
                time.sleep(1); st.rerun()
            elif run: st.success(f"Finished: {run['status']}"); report=api("GET",f"/runs/{active}/report"); render_report(report or {})

else:
    st.title("Run History")
    f1,f2,f3=st.columns(3); status_filter=f1.selectbox("Status",["all","queued","running","completed","failed","timed_out","cancelled"]); model_filter=f2.text_input("Model"); profile_filter=f3.text_input("Profile ID")
    params={};
    if status_filter != "all": params["status"]=status_filter
    if model_filter: params["model"]=model_filter
    if profile_filter: params["profile_id"]=profile_filter
    runs=api("GET","/runs",params=params) or []
    for run in runs:
        with st.expander(f"{run['created_at']} · {run['status']} · {run['model']}"):
            st.write(run.get("prompt","")); report=api("GET",f"/runs/{run['id']}/report")
            if report: render_report(report); st.download_button("Download complete JSON report",json.dumps(report,indent=2),f"{run['id']}.json","application/json")
            c1,c2,c3=st.columns(3)
            if c1.button("Clone to New Run",key=f"clone-{run['id']}"):
                st.session_state["prefill"]={"profile_revision_id":run["profile_revision_id"],"enabled_server":run["enabled_server"],"model":run["model"],"tool_mode":run["tool_mode"],"prompt":run["prompt"],"expected_output":run["expected_output"]}; st.session_state["page"]="New Run"; st.rerun()
            if c2.button("Cancel",key=f"cancel-{run['id']}"): api("POST",f"/runs/{run['id']}/cancel"); st.rerun()
            if c3.button("Delete",key=f"delete-{run['id']}"):
                if run["status"] in ("queued","running"): st.warning("Cancel and wait for completion before deleting")
                else: api("DELETE",f"/runs/{run['id']}"); st.rerun()
    if st.button("Clear all history"): st.session_state["confirm_clear"]=True
    if st.session_state.get("confirm_clear"):
        st.warning("This permanently deletes stored runs and traces.")
        if st.button("Confirm clear all"): api("DELETE","/runs",params={"confirm":True}); st.session_state["confirm_clear"]=False; st.rerun()
