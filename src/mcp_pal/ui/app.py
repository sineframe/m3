"""Streamlit client for the MCP Testing Platform API."""
import html, json, os, re, time
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
    st.warning(report.get("warning","Trace payloads are redacted before persistence."))
    left,right=st.columns(2); left.subheader(f"Final {run.get('harness','harness')} response"); left.code(truncate(run.get("claude_result") or ""), language="text"); right.subheader("Expected output"); right.code(truncate(run.get("expected_output", "")), language="text")
    if run.get("harness") == "claude-code": render_trace(report.get("trace") or {}, key_suffix=str(run.get("id", "active")))
    st.subheader("MCP activity summary"); st.json(report.get("mcp_summary",{}))
    events=report.get("events",[])
    with st.expander(f"Normalized timeline ({len(events)} events)", expanded=False):
        for event in events:
            st.caption(f"#{event.get('sequence')} · {event.get('timestamp')} · {event.get('event_type')}")
            st.markdown(linkify(truncate(event.get("payload",{}))))
    with st.expander("Raw harness events", expanded=False):
        for event in events: st.code(truncate(event.get("raw_event",{})))
    with st.expander("Thinking payloads", expanded=False):
        for event in events:
            if event.get("event_type") == "thinking": st.code(truncate(event.get("payload",{})))
    with st.expander("stderr", expanded=False): st.code(report.get("stderr") or "")


def _ms(value, default=0.0):
    try: return float(value)
    except (TypeError, ValueError): return default


