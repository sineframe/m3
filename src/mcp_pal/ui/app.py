"""Streamlit client for the MCP Testing Platform API."""
import html, json, os, re, time
import requests
import streamlit as st
from mcp_pal.domain.builtin_profiles import EXCALIDRAW_MCP_CONFIG
from mcp_pal.ui.health import health_state
from mcp_pal.ui.trace_view import actor, display_name, format_duration, number, server_latency_for, visible_spans, wire_unavailable_message

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
    render_trace(report.get("trace") or {}, key_suffix=str(run.get("id", "active")))
    render_mcp_calls((report.get("trace") or {}).get("mcp_calls") or [])
    with st.expander("MCP activity summary", expanded=False):
        st.json(report.get("mcp_summary",{}))
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

def render_mcp_calls(calls):
    """Render backend-normalized calls without interpreting harness payloads."""
    st.subheader("MCP Calls")
    if not calls:
        st.caption("No selected-server MCP calls were captured.")
        return
    for call in calls:
        label = f"{call.get('server','—')} · {call.get('tool','—')} · {call.get('status','unknown')} · {format_duration(call.get('duration_ms'))}"
        with st.expander(label, expanded=False):
            st.caption(f"Model view · {call.get('harness','—')} · {call.get('transport','—')}")
            st.markdown("**Arguments**")
            st.code(truncate(call.get("arguments")), language="json")
            st.markdown("**Result**")
            st.code(truncate(call.get("result")), language="json")
            if call.get("error") is not None: st.error(truncate(call.get("error")))
            if call.get("wire_request") is not None or call.get("wire_response") is not None:
                st.caption(f"Wire view · server latency {format_duration(call.get('server_latency_ms'))}")
                st.markdown("**Raw wire request**"); st.code(truncate(call.get("wire_request")), language="json")
                st.markdown("**Raw wire response**"); st.code(truncate(call.get("wire_response")), language="json")
            else:
                st.caption(wire_unavailable_message(call.get("harness")))


