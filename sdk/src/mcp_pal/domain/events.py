import json
from typing import Any

def normalize_events(raw: Any, selected_server: str | None = None) -> list[tuple[str, dict]]:
    """Translate supported harness events into the backend's canonical events."""
    if not isinstance(raw, dict): return [("error", {"message": str(raw)})]
    typ, subtype = raw.get("type", ""), raw.get("subtype", "")
    # OpenCode JSON output. A completed tool part contains both the call and its
    # result, so expose both canonical events and correlate them by call ID.
    if typ in ("step_start", "step_finish", "text", "reasoning", "tool_use"):
        part = raw.get("part") if isinstance(raw.get("part"), dict) else {}
        session_id = raw.get("sessionID") or part.get("sessionID")
        common = {"harness": "opencode", "session_id": session_id}
        if typ == "step_start": return [("step_start", {**common, "step": part})]
        if typ == "step_finish":
            return [("step_finish", {**common, "step": part, "cost_usd": part.get("cost"), "tokens": part.get("tokens")})]
        if typ == "text": return [("assistant_text", {**common, "text": part.get("text", "")})]
        if typ == "reasoning": return [("thinking", {**common, "text": part.get("text", "")})]
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        call_id = part.get("callID") or part.get("callId") or part.get("id")
        name = part.get("tool", "")
        server_name = selected_server if selected_server and name.lower().startswith(selected_server.lower()+"_") else None
        call = ("tool_call", {**common, "tool_use_id": call_id, "tool_name": name, "server_name": server_name, "input": state.get("input")})
        result = ("tool_result", {**common, "tool_use_id": call_id, "tool_name": name, "server_name": server_name, "result": state.get("output"), "is_error": state.get("status") == "error", "error": state.get("error")})
        return [call, result]
    # Claude's --include-partial-messages stream wraps Anthropic streaming
    # events. Preserve each emitted delta so the trace can show first-output
    # timing and incremental thinking/text without inventing hidden reasoning.
    if typ == "stream_event":
        event = raw.get("event") if isinstance(raw.get("event"), dict) else {}
        event_type = event.get("type", "")
        if event_type == "content_block_start":
            block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            block_type = block.get("type")
            if block_type == "thinking": return [("thinking", {"content": block, "partial": True, "raw": raw})]
            if block_type in ("tool_use", "tool_call"): return [("tool_call", {"content": [block], "partial": True, "raw": raw, "tool_use_id": block.get("id"), "tool_name": block.get("name")})]
            if block_type == "text": return [("assistant_text", {"text": block.get("text", ""), "content": block, "partial": True, "raw": raw})]
        if event_type == "content_block_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            delta_type = delta.get("type", "")
            if delta_type in ("thinking_delta", "signature_delta"): return [("thinking", {"content": delta, "partial": True, "raw": raw})]
            if delta_type == "text_delta": return [("assistant_text", {"text": delta.get("text", ""), "partial": True, "raw": raw})]
            if delta_type == "input_json_delta": return [("tool_call", {"content": [delta], "partial": True, "raw": raw})]
        if event_type in ("message_start", "message_delta", "message_stop"):
            return [("model_stream", {"event": event, "partial": True, "raw": raw})]
        return [("stream_event", {"event": event, "partial": True, "raw": raw})]
    if typ in ("assistant", "message"):
        content = raw.get("message", {}).get("content", raw.get("content", []))
        if isinstance(content, str): return [("assistant_text", {"text": content, "raw": raw})]
        if isinstance(content, list):
            out=[]
            for block in content:
                if not isinstance(block, dict):
                    out.append(("assistant_text", {"text": str(block), "raw": raw})); continue
                btype=block.get("type")
                if btype == "thinking": out.append(("thinking", {"content": block, "raw": raw}))
                elif btype in ("tool_use", "tool_call"):
                    name=block.get("name", ""); server_name=selected_server if selected_server and f"mcp__{selected_server.lower()}__" in name.lower() else None
                    out.append(("tool_call", {"content": [block], "tool_use_id": block.get("id"), "tool_name": name, "server_name": server_name, "input": block.get("input")}))
                elif btype == "tool_result":
                    out.append(("tool_result", {"content": [block], "tool_use_id": block.get("tool_use_id"), "is_error": bool(block.get("is_error"))}))
                elif btype == "text": out.append(("assistant_text", {"text": block.get("text", ""), "content": block}))
            return out or [("system", raw)]
    if typ == "user":
        content = raw.get("message", {}).get("content", raw.get("content", []))
        if isinstance(content, list):
            out=[]
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out.append(("tool_result", {"content": [block], "raw": raw, "tool_use_id": block.get("tool_use_id")}))
            if out: return out
    if typ == "system": return [("mcp_initialization" if "init" in subtype or "mcp" in str(raw).lower() else "system", raw)]
    if typ in ("tool_result", "tool_response") or subtype in ("tool_result", "tool_response"): return [("tool_result", raw)]
    if typ in ("result", "final"): return [("assistant_text", {"text": raw.get("result", raw.get("text", "")), "raw": raw})]
    if "retry" in subtype or typ == "api_retry": return [("api_retry", raw)]
    if typ == "error" or "error" in subtype: return [("error", raw)]
    if typ == "user" and raw.get("tool_use_result") is not None: return [("tool_result", raw)]
    return [("system", raw)]

