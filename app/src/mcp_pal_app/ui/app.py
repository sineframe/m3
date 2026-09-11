"""Streamlit client for the MCP Testing Platform API."""

import html
import json
import os
import re
import time

import requests
import streamlit as st

from mcp_pal_app.builtin_profiles import EXCALIDRAW_MCP_CONFIG
from mcp_pal_app.ui.health import health_state
from mcp_pal_app.ui.trace_view import (
    actor,
    display_name,
    format_duration,
    number,
    preview_value,
    server_latency_for,
    visible_spans,
    wire_unavailable_message,
)

API = os.getenv("MCP_PAL_API_URL", "http://localhost:8000/api/v1")
MAX_PREVIEW = 4000


def api(method, path, **kwargs):
    try:
        r = requests.request(method, API + path, timeout=10, **kwargs)
        if r.status_code >= 400:
            st.error(r.text)
            return None
        return r.json() if r.content else None
    except Exception as e:
        st.error(f"Backend unavailable: {e}")
        return None


def capability_descriptors(payload):
    """Return the unified capability descriptors, tolerating the old shape.

    The API's selection id is deliberately the only value used to identify a
    harness in the form.  This matters when two ACP profiles have the same
    protocol and model sentinel but different revisions/manifests.
    """
    payload = payload or {}
    descriptors = payload.get("harnesses") or []
    if descriptors and isinstance(descriptors[0], str):
        models_by_harness = payload.get("models_by_harness") or {}
        descriptors = [
            {
                "selection_id": value,
                "kind": "builtin",
                "harness": value,
                "name": value,
                "ready": True,
                "models": models_by_harness.get(value, payload.get("models", [])),
                "tool_modes": ["mcp_only", "mcp_read_only", "full"],
            }
            for value in descriptors
        ]
    return [
        dict(item)
        for item in descriptors
        if isinstance(item, dict) and item.get("selection_id")
    ]


def _option_id(option):
    return (
        str(option.get("id") or option.get("name") or "option")
        if isinstance(option, dict)
        else str(option)
    )


def _option_label(option):
    return (
        str(option.get("name") or option.get("label") or option.get("id") or "option")
        if isinstance(option, dict)
        else str(option)
    )


def _select_values(option):
    values = (option or {}).get("options", []) if isinstance(option, dict) else []
    result = []
    for value in values:
        if isinstance(value, dict):
            if "value" in value:
                result.append(value["value"])
            elif isinstance(value.get("options"), list):
                result.extend(_select_values(value))
            else:
                result.append(value.get("id", value.get("name")))
        else:
            result.append(value)
    return result


def _config_default(option):
    """Get an ACP option default without coercing typed values."""
    if not isinstance(option, dict):
        return None
    # ACP SDK responses use ``currentValue`` (and Python-facing integrations
    # commonly expose its snake_case spelling) for the displayed selection.
    # Prefer it over a schema default, then use the default aliases before
    # falling back to the first choice/boolean value.
    for key in ("currentValue", "current_value", "default", "default_value"):
        if key in option and option[key] is not None:
            return option[key]
    values = _select_values(option)
    return (
        values[0]
        if values
        else (
            False if str(option.get("type", "")).lower() in {"boolean", "bool"} else ""
        )
    )


def _render_config_option(option, key_prefix, override=None):
    """Render one ACP boolean/select option and return its exact typed value."""
    oid = _option_id(option)
    label = _option_label(option)
    kind = (
        str((option or {}).get("type", "select")).lower()
        if isinstance(option, dict)
        else "select"
    )
    default = _config_default(option) if override is None else override
    key = f"{key_prefix}-{oid}"
    if kind in {"boolean", "bool"} or isinstance(default, bool):
        return oid, st.checkbox(label, value=bool(default), key=key)
    values = _select_values(option)
    if not values:
        values = [default]
    if default not in values:
        values.insert(0, default)
    index = values.index(default)
    return oid, st.selectbox(
        label, values, index=index, key=key, format_func=lambda value: str(value)
    )


def _mode_id(mode):
    return (
        str(mode.get("id") or mode.get("modeId") or mode.get("name"))
        if isinstance(mode, dict)
        else str(mode)
    )


def _mode_label(mode):
    return (
        str(mode.get("name") or mode.get("label") or mode.get("id"))
        if isinstance(mode, dict)
        else str(mode)
    )


def _badge(label, ready, detail=None):
    text = f"{'✓' if ready else '○'} {label}"
    if detail:
        text += f" · {detail}"
    (st.success if ready else st.warning)(text)


def truncate(value, limit=MAX_PREVIEW):
    projected = preview_value(value)
    text = (
        projected
        if isinstance(projected, str)
        else json.dumps(projected, ensure_ascii=False, indent=2)
    )
    return (
        text
        if len(text) <= limit
        else text[:limit] + f"… ({len(text) - limit} more characters)"
    )


def linkify(text):
    return re.sub(r"(https?://[^\s\]\)]+)", r"[\1](\1)", str(text))