def render_trace(trace, key_suffix="active"):
    """Render a safe, dense Braintrust-style post-run waterfall."""
    if not trace.get("available"):
        st.info("Trace unavailable for this legacy run. New runs record Claude stream and MCP transport frames.")
        return
    summary=trace.get("summary") or {}; spans=trace.get("spans") or []
    transport=str(summary.get("transport") or "unknown").upper()
    display=lambda key, suffix="": f"{summary[key]}{suffix}" if summary.get(key) is not None else "—"
    metrics=[("Transport", transport), ("Wall time", display("duration_ms", " ms")), ("API time", display("duration_api_ms", " ms")), ("Turns", display("turns")), ("Tokens", display("total_tokens")), ("Cost", f"${summary['cost_usd']}" if summary.get("cost_usd") is not None else "—")]
    cols=st.columns(len(metrics))
    for col,(label,value) in zip(cols,metrics): col.metric(label,value)
    st.markdown("""
    <style>
      .trace-shell { background:#11151a; border:1px solid #2b333d; border-radius:8px; padding:14px 16px 8px; color:#d8e0e8; }
      .trace-kicker { color:#8795a5; font:600 10px ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing:.14em; text-transform:uppercase; }
      .trace-transport { color:#7ee787; font:700 12px ui-monospace, SFMono-Regular, Menlo, monospace; margin:4px 0 12px; }
      .trace-row { display:grid; grid-template-columns:minmax(210px,1.35fr) 72px minmax(180px,2fr) 72px; align-items:center; gap:9px; min-height:27px; border-top:1px solid #202831; font:12px ui-monospace, SFMono-Regular, Menlo, monospace; }
      .trace-name { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:#c9d1d9; }
      .trace-bar-wrap { height:12px; background:#1d252e; border-radius:2px; position:relative; }
      .trace-bar { height:100%; border-radius:2px; min-width:3px; } .trace-ok { background:#3fb950; } .trace-error { background:#f85149; } .trace-blue { background:#58a6ff; }
      .trace-head { color:#81909f; font-size:10px; letter-spacing:.08em; text-transform:uppercase; padding-bottom:5px; }
      .trace-dur { color:#8b949e; text-align:right; } .trace-kind { color:#8b949e; }
    </style>
    """, unsafe_allow_html=True)
    st.markdown(f'<div class="trace-shell"><div class="trace-kicker">Claude trace · {html.escape(str(trace.get("schema", "claude.v1")))}</div><div class="trace-transport">● MCP TRANSPORT: {html.escape(transport)}</div></div>', unsafe_allow_html=True)
    st.caption(f"Input {display('input_tokens')} · Output {display('output_tokens')} · Cache read {display('cache_read_input_tokens')} · Cache write {display('cache_creation_input_tokens')} · First output {display('time_to_first_output_ms', ' ms')}")
    if not spans:
        st.caption("No spans were captured.")
        return
    kinds=sorted({str(x.get("kind","unknown")) for x in spans})
    selected=st.multiselect("Span types", kinds, default=kinds, key=f"trace-span-filter-{key_suffix}")
    visible=[x for x in spans if str(x.get("kind","unknown")) in selected]
    total=max((_ms(x.get("end_ms"), _ms(x.get("start_ms"))) for x in visible), default=_ms(summary.get("duration_ms"),1.0)) or 1.0
    rows=['<div class="trace-row trace-head"><div>Span</div><div>Kind</div><div>Timeline</div><div>Time</div></div>']
    for span in visible:
        start=max(0.0,_ms(span.get("start_ms"))); end=max(start,_ms(span.get("end_ms"),start)); duration=max(0.0,end-start)
        left=min(100.0,start/total*100); width=max(0.7,min(100.0,duration/total*100)); status=str(span.get("status","completed"))
        color="trace-error" if status in {"error","failed"} else ("trace-blue" if span.get("kind") in {"model_turn","text","thinking"} else "trace-ok")
        indent=0 if span.get("parent_id") in (None,"run") else 16
        name=html.escape(str(span.get("name","span"))); kind=html.escape(str(span.get("kind","unknown")))
        rows.append(f'<div class="trace-row"><div class="trace-name" style="padding-left:{indent}px">{name}</div><div class="trace-kind">{kind}</div><div class="trace-bar-wrap"><div class="trace-bar {color}" style="margin-left:{left:.3f}%;width:{width:.3f}%"></div></div><div class="trace-dur">{duration:.1f} ms</div></div>')
    st.markdown('<div class="trace-shell">'+"".join(rows)+"</div>", unsafe_allow_html=True)
    st.caption("Select a span below for redacted input/output and correlation metadata.")
    for span in visible:
        with st.expander(f"{span.get('name','span')} · {span.get('duration_ms',0)} ms · {span.get('status','completed')}", expanded=False):
            st.json({"kind":span.get("kind"),"transport":span.get("transport"),"metadata":span.get("metadata"),"tokens":span.get("tokens")})
            if span.get("input") is not None: st.code(truncate(span.get("input")), language="json")
            if span.get("output") is not None: st.code(truncate(span.get("output")), language="json")
    with st.expander(f"Captured MCP protocol frames ({len(trace.get('protocol_events') or [])})", expanded=False):
        for event in trace.get("protocol_events") or []:
            st.caption(f"{event.get('offset_ms','—')} ms · {event.get('transport','—')} · {event.get('direction','—')}")
            st.code(truncate(event.get("payload",{})), language="json")

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
health_payload=None
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
            harnesses=caps.get("harnesses",["claude-code"]); preferred=prefill.get("harness") if prefill.get("harness") in harnesses else harnesses[0]
            harness=st.selectbox("Harness",harnesses,index=harnesses.index(preferred)); models=caps.get("models_by_harness",{}).get(harness,caps.get("models",[])); model=prefill.get("model") if prefill.get("model") in models else (models[0] if models else "")
            model=st.selectbox("Model",models,index=models.index(model) if model in models else 0); server=st.selectbox("Enabled server",servers,index=servers.index(default_server) if default_server else 0)
            modes=["mcp_only","mcp_read_only","full"]; mode=st.selectbox("Tool mode",modes,index=modes.index(prefill.get("tool_mode")) if prefill.get("tool_mode") in modes else 0)
            if mode == "full": st.error("HIGH RISK: full mode grants unrestricted auto-approval.")
            prompt=st.text_area("Prompt",value=prefill.get("prompt",""),height=180); expected=st.text_area("Expected output (required)",value=prefill.get("expected_output",""),height=100)
            harness_health=(health_payload or {}).get("harnesses",{}).get(harness,{}); selected_ready=harness_health.get("ready",health["runner_ready"])
            if not selected_ready:
                with st.container(border=True):
                    st.subheader(f"{harness} setup required")
                    st.warning("Check this harness's executable, CLI version, and API key or saved authentication.")
            submitted=st.form_submit_button("Run",disabled=not selected_ready)
            if submitted:
                if not prompt.strip() or not expected.strip(): st.error("Prompt and expected output are required")
                else:
                    result=api("POST","/runs",json={"harness":harness,"model":model,"prompt":prompt,"expected_output":expected,"profile_revision_id":r['id'],"enabled_server":server,"tool_mode":mode})
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
                st.session_state["prefill"]={"profile_revision_id":run["profile_revision_id"],"enabled_server":run["enabled_server"],"harness":run["harness"],"model":run["model"],"tool_mode":run["tool_mode"],"prompt":run["prompt"],"expected_output":run["expected_output"]}; st.session_state["page"]="New Run"; st.rerun()
            if c2.button("Cancel",key=f"cancel-{run['id']}"): api("POST",f"/runs/{run['id']}/cancel"); st.rerun()
            if c3.button("Delete",key=f"delete-{run['id']}"):
                if run["status"] in ("queued","running"): st.warning("Cancel and wait for completion before deleting")
                else: api("DELETE",f"/runs/{run['id']}"); st.rerun()
    if st.button("Clear all history"): st.session_state["confirm_clear"]=True
    if st.session_state.get("confirm_clear"):
        st.warning("This permanently deletes stored runs and traces.")
        if st.button("Confirm clear all"): api("DELETE","/runs",params={"confirm":True}); st.session_state["confirm_clear"]=False; st.rerun()