def render_trace(trace, key_suffix="active"):
    """Render a chronological agent flow with optional protocol detail."""
    if not trace.get("available"):
        st.info("Trace unavailable for this legacy run.")
        return
    summary=trace.get("summary") or {}; spans=trace.get("spans") or []; harness=str(trace.get("harness") or "claude-code")
    transport=str(summary.get("transport") or "unknown").upper()
    call_count=len(trace.get("mcp_calls") or [])
    if harness == "opencode" and not spans:
        cols=st.columns(2); cols[0].metric("Transport", transport); cols[1].metric("MCP calls", call_count)
        if trace.get("limitations"): st.warning(" ".join(str(x) for x in trace["limitations"]))
        with st.expander(f"Captured MCP protocol frames ({len(trace.get('protocol_events') or [])})", expanded=False):
            for event in trace.get("protocol_events") or []:
                st.caption(f"{event.get('offset_ms','—')} ms · {event.get('direction','—')}")
                st.code(truncate(event.get("payload",{})), language="json")
        return
    display=lambda key: summary[key] if summary.get(key) is not None else "—"
    turn_label="OpenCode steps" if harness == "opencode" else "Claude turns"
    metrics=[("Transport", transport), ("Total time", format_duration(summary.get("duration_ms"))), (turn_label, display("turns")), ("MCP calls", call_count), ("Tokens", display("total_tokens")), ("Cost", f"${summary['cost_usd']:.4f}" if isinstance(summary.get("cost_usd"),(int,float)) else "—")]
    cols=st.columns(len(metrics))
    for col,(label,value) in zip(cols,metrics): col.metric(label,value)
    st.markdown("""
    <style>
      .trace-shell { background:#10151b; border:1px solid #2a3540; border-radius:10px; padding:10px 16px 12px; color:#d8e0e8; box-shadow:inset 0 1px 0 rgba(255,255,255,.025); }
      .trace-kicker { color:#8291a2; font:600 10px Menlo, Monaco, monospace; letter-spacing:.14em; text-transform:uppercase; }
      .trace-transport { color:#64d8cb; font:700 12px Menlo, Monaco, monospace; margin-top:5px; }
      .trace-row { display:grid; grid-template-columns:minmax(240px,1.2fr) 92px minmax(240px,2fr) 92px; align-items:center; column-gap:14px; min-height:43px; border-top:1px solid #202b35; font:12px Menlo, Monaco, monospace; }
      .trace-name { min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:#e3e9ef; font-weight:600; }
      .trace-sub { color:#728091; font-size:10px; font-weight:400; margin-top:2px; }
      .trace-actor { white-space:nowrap; color:#8e9baa; font-size:10px; text-transform:uppercase; letter-spacing:.06em; }
      .trace-bar-wrap { height:10px; background:#1b252f; border-radius:999px; position:relative; overflow:visible; }
      .trace-bar { position:absolute; top:0; height:100%; border-radius:999px; min-width:3px; box-shadow:0 0 10px currentColor; }
      .trace-claude { color:#57a6ff; background:#57a6ff; } .trace-thinking { color:#b792ff; background:#b792ff; } .trace-mcp { color:#f6a84b; background:#f6a84b; } .trace-server { color:#49c87a; background:#49c87a; } .trace-error { color:#ff6464; background:#ff6464; }
      .trace-head { color:#778697; font-size:9px; letter-spacing:.11em; text-transform:uppercase; min-height:30px; }
      .trace-dur { color:#aab5c0; text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
      .trace-legend { display:flex; gap:18px; color:#7f8c9a; font:10px Menlo, Monaco, monospace; margin:8px 0 3px; }
      .trace-dot { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:6px; }
    </style>
    """, unsafe_allow_html=True)
    st.markdown(f'<div class="trace-shell"><div class="trace-kicker">{html.escape(harness)} trace · {html.escape(str(trace.get("schema", "claude.v1")))}</div><div class="trace-transport">● MCP TRANSPORT: {html.escape(transport)}</div></div>', unsafe_allow_html=True)
    st.caption(f"Input {display('input_tokens')} · Output {display('output_tokens')} · Cache read {display('cache_read_input_tokens')} · Cache write {display('cache_creation_input_tokens')} · First output {format_duration(summary.get('time_to_first_output_ms')) if summary.get('time_to_first_output_ms') is not None else '—'}")
    if not spans:
        st.caption("No agent waterfall spans were captured; backend MCP calls and protocol frames are shown below.")
        if trace.get("limitations"):
            st.warning(" ".join(str(x) for x in trace["limitations"]))
        with st.expander(f"Captured MCP protocol frames ({len(trace.get('protocol_events') or [])})", expanded=False):
            for event in trace.get("protocol_events") or []:
                st.caption(f"{event.get('offset_ms','—')} ms · {event.get('direction','—')}")
                st.code(truncate(event.get("payload",{})), language="json")
        return
    show_protocol=st.toggle("Show protocol events",value=False,key=f"trace-protocol-{key_suffix}",help="Adds initialize, tools/list, tools/call, notifications, and response frames.")
    visible=visible_spans(spans,include_protocol=show_protocol)
    total=max(number(summary.get("duration_ms")),max((number(x.get("end_ms")) for x in visible),default=1.0),1.0)
    agent_label="OpenCode" if harness == "opencode" else "Claude"
    rows=[f'<div class="trace-legend"><span><i class="trace-dot trace-claude"></i>{agent_label}</span><span><i class="trace-dot trace-thinking"></i>Thinking</span><span><i class="trace-dot trace-mcp"></i>MCP round trip</span><span><i class="trace-dot trace-server"></i>Server</span></div>', '<div class="trace-row trace-head"><div>Activity</div><div>Actor</div><div>Elapsed time</div><div>Duration</div></div>']
    for span in visible:
        start=max(0.0,number(span.get("start_ms"))); end=max(start,number(span.get("end_ms"),start)); duration=max(0.0,end-start)
        left=min(100.0,start/total*100); width=max(0.45,min(100.0-left,duration/total*100)); status=str(span.get("status","completed")); kind=str(span.get("kind",""))
        selected_mcp=(span.get("metadata") or {}).get("mcp_selected", True)
        color="trace-error" if status in {"error","failed"} else ("trace-thinking" if kind=="thinking" else "trace-claude" if kind in {"model_turn","text"} or (kind=="tool_call" and not selected_mcp) else "trace-mcp" if kind=="tool_call" else "trace-server")
        indent=18 if kind in {"thinking","text","mcp","mcp_event"} else 0
        name=html.escape(display_name(span)); who=html.escape(actor(span)); latency=server_latency_for(span,spans)
        sub=f"Server {format_duration(latency)} · other observed time {format_duration(max(0,duration-latency))}" if latency is not None else ""
        rows.append(f'<div class="trace-row"><div class="trace-name" style="padding-left:{indent}px">{name}<div class="trace-sub">{html.escape(sub)}</div></div><div class="trace-actor">{who}</div><div class="trace-bar-wrap"><div class="trace-bar {color}" style="left:{left:.3f}%;width:{width:.3f}%"></div></div><div class="trace-dur">{format_duration(duration)}</div></div>')
    st.markdown('<div class="trace-shell">'+"".join(rows)+"</div>", unsafe_allow_html=True)
    st.caption(f"MCP call duration is {agent_label}-observed round-trip time. Server duration is measured from captured JSON-RPC request and response frames; the remainder includes transport and harness overhead.")
    if visible:
        selected_id=st.selectbox("Inspect activity",[span.get("id") for span in visible],key=f"trace-inspect-{key_suffix}",format_func=lambda ident: display_name(next(span for span in visible if span.get("id")==ident)))
        selected=next(span for span in visible if span.get("id")==selected_id)
        with st.container(border=True):
            st.caption(f"{actor(selected)} · {format_duration(selected.get('duration_ms'))} · {selected.get('status','completed')}")
            if selected.get("input") is not None: st.markdown("**Input**"); st.code(truncate(selected.get("input")),language="json")
            if selected.get("output") is not None: st.markdown("**Output**"); st.code(truncate(selected.get("output")),language="json")
            with st.expander("Metadata",expanded=False): st.json({"transport":selected.get("transport"),"tokens":selected.get("tokens"),"metadata":selected.get("metadata")})
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
        raw=st.text_area("MCP JSON", json.dumps(EXCALIDRAW_MCP_CONFIG, indent=2), height=260)
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
        # Harness and model must stay outside the form: Streamlit batches form
        # widget changes until submission, but the model options depend on the
        # selected harness and need to refresh immediately.
        harnesses=caps.get("harnesses",["claude-code"]); preferred=prefill.get("harness") if prefill.get("harness") in harnesses else harnesses[0]
        harness=st.selectbox("Harness",harnesses,index=harnesses.index(preferred)); models=caps.get("models_by_harness",{}).get(harness,caps.get("models",[])); model=prefill.get("model") if prefill.get("model") in models else (models[0] if models else "")
        model=st.selectbox("Model",models,index=models.index(model) if model in models else 0)
        with st.form("run"):
            server=st.selectbox("Enabled server",servers,index=servers.index(default_server) if default_server else 0)
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