def render_report(report):
    """Render a report while keeping large raw values available by download."""
    run = report.get("run", {})
    assertions = report.get("assertions", {})
    c1, c2, c3 = st.columns(3)
    c1.metric("Lifecycle", assertions.get("lifecycle", run.get("status")))
    c2.metric("MCP assertion", assertions.get("mcp", {}).get("status"))
    c3.metric("Semantic assertion", assertions.get("semantic", {}).get("status"))
    if report.get("high_risk"):
        st.error("HIGH RISK: full tool mode used unrestricted auto-approval.")
    st.warning(report.get("warning", "Trace payloads are redacted before persistence."))
    harness = run.get("harness", "harness")
    final_output = (
        run.get("final_output") if harness == "acp" else run.get("claude_result")
    )
    left, right = st.columns(2)
    left.subheader(f"Final {harness} response")
    left.code(truncate(final_output or ""), language="text")
    right.subheader("Expected output")
    right.code(truncate(run.get("expected_output", "")), language="text")
    trace = report.get("trace") or {}
    # ACP reports are rendered exclusively from the backend-normalized trace
    # trace.  Legacy event rows and vendor payloads must not become a second,
    # conflicting source of MCP evidence.
    if harness == "acp":
        if not str(trace.get("schema") or "").startswith("acp.v"):
            st.info("ACP normalized trace is unavailable for this run.")
        else:
            render_trace(trace, key_suffix=str(run.get("id", "active")))
            render_mcp_calls(trace.get("mcp_calls") or [])
    else:
        render_trace(trace, key_suffix=str(run.get("id", "active")))
        render_mcp_calls(trace.get("mcp_calls") or [])
    with st.expander("MCP activity summary", expanded=False):
        st.json(report.get("mcp_summary", {}))
    events = [] if harness == "acp" else report.get("events", [])
    with st.expander(f"Normalized timeline ({len(events)} events)", expanded=False):
        for event in events:
            st.caption(
                f"#{event.get('sequence')} · {event.get('timestamp')} · {event.get('event_type')}"
            )
            st.markdown(linkify(truncate(event.get("payload", {}))))
    with st.expander("Raw harness events", expanded=False):
        for event in events:
            st.code(truncate(event.get("raw_event", {})))
    with st.expander("stderr", expanded=False):
        st.code(report.get("stderr") or "")


def render_mcp_calls(calls):
    """Render backend-normalized calls without interpreting harness payloads."""
    st.subheader("MCP Calls")
    if not calls:
        st.caption("No selected-server MCP calls were captured.")
        return
    for call in calls:
        label = f"{call.get('server', '—')} · {call.get('tool', '—')} · {call.get('status', 'unknown')} · {format_duration(call.get('duration_ms'))}"
        with st.expander(label, expanded=False):
            st.caption(
                f"Model view · {call.get('harness', '—')} · {call.get('transport', '—')}"
            )
            st.markdown("**Arguments**")
            st.code(truncate(call.get("arguments")), language="json")
            st.markdown("**Result**")
            st.code(truncate(call.get("result")), language="json")
            if call.get("error") is not None:
                st.error(truncate(call.get("error")))
            if (
                call.get("wire_request") is not None
                or call.get("wire_response") is not None
            ):
                st.caption(
                    f"Wire view · server latency {format_duration(call.get('server_latency_ms'))}"
                )
                st.markdown("**Raw wire request**")
                st.code(truncate(call.get("wire_request")), language="json")
                st.markdown("**Raw wire response**")
                st.code(truncate(call.get("wire_response")), language="json")
            else:
                st.caption(wire_unavailable_message(call.get("harness")))