def normalize_event(raw: Any, selected_server: str | None = None) -> tuple[str, dict]:
    """Compatibility helper returning the first normalized event."""
    return normalize_events(raw, selected_server)[0]

def _namespace(server: str) -> str: return f"mcp__{server.lower()}__"
def _name(value: Any) -> str:
    return str(value or "")

def _call_info(payload: dict) -> tuple[str | None, str]:
    blocks=payload.get("content", []) if isinstance(payload, dict) else []
    block=blocks[0] if blocks and isinstance(blocks[0], dict) else payload
    return (block.get("id") or payload.get("tool_use_id"), _name(block.get("name") or payload.get("tool_name") or payload.get("name")))

def _result_error(payload: Any) -> bool:
    if isinstance(payload, dict):
        if payload.get("is_error") or payload.get("tool_error"): return True
        if payload.get("error") not in (None, "", False): return True
        return any(_result_error(v) for k,v in payload.items() if k in ("tool_use_result", "result", "content", "message"))
    if isinstance(payload, list): return any(_result_error(v) for v in payload)
    return False

def _normalized(raw_events: list[Any], server: str):
    out=[]
    seen=set(); canonical_seen=set()
    for raw in raw_events:
        try: key=json.dumps(raw,sort_keys=True,ensure_ascii=False)
        except TypeError: key=repr(raw)
        if key in seen: continue
        seen.add(key)
        for typ,payload in normalize_events(raw, server):
            if typ in ("tool_call", "tool_result") and payload.get("harness") == "opencode":
                canonical=(typ,payload.get("session_id"),payload.get("tool_use_id"),payload.get("tool_name"))
                if canonical in canonical_seen: continue
                canonical_seen.add(canonical)
            out.append((typ,payload))
    return out

def derive_mcp_summary(raw_events: list[Any], selected_server: str) -> dict:
    """Summarize initialization, selected calls, and correlated results."""
    normalized=_normalized(raw_events, selected_server); ns=_namespace(selected_server)
    init = any(t == "mcp_initialization" for t,_ in normalized)
    init_state = "init_observed" if init else "not_observed"
    # Claude's initialization event may advertise per-server connection
    # states. Generic `system/init` alone is deliberately not treated as a
    # successful selected-server initialization.
    for raw in raw_events:
        if not isinstance(raw, dict): continue
        servers=raw.get("mcp_servers")
        if isinstance(servers, dict):
            entry=servers.get(selected_server)
            if isinstance(entry, dict): state=str(entry.get("status", entry.get("state", ""))).lower()
            else: state=str(entry or "").lower()
        elif isinstance(servers, list):
            entry=next((item for item in servers if isinstance(item, dict) and item.get("name") == selected_server), None)
            state=str((entry or {}).get("status", (entry or {}).get("state", ""))).lower()
        else: continue
        if state in ("connected", "ready", "initialized", "ok", "success"): init_state="connected"
        elif state in ("failed", "error", "disconnected"): init_state="failed"
    advertised=[]
    for raw in raw_events:
        if isinstance(raw, dict):
            for key in ("tools", "mcp_tools", "advertised_tools"):
                value=raw.get(key)
                if isinstance(value,list): advertised.extend(_name(x.get("name") if isinstance(x,dict) else x) for x in value)
    calls=[]; call_ids=set(); seen_calls=set()
    for typ,payload in normalized:
        if typ != "tool_call": continue
        ident,name=_call_info(payload)
        if ns in name.lower() or name.lower().startswith(selected_server.lower()+"_") or payload.get("server_name") == selected_server or payload.get("server") == selected_server:
            call_key=("id", ident) if ident else ("name", name, json.dumps(payload.get("content", []), sort_keys=True, ensure_ascii=False))
            if call_key in seen_calls: continue
            seen_calls.add(call_key)
            calls.append({"tool_use_id":ident,"name":name}); call_ids.add(ident) if ident else None
    matched_results=[]; unmatched=[]
    for typ,payload in normalized:
        if typ != "tool_result": continue
        ident,name=_call_info(payload)
        if ident and ident in call_ids: matched_results.append(payload)
        elif not ident and ns in name.lower(): matched_results.append(payload)
        else: unmatched.append(payload)
    errors=sum(1 for p in matched_results if _result_error(p)); successes=len(matched_results)-errors
    advertised=list(dict.fromkeys(x for x in advertised if x))
    return {"initialization_state":init_state,"advertised_tools":advertised,"selected_server_call_count":len(calls),"selected_server_call_names":[x["name"] for x in calls],"success_count":successes,"error_count":errors,"correlated_result_count":len(matched_results)}

def derive_mcp_assertion(raw_events: list[Any], selected_server: str) -> str:
    summary=derive_mcp_summary(raw_events, selected_server)
    if summary["selected_server_call_count"] == 0 or summary["correlated_result_count"] == 0: return "failed"
    if summary["success_count"] and summary["error_count"]: return "warning"
    return "passed" if summary["success_count"] else "failed"
