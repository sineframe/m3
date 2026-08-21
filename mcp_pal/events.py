import json
from typing import Any

def normalize_events(raw: Any, selected_server: str | None = None) -> list[tuple[str, dict]]:
    """Return one normalized event for every Claude content block."""
    if not isinstance(raw, dict): return [("error", {"message": str(raw)})]
    typ, subtype = raw.get("type", ""), raw.get("subtype", "")
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
                    out.append(("tool_call", {"content": [block], "raw": raw, "tool_use_id": block.get("id"), "tool_name": block.get("name")}))
                elif btype == "tool_result":
                    out.append(("tool_result", {"content": [block], "raw": raw, "tool_use_id": block.get("tool_use_id")}))
                elif btype == "text": out.append(("assistant_text", {"text": block.get("text", ""), "content": block, "raw": raw}))
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
    seen=set()
    for raw in raw_events:
        try: key=json.dumps(raw,sort_keys=True,ensure_ascii=False)
        except TypeError: key=repr(raw)
        if key in seen: continue
        seen.add(key)
        for typ,payload in normalize_events(raw, server): out.append((typ,payload))
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
    calls=[]; call_ids=set()
    for typ,payload in normalized:
        if typ != "tool_call": continue
        ident,name=_call_info(payload)
        if ns in name.lower() or payload.get("server_name") == selected_server or payload.get("server") == selected_server:
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