def render_trace(trace, key_suffix="active"):
    """Render a chronological agent flow with optional protocol detail."""
    if not trace.get("available"):
        st.info("Trace unavailable for this legacy run.")
        return
    summary = trace.get("summary") or {}
    spans = trace.get("spans") or []
    harness = str(trace.get("harness") or "claude-code")
    transport = str(summary.get("transport") or "unknown").upper()
    configured_transport = summary.get("configured_transport") or trace.get(
        "configured_transport"
    )
    instrumented_transport = summary.get("instrumented_transport") or trace.get(
        "instrumented_transport"
    )
    call_count = len(trace.get("mcp_calls") or [])
    if harness == "opencode" and not spans:
        cols = st.columns(2)
        cols[0].metric("Transport", transport)
        cols[1].metric("MCP calls", call_count)
        if trace.get("limitations"):
            st.warning(" ".join(str(x) for x in trace["limitations"]))
        with st.expander(
            f"Captured MCP protocol frames ({len(trace.get('protocol_events') or [])})",
            expanded=False,
        ):
            for event in trace.get("protocol_events") or []:
                st.caption(
                    f"{event.get('offset_ms', '—')} ms · {event.get('direction', '—')}"
                )
                st.code(truncate(event.get("payload", {})), language="json")
        return
    display = lambda key: summary[key] if summary.get(key) is not None else "—"
    turn_label = (
        "OpenCode steps"
        if harness == "opencode"
        else ("ACP turn" if harness == "acp" else "Claude turns")
    )
    metrics = [
        ("Transport", transport),
        ("Total time", format_duration(summary.get("duration_ms"))),
        (turn_label, display("turns")),
        ("MCP calls", call_count),
        ("Tokens", display("total_tokens")),
        (
            "Cost",
            f"${summary['cost_usd']:.4f}"
            if isinstance(summary.get("cost_usd"), (int, float))
            else "—",
        ),
    ]
    cols = st.columns(len(metrics))
    for col, (label, value) in zip(cols, metrics, strict=False):
        col.metric(label, value)
    st.markdown(
        """
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
    """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="trace-shell"><div class="trace-kicker">{html.escape(harness)} trace · {html.escape(str(trace.get("schema", "claude.v1")))}</div><div class="trace-transport">● MCP TRANSPORT: {html.escape(transport)}</div></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"Input {display('input_tokens')} · Output {display('output_tokens')} · Cache read {display('cache_read_input_tokens')} · Cache write {display('cache_creation_input_tokens')} · First output {format_duration(summary.get('time_to_first_output_ms')) if summary.get('time_to_first_output_ms') is not None else '—'}"
    )
    if configured_transport or instrumented_transport:
        st.caption(
            f"Configured transport: {configured_transport or '—'} · Instrumented transport: {instrumented_transport or '—'}"
        )
    if not spans:
        st.caption(
            "No agent waterfall spans were captured; backend MCP calls and protocol frames are shown below."
        )
        if trace.get("limitations"):
            st.warning(" ".join(str(x) for x in trace["limitations"]))
        with st.expander(
            f"Captured MCP protocol frames ({len(trace.get('protocol_events') or [])})",
            expanded=False,
        ):
            for event in trace.get("protocol_events") or []:
                st.caption(
                    f"{event.get('offset_ms', '—')} ms · {event.get('direction', '—')}"
                )
                st.code(truncate(event.get("payload", {})), language="json")
        return
    show_protocol = st.toggle(
        "Show protocol events",
        value=False,
        key=f"trace-protocol-{key_suffix}",
        help="Adds initialize, tools/list, tools/call, notifications, and response frames.",
    )
    visible = visible_spans(spans, include_protocol=show_protocol)
    total = max(
        number(summary.get("duration_ms")),
        max((number(x.get("end_ms")) for x in visible), default=1.0),
        1.0,
    )
    agent_label = (
        "OpenCode"
        if harness == "opencode"
        else ("ACP" if harness == "acp" else "Claude")
    )
    rows = [
        f'<div class="trace-legend"><span><i class="trace-dot trace-claude"></i>{agent_label}</span><span><i class="trace-dot trace-thinking"></i>Thinking</span><span><i class="trace-dot trace-mcp"></i>MCP round trip</span><span><i class="trace-dot trace-server"></i>Server</span></div>',
        '<div class="trace-row trace-head"><div>Activity</div><div>Actor</div><div>Elapsed time</div><div>Duration</div></div>',
    ]
    for span in visible:
        start = max(0.0, number(span.get("start_ms")))
        end = max(start, number(span.get("end_ms"), start))
        duration = max(0.0, end - start)
        left = min(100.0, start / total * 100)
        width = max(0.45, min(100.0 - left, duration / total * 100))
        status = str(span.get("status", "completed"))
        kind = str(span.get("kind", ""))
        selected_mcp = (span.get("metadata") or {}).get("mcp_selected", True)
        color = (
            "trace-error"
            if status in {"error", "failed"}
            else (
                "trace-thinking"
                if kind == "thinking"
                else "trace-claude"
                if kind in {"model_turn", "text"}
                or (kind == "tool_call" and not selected_mcp)
                else "trace-mcp"
                if kind == "tool_call"
                else "trace-server"
            )
        )
        indent = 18 if kind in {"thinking", "text", "mcp", "mcp_event"} else 0
        name = html.escape(display_name(span))
        who = html.escape(actor(span))
        latency = server_latency_for(span, spans)
        sub = (
            f"Server {format_duration(latency)} · other observed time {format_duration(max(0, duration - latency))}"
            if latency is not None
            else ""
        )
        rows.append(
            f'<div class="trace-row"><div class="trace-name" style="padding-left:{indent}px">{name}<div class="trace-sub">{html.escape(sub)}</div></div><div class="trace-actor">{who}</div><div class="trace-bar-wrap"><div class="trace-bar {color}" style="left:{left:.3f}%;width:{width:.3f}%"></div></div><div class="trace-dur">{format_duration(duration)}</div></div>'
        )
    st.markdown(
        '<div class="trace-shell">' + "".join(rows) + "</div>", unsafe_allow_html=True
    )
    st.caption(
        f"MCP call duration is {agent_label}-observed round-trip time. Server duration is measured from captured JSON-RPC request and response frames; the remainder includes transport and harness overhead."
    )
    if visible:
        selected_id = st.selectbox(
            "Inspect activity",
            [span.get("id") for span in visible],
            key=f"trace-inspect-{key_suffix}",
            format_func=lambda ident: display_name(
                next(span for span in visible if span.get("id") == ident)
            ),
        )
        selected = next(span for span in visible if span.get("id") == selected_id)
        with st.container(border=True):
            st.caption(
                f"{actor(selected)} · {format_duration(selected.get('duration_ms'))} · {selected.get('status', 'completed')}"
            )
            if selected.get("steps"):
                st.markdown("**Model steps**")
                for step in selected["steps"]:
                    st.caption(
                        f"#{step.get('sequence')} · {step.get('kind')} · {format_duration(step.get('duration_ms'))}"
                    )
                    if step.get("input") is not None:
                        st.code(truncate(step.get("input")), language="json")
                    if step.get("output") is not None:
                        st.code(
                            truncate(step.get("output")),
                            language="text"
                            if isinstance(step.get("output"), str)
                            else "json",
                        )
            if selected.get("input") is not None:
                st.markdown("**Input**")
                st.code(truncate(selected.get("input")), language="json")
            if selected.get("output") is not None:
                st.markdown("**Output**")
                st.code(truncate(selected.get("output")), language="json")
            with st.expander("Metadata", expanded=False):
                st.json(
                    {
                        "transport": selected.get("transport"),
                        "tokens": selected.get("tokens"),
                        "metadata": selected.get("metadata"),
                    }
                )
    with st.expander(
        f"Captured MCP protocol frames ({len(trace.get('protocol_events') or [])})",
        expanded=False,
    ):
        for event in trace.get("protocol_events") or []:
            st.caption(
                f"{event.get('offset_ms', '—')} ms · {event.get('transport', '—')} · {event.get('direction', '—')}"
            )
            st.code(truncate(event.get("payload", {})), language="json")


st.set_page_config(page_title="MCP Testing Platform", page_icon="🧪", layout="wide")
if "page" not in st.session_state:
    st.session_state["page"] = "New Run"


def _new_run_page():
    pass


def _profiles_page():
    pass


def _history_page():
    pass


def _harness_page():
    pass


navigation = st.navigation(
    [
        st.Page(_new_run_page, title="New Run", icon="▶️"),
        st.Page(_profiles_page, title="MCP Profiles", icon="🧩"),
        st.Page(_history_page, title="Run History", icon="🕘"),
        st.Page(_harness_page, title="Harness Profiles", icon="⚙️"),
    ]
)
navigation.run()
page = navigation.title
st.session_state["page"] = page
health_payload = None
try:
    health_payload = api("GET", "/health")
    health = health_state(health_payload)
except Exception:
    health = health_state(request_error=True)
if health["connected"]:
    st.caption(
        "● Backend connected"
        + (" · runner ready" if health["runner_ready"] else " · runner setup required")
    )
else:
    st.error("Backend unavailable")
caps = api("GET", "/capabilities") or {"models": []}

if page == "Harness Profiles":
    st.title("Harness Profiles")
    st.caption(
        "Register ACP agents launched directly over stdio. Credentials are referenced by host environment name and are never stored here."
    )
    with st.expander("Create a harness profile", expanded=True):
        with st.form("new_harness"):
            name = st.text_input("Name", placeholder="My local agent")
            description = st.text_input(
                "Description", placeholder="What this harness is used for"
            )
            command = st.text_input(
                "Executable command", placeholder="my-agent or /absolute/path/my-agent"
            )
            args_raw = st.text_area(
                "Arguments (JSON array)", "[]", help='Example: ["--acp", "--verbose"]'
            )
            env_raw = st.text_area(
                "Environment references (JSON object)",
                "{}",
                help='Example: {"OPENAI_API_KEY": "${TEAM_OPENAI_KEY}"}',
            )
            trusted = st.checkbox(
                "I acknowledge this executable is trusted and will run unsandboxed"
            )
            if st.form_submit_button("Create immutable revision"):
                try:
                    args = json.loads(args_raw)
                    env = json.loads(env_raw)
                    if not isinstance(args, list) or not all(
                        isinstance(value, str) for value in args
                    ):
                        raise ValueError("arguments must be a JSON array of strings")
                    if not isinstance(env, dict) or any(
                        not isinstance(value, str)
                        or not (value.startswith("${") and value.endswith("}"))
                        for value in env.values()
                    ):
                        raise ValueError(
                            "environment values must be ${HOST_VARIABLE} references"
                        )
                    if not trusted:
                        raise ValueError("Trust acknowledgment is required")
                    if not name.strip() or not command.strip():
                        raise ValueError("Name and executable command are required")
                    created = api(
                        "POST",
                        "/harness-profiles",
                        json={
                            "name": name.strip(),
                            "description": description,
                            "manifest": {
                                "command": command.strip(),
                                "args": args,
                                "env": env,
                            },
                            "trusted_unsandboxed": True,
                        },
                    )
                    if created:
                        st.success("Harness profile created with revision 1.")
                        st.rerun()
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    st.error(f"Manifest is invalid: {exc}")

    import_file = st.file_uploader(
        "Import harness manifest", type=["json"], key="harness-import"
    )
    if import_file is not None and st.button(
        "Import as unverified profile", key="import-harness"
    ):
        try:
            imported = api(
                "POST",
                "/harness-profiles/import",
                json=json.loads(import_file.getvalue().decode("utf-8")),
            )
            if imported:
                st.success("Imported as a new unverified profile.")
                st.rerun()
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            st.error(f"Import failed: {exc}")

    profiles = api("GET", "/harness-profiles", params={"include_archived": True}) or []
    descriptors = capability_descriptors(caps)
    for profile in profiles:
        pid = profile.get("id")
        archived = bool(profile.get("archived"))
        revisions = profile.get("revisions") or []
        with st.expander(
            f"{profile.get('name', 'Unnamed')} {'· archived' if archived else ''}",
            expanded=False,
        ):
            st.write(profile.get("description") or "No description.")
            with st.form(f"metadata-{pid}"):
                metadata_name = st.text_input(
                    "Profile name",
                    value=profile.get("name", ""),
                    key=f"metadata-name-{pid}",
                )
                metadata_description = st.text_input(
                    "Profile description",
                    value=profile.get("description", ""),
                    key=f"metadata-description-{pid}",
                )
                if st.form_submit_button("Save profile metadata"):
                    if not metadata_name.strip():
                        st.error("Profile name is required")
                    else:
                        updated = api(
                            "PATCH",
                            f"/harness-profiles/{pid}",
                            json={
                                "name": metadata_name.strip(),
                                "description": metadata_description,
                            },
                        )
                        if updated:
                            st.rerun()
            current = next(
                (
                    rev
                    for rev in revisions
                    if rev.get("id") == profile.get("current_revision_id")
                ),
                revisions[-1] if revisions else {},
            )
            st.caption(
                f"Current immutable revision {current.get('revision_number', '—')} · {len(revisions)} revision(s)"
            )
            manifest = current.get("manifest") or {}
            st.json(manifest)
            trust = bool(current.get("trusted_unsandboxed"))
            descriptor = next(
                (
                    item
                    for item in descriptors
                    if item.get("profile_id") == pid
                    and item.get("revision_id") == current.get("id")
                ),
                None,
            )
            # Capabilities perform executable/PATH preflight. Do not infer
            # readiness from a non-empty command in an unavailable profile.
            local_ready = bool(
                descriptor
                and descriptor.get("local_ready", descriptor.get("ready", False))
            )
            _badge(
                "Local ready",
                local_ready,
                "executable available" if local_ready else "executable unavailable",
            )
            _badge("Trusted revision", trust, "required before ACP runs")
            with st.expander("Create a new immutable revision", expanded=False):
                with st.form(f"revision-{pid}"):
                    rev_command = st.text_input(
                        "Executable command",
                        value=manifest.get("command", ""),
                        key=f"cmd-{pid}",
                    )
                    rev_args = st.text_area(
                        "Arguments (JSON array)",
                        value=json.dumps(manifest.get("args", [])),
                        key=f"args-{pid}",
                    )
                    rev_env = st.text_area(
                        "Environment references (JSON object)",
                        value=json.dumps(manifest.get("env", {})),
                        key=f"env-{pid}",
                    )
                    rev_trust = st.checkbox(
                        "I acknowledge this new revision is trusted and unsandboxed",
                        value=trust,
                        key=f"trust-{pid}",
                    )
                    if st.form_submit_button("Save new revision"):
                        try:
                            args = json.loads(rev_args)
                            env = json.loads(rev_env)
                            if not isinstance(args, list) or not all(
                                isinstance(v, str) for v in args
                            ):
                                raise ValueError(
                                    "arguments must be a JSON array of strings"
                                )
                            if not isinstance(env, dict) or any(
                                not isinstance(v, str)
                                or not (v.startswith("${") and v.endswith("}"))
                                for v in env.values()
                            ):
                                raise ValueError(
                                    "environment values must be ${HOST_VARIABLE} references"
                                )
                            if not rev_trust:
                                raise ValueError(
                                    "Trust acknowledgment is required for every revision"
                                )
                            result = api(
                                "POST",
                                f"/harness-profiles/{pid}/revisions",
                                json={
                                    "name": profile.get("name", ""),
                                    "description": profile.get("description", ""),
                                    "manifest": {
                                        "command": rev_command,
                                        "args": args,
                                        "env": env,
                                    },
                                    "trusted_unsandboxed": True,
                                },
                            )
                            if result:
                                st.rerun()
                        except (ValueError, TypeError, json.JSONDecodeError) as exc:
                            st.error(f"Manifest is invalid: {exc}")
            probes = api("GET", f"/harness-profiles/{pid}/probes") or []
            st.markdown("**Verification**")
            protocol = next(
                (probe for probe in probes if probe.get("kind") == "protocol"), None
            )
            full = next(
                (probe for probe in probes if probe.get("kind") == "full"), None
            )
            protocol_verified = bool(
                descriptor and descriptor.get("protocol_verified", False)
            )
            full_verified = bool(descriptor and descriptor.get("full_verified", False))
            _badge(
                "Protocol verified",
                protocol_verified,
                "verified" if protocol_verified else "not verified",
            )
            _badge(
                "Fully verified",
                full_verified,
                "verified" if full_verified else "not verified",
            )
            for warning in (descriptor or {}).get("warnings") or []:
                st.warning(str(warning))
            with st.expander(f"Probe history ({len(probes)})", expanded=False):
                for probe in probes:
                    st.caption(
                        f"{probe.get('created_at', '—')} · {probe.get('kind')} · {probe.get('status')} · {probe.get('transport', 'stdio')} · mode={probe.get('mode_id') or 'default'}"
                    )
                    st.json(
                        {
                            "session_config": probe.get("session_config") or {},
                            "agent_identity": probe.get("agent_identity"),
                            "evidence": probe.get("evidence") or {},
                        }
                    )
            transport = st.selectbox(
                "Probe transport",
                ["stdio", "http", "sse"],
                key=f"probe-transport-{pid}",
            )
            probe_mode = st.text_input(
                "Full probe mode id (optional)", key=f"probe-mode-{pid}"
            )
            probe_config = st.text_area(
                "Full probe session config (JSON object)",
                "{}",
                key=f"probe-config-{pid}",
            )
            probe_col, full_col = st.columns(2)
            if probe_col.button("Start protocol probe", key=f"probe-{pid}"):
                api(
                    "POST",
                    f"/harness-profiles/{pid}/probe",
                    params={"kind": "protocol", "transport": transport},
                )
                st.rerun()
            if full_col.button("Start full probe", key=f"full-{pid}"):
                try:
                    config = json.loads(probe_config)
                    if not isinstance(config, dict):
                        raise ValueError("session config must be a JSON object")
                    api(
                        "POST",
                        f"/harness-profiles/{pid}/probe",
                        params={
                            "kind": "full",
                            "transport": transport,
                            "mode_id": probe_mode or None,
                            "session_config": json.dumps(config, separators=(",", ":")),
                        },
                    )
                    st.rerun()
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    st.error(f"Probe config is invalid: {exc}")
            if archived:
                if st.button("Restore profile", key=f"restore-h-{pid}"):
                    api("POST", f"/harness-profiles/{pid}/restore")
                    st.rerun()
            else:
                if st.button("Archive profile", key=f"archive-h-{pid}"):
                    api("POST", f"/harness-profiles/{pid}/archive")
                    st.rerun()
            exported = api("GET", f"/harness-profiles/{pid}/export")
            if exported is not None:
                st.download_button(
                    "Export manifest",
                    json.dumps(exported, indent=2),
                    f"{pid}.json",
                    "application/json",
                    key=f"export-{pid}",
                )
elif page == "MCP Profiles":
    st.title("MCP Profiles")
    with st.form("new_profile"):
        name = st.text_input("Name")
        desc = st.text_input("Description")
        raw = st.text_area(
            "MCP JSON", json.dumps(EXCALIDRAW_MCP_CONFIG, indent=2), height=260
        )
        if st.form_submit_button("Create profile"):
            try:
                api(
                    "POST",
                    "/profiles",
                    json={
                        "name": name,
                        "description": desc,
                        "mcp_json": json.loads(raw),
                    },
                )
                st.rerun()
            except Exception as e:
                st.error(f"Invalid JSON: {e}")
    profiles = api("GET", "/profiles", params={"include_archived": True}) or []
    for p in profiles:
        with st.expander(f"{p['name']} {'(archived)' if p['archived'] else ''}"):
            st.write(p.get("description", ""))
            full = api("GET", f"/profiles/{p['id']}")
            if full:
                validation = full.get("validation", {})
                for warning in validation.get("warnings", []):
                    st.warning(warning)
                if validation.get("missing_environment_variables"):
                    st.warning(
                        "Missing environment variables: "
                        + ", ".join(validation["missing_environment_variables"])
                    )
                revs = full.get("revisions", [])
                st.write(f"{len(revs)} revision(s)")
                sel = (
                    st.selectbox(
                        "Revision",
                        revs,
                        key=f"rev-{p['id']}",
                        format_func=lambda x: (
                            f"Revision {x['revision_number']} · {x['created_at']}"
                        ),
                    )
                    if revs
                    else None
                )
                if sel:
                    st.json(sel.get("mcp_json", {}))
            col1, col2 = st.columns(2)
            if p["archived"]:
                if col1.button("Restore", key=f"restore-{p['id']}"):
                    api("POST", f"/profiles/{p['id']}/restore")
                    st.rerun()
            else:
                if col1.button("Archive", key=f"archive-{p['id']}"):
                    api("POST", f"/profiles/{p['id']}/archive")
                    st.rerun()
                with col2.form(f"revision-{p['id']}"):
                    edit = st.text_area(
                        "New revision JSON",
                        json.dumps((sel or {}).get("mcp_json", {}), indent=2),
                    )
                    if st.form_submit_button("Save revision"):
                        try:
                            api(
                                "POST",
                                f"/profiles/{p['id']}/revisions",
                                json={"mcp_json": json.loads(edit)},
                            )
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

elif page == "New Run":
    st.title("New Run")
    prefill = st.session_state.get("prefill", {})
    if prefill and st.button("Clear clone draft"):
        st.session_state["prefill"] = {}
        st.rerun()
    profiles = api("GET", "/profiles") or []
    options = []
    for p in profiles:
        full = api("GET", f"/profiles/{p['id']}")
        options += [(p, r) for r in (full or {}).get("revisions", [])]
    if not options:
        st.info("Create an MCP profile first.")
    else:
        use_latest = (
            st.checkbox("Use latest profile revision", value=False)
            if prefill
            else False
        )
        if use_latest:
            profile_for_clone = next(
                (
                    x
                    for x, r0 in options
                    if r0["id"] == prefill.get("profile_revision_id")
                ),
                None,
            )
            latest_id = (
                profile_for_clone.get("current_revision_id")
                if profile_for_clone
                else None
            )
            if latest_id:
                prefill["profile_revision_id"] = latest_id
        default_index = next(
            (
                i
                for i, x in enumerate(options)
                if x[1]["id"] == prefill.get("profile_revision_id")
            ),
            0,
        )
        p, r = st.selectbox(
            "Profile and revision",
            options,
            index=default_index,
            format_func=lambda x: (
                f"{x[0]['name']} · revision {x[1]['revision_number']}"
            ),
        )
        servers = list((r.get("mcp_json") or {}).get("mcpServers", {}))
        default_server = (
            prefill.get("enabled_server")
            if prefill.get("enabled_server") in servers
            else (servers[0] if servers else None)
        )
        # Harness and model must stay outside the form: Streamlit batches form
        # widget changes until submission, but the model options depend on the
        # selected harness and need to refresh immediately.
        descriptors = capability_descriptors(caps)
        ids = [x.get("selection_id") for x in descriptors]
        preferred = (
            prefill.get("selection_id")
            if prefill.get("selection_id") in ids
            else (ids[0] if ids else "")
        )
        if not ids:
            st.error(
                "No harness descriptors are available. Check the API capabilities response."
            )
            selected = {}
            selection = ""
            harness = ""
            models = []
            model = ""
        else:
            selection = st.selectbox(
                "Harness",
                ids,
                index=ids.index(preferred) if preferred in ids else 0,
                format_func=lambda value: next(
                    (
                        f"{x.get('name', value)} · {x.get('selection_id', value)}"
                        for x in descriptors
                        if x.get("selection_id") == value
                    ),
                    value,
                ),
            )
            selected = next(
                (x for x in descriptors if x.get("selection_id") == selection), {}
            )
            # The selected descriptor is authoritative.  Do not combine its
            # readiness or options with global /health or another harness.
            harness = selected.get("harness")
            models = selected.get("models") or []
            model = (
                prefill.get("model")
                if prefill.get("model") in models
                else (models[0] if models else "")
            )
            _badge(
                "Harness ready",
                bool(selected.get("ready", selected.get("local_ready", False))),
                "local executable" if selected.get("kind") == "acp" else "configured",
            )
        model = st.selectbox(
            "Model", models, index=models.index(model) if model in models else 0
        )
        with st.form("run"):
            server = st.selectbox(
                "Enabled server",
                servers,
                index=servers.index(default_server) if default_server else 0,
            )
            modes = selected.get("tool_modes", ["mcp_only", "mcp_read_only", "full"])
            mode = st.selectbox(
                "Tool mode",
                modes,
                index=modes.index(prefill.get("tool_mode"))
                if prefill.get("tool_mode") in modes
                else 0,
            )
            agent_mode_id = None
            session_config = {}
            if harness == "acp":
                # Unprobed descriptors intentionally expose no mode/config;
                # this keeps unverified runs empty rather than inventing ACP
                # settings.  Probed options retain their typed defaults.
                agent_modes = selected.get("agent_modes") or []
                mode_ids = [_mode_id(mode) for mode in agent_modes]
                mode_labels = {
                    _mode_id(mode): _mode_label(mode) for mode in agent_modes
                }
                if mode_ids:
                    clone_mode = prefill.get("agent_mode_id")
                    descriptor_mode = selected.get("current_agent_mode_id")
                    default_mode = (
                        clone_mode
                        if clone_mode in mode_ids
                        else (
                            descriptor_mode
                            if descriptor_mode in mode_ids
                            else mode_ids[0]
                        )
                    )
                    agent_mode_id = st.selectbox(
                        "Agent mode",
                        mode_ids,
                        index=mode_ids.index(default_mode),
                        format_func=lambda value: mode_labels.get(value, value),
                    )
                else:
                    agent_mode_id = None
                for option in selected.get("session_config_options") or []:
                    oid, value = _render_config_option(
                        option,
                        f"acp-config-{selection}",
                        (prefill.get("session_config") or {}).get(_option_id(option)),
                    )
                    session_config[oid] = value
                warnings = list(selected.get("warnings") or [])
                if (
                    not selected.get("full_verified", False)
                    and "Harness is not fully verified" not in warnings
                ):
                    warnings.append("Harness is not fully verified")
                if warnings:
                    for warning in warnings:
                        st.warning(str(warning))
                    confirmed = st.checkbox(
                        "I understand this harness is unverified and will run unsandboxed",
                        key=f"confirm-{selection}",
                    )
                else:
                    confirmed = True
            if mode == "full":
                st.error("HIGH RISK: full mode grants unrestricted auto-approval.")
            prompt = st.text_area("Prompt", value=prefill.get("prompt", ""), height=180)
            expected = st.text_area(
                "Expected output (required)",
                value=prefill.get("expected_output", ""),
                height=100,
            )
            selected_ready = bool(
                selected.get("ready", selected.get("local_ready", False))
            )
            if not selected_ready:
                with st.container(border=True):
                    st.subheader(f"{harness} setup required")
                    st.warning(
                        "Check this harness's executable, CLI version, and API key or saved authentication."
                    )
            submitted = st.form_submit_button(
                "Run",
                disabled=not selected_ready or (harness == "acp" and not confirmed),
            )
            if submitted:
                if not prompt.strip() or not expected.strip():
                    st.error("Prompt and expected output are required")
                else:
                    payload = {
                        "harness": harness,
                        "model": ("agent-default" if harness == "acp" else model),
                        "prompt": prompt,
                        "expected_output": expected,
                        "profile_revision_id": r["id"],
                        "enabled_server": server,
                        "tool_mode": ("agent_default" if harness == "acp" else mode),
                    }
                    if harness == "acp":
                        payload.update(
                            {
                                "harness_revision_id": selected.get("revision_id"),
                                "agent_mode_id": agent_mode_id,
                                "session_config": session_config,
                            }
                        )
                    result = api("POST", "/runs", json=payload)
                    if result:
                        st.session_state["active_run"] = result["id"]
                        st.session_state["prefill"] = {}
                        st.rerun()
        active = st.session_state.get("active_run")
        if active:
            run = api("GET", f"/runs/{active}")
            if run and run["status"] in ("queued", "running"):
                st.info(
                    f"Status: {run['status']} · elapsed {run.get('elapsed_seconds', 0):.1f}s · queue position {run.get('queue_position') or 'active'}"
                )
                if st.button("Cancel"):
                    api("POST", f"/runs/{active}/cancel")
                    st.rerun()
                time.sleep(1)
                st.rerun()
            elif run:
                st.success(f"Finished: {run['status']}")
                report = api("GET", f"/runs/{active}/report")
                render_report(report or {})

else:
    st.title("Run History")
    f1, f2, f3, f4 = st.columns(4)
    status_filter = f1.selectbox(
        "Status",
        ["all", "queued", "running", "completed", "failed", "timed_out", "cancelled"],
    )
    model_filter = f2.text_input("Model")
    harness_filter = f3.selectbox(
        "Harness kind", ["all", "claude-code", "opencode", "acp"]
    )
    profile_filter = f4.text_input("Harness profile / MCP profile ID")
    params = {}
    if status_filter != "all":
        params["status"] = status_filter
    if model_filter:
        params["model"] = model_filter
    # Keep filtering server-side.  ACP profile IDs belong to harness history,
    # while ``profile_id`` remains the MCP profile filter for native runs.
    if harness_filter != "all":
        params["harness"] = harness_filter
    if profile_filter and harness_filter != "acp":
        params["profile_id"] = profile_filter
    elif profile_filter:
        params["harness_profile_id"] = profile_filter
    runs = api("GET", "/runs", params=params) or []
    for run in runs:
        with st.expander(f"{run['created_at']} · {run['status']} · {run['model']}"):
            st.write(run.get("prompt", ""))
            report = api("GET", f"/runs/{run['id']}/report")
            if report:
                render_report(report)
                st.download_button(
                    "Download complete JSON report",
                    json.dumps(report, indent=2),
                    f"{run['id']}.json",
                    "application/json",
                )
            c1, c2, c3 = st.columns(3)
            if c1.button("Clone to New Run", key=f"clone-{run['id']}"):
                snapshot = (
                    (report or {}).get("run", {}).get("harness_snapshot")
                    or run.get("harness_snapshot")
                    or {}
                )
                selection_id = (
                    f"profile:{run.get('harness_profile_id')}"
                    if run.get("harness") == "acp" and run.get("harness_profile_id")
                    else f"builtin:{run.get('harness')}"
                )
                st.session_state["prefill"] = {
                    "profile_revision_id": run["profile_revision_id"],
                    "enabled_server": run["enabled_server"],
                    "harness": run["harness"],
                    "selection_id": selection_id,
                    "harness_revision_id": snapshot.get("revision_id"),
                    "model": run["model"],
                    "tool_mode": run["tool_mode"],
                    "agent_mode_id": snapshot.get("agent_mode_id"),
                    "session_config": snapshot.get("session_config") or {},
                    "prompt": run["prompt"],
                    "expected_output": run["expected_output"],
                }
                st.session_state["page"] = "New Run"
                st.rerun()
            if c2.button("Cancel", key=f"cancel-{run['id']}"):
                api("POST", f"/runs/{run['id']}/cancel")
                st.rerun()
            if c3.button("Delete", key=f"delete-{run['id']}"):
                if run["status"] in ("queued", "running"):
                    st.warning("Cancel and wait for completion before deleting")
                else:
                    api("DELETE", f"/runs/{run['id']}")
                    st.rerun()
    if st.button("Clear all history"):
        st.session_state["confirm_clear"] = True
    if st.session_state.get("confirm_clear"):
        st.warning("This permanently deletes stored runs and traces.")
        if st.button("Confirm clear all"):
            api("DELETE", "/runs", params={"confirm": True})
            st.session_state["confirm_clear"] = False
            st.rerun()
