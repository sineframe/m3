"""Versioned, redacted, harness-neutral MCP call records."""
from __future__ import annotations
from typing import Any, Iterable

SCHEMA_VERSION = "mcp.v1"
UNMATCHED_LIMITATION = "No correlated MCP transport capture was available for this call; wire request/response and server latency are unavailable."

def _tool_name(value: Any, server: str | None = None) -> str:
    name = str(value or "")
    if name.startswith("MCP tool · "): name = name.removeprefix("MCP tool · ")
    if name.startswith("mcp__") and len(name.split("__", 2)) == 3: name = name.split("__", 2)[2]
    if server and name.lower().startswith(f"{server.lower()}_"): name = name[len(server) + 1:]
    return name

def _selected(value: Any, server: str) -> bool:
    name = str(value or "").lower(); server = server.lower()
    return name.startswith(f"mcp__{server}__") or name.startswith(f"{server}_")

def _error(result: Any, explicit: Any = None) -> Any:
    if explicit not in (None, "", False): return explicit
    if isinstance(result, dict) and (result.get("is_error") or result.get("error")):
        return result.get("error") or result.get("message") or result
    return None

def _call(**values: Any) -> dict[str, Any]:
    values.setdefault("error", None); values.setdefault("start_ms", None); values.setdefault("end_ms", None); values.setdefault("duration_ms", None)
    values.setdefault("server_latency_ms", None); values.setdefault("wire_request", None); values.setdefault("wire_response", None)
    values.setdefault("provenance", {"model": True, "wire": values["wire_request"] is not None or values["wire_response"] is not None})
    return values

def _canonical_arguments(wire_request: Any, native_arguments: Any) -> Any:
    """Prefer the arguments actually transmitted to the MCP server."""
    if isinstance(wire_request, dict):
        params = wire_request.get("params")
        if isinstance(params, dict) and "arguments" in params:
            return params["arguments"]
    return native_arguments

def from_claude_trace(trace: dict[str, Any], selected_server: str, transport: str) -> list[dict[str, Any]]:
    spans = trace.get("spans") or []
    intents = [s for s in spans if s.get("kind") == "tool_call" and s.get("metadata", {}).get("mcp_selected") is True]
    # Compatibility for traces built by older callers without classification metadata.
    intents = intents or [s for s in spans if s.get("kind") == "tool_call" and _selected(s.get("name"), selected_server)]
    intents.sort(key=lambda s: float(s.get("start_ms") or 0))
    wire = [s for s in spans if s.get("kind") == "mcp" and s.get("name") == "tools/call"]
    used: set[str] = set(); calls = []
    for index, span in enumerate(intents, 1):
        tool = _tool_name(span.get("name")); wire_match = next((s for s in wire if s.get("parent_id") == span.get("id") and str(s.get("id")) not in used), None)
        if wire_match is None:
            wire_match = next((s for s in wire if str((s.get("input") or {}).get("params", {}).get("name") or "") == tool and str(s.get("id")) not in used), None)
        if wire_match: used.add(str(wire_match.get("id")))
        result = span.get("output"); err = _error(result, span.get("error"))
        if wire_match and isinstance(wire_match.get("output"), dict) and wire_match["output"].get("error"):
            err = wire_match["output"]["error"]
        call = _call(id=str(span.get("id") or f"claude-mcp-{index}"), server=selected_server, tool=tool, status="error" if err or span.get("status") in {"error", "failed"} else span.get("status", "completed"), error=err, start_ms=span.get("start_ms"), end_ms=span.get("end_ms"), duration_ms=span.get("duration_ms"), arguments=span.get("input"), result=result, harness="claude-code", transport=transport)
        if wire_match:
            wire_request = wire_match.get("input")
            call.update(arguments=_canonical_arguments(wire_request, call["arguments"]), server_latency_ms=wire_match.get("duration_ms"), wire_request=wire_request, wire_response=wire_match.get("output"), provenance={"model": True, "wire": True})
        calls.append(call)
    return calls

def _unwrap(event: dict[str, Any]) -> tuple[dict[str, Any], float | None]:
    offset = event.get("offset_ms")
    raw = event.get("raw_event") if isinstance(event.get("raw_event"), dict) else event
    return (raw if isinstance(raw, dict) else {}), offset if isinstance(offset, (int, float)) else None

def from_opencode_events(events: Iterable[dict[str, Any]], selected_server: str, transport: str = "stdio") -> list[dict[str, Any]]:
    candidates: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, wrapper in enumerate(events, 1):
        event, receipt = _unwrap(wrapper if isinstance(wrapper, dict) else {})
        if event.get("type") not in {"tool_use", "tool"}: continue
        part = event.get("part") if isinstance(event.get("part"), dict) else event
        tool_full = str(part.get("tool") or part.get("name") or "")
        if not _selected(tool_full, selected_server): continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        ident = str(part.get("callID") or part.get("callId") or part.get("id") or f"opencode-mcp-{index}")
        timing = part.get("time") if isinstance(part.get("time"), dict) else {}
        native_start, native_end = timing.get("start"), timing.get("end")
        duration = native_end - native_start if isinstance(native_start, (int, float)) and isinstance(native_end, (int, float)) and native_end >= native_start else None
        if receipt is not None:
            # Native timestamps give duration, while backend receipt time puts
            # that duration on the per-run monotonic timeline.
            end = receipt
            start = max(0.0, receipt - duration) if duration is not None else receipt
        else:
            start = native_start if isinstance(native_start, (int, float)) else None
            end = native_end if isinstance(native_end, (int, float)) else None
        result = state.get("output"); err = _error(result, state.get("error") if state.get("status") == "error" else None)
        native_status = str(state.get("status") or "unknown").lower()
        normalized_status = "error" if err or native_status == "error" else (native_status if native_status in {"running", "pending"} else "completed")
        item = _call(id=ident, server=selected_server, tool=_tool_name(tool_full, selected_server), status=normalized_status, error=err, start_ms=start, end_ms=end, duration_ms=duration, arguments=state.get("input"), result=result, harness="opencode", transport=transport, limitations=[])
        # Terminal records supersede an earlier in-progress duplicate.
        rank = 2 if item["status"] in {"completed", "error", "failed"} and (result is not None or err is not None or duration is not None) else 1
        previous = candidates.get(ident)
        if previous is None or rank >= previous[0]: candidates[ident] = (rank, item)
    output = [item for _, item in candidates.values()]
    for item in output:
        if not item["provenance"].get("wire"):
            item["limitations"] = [UNMATCHED_LIMITATION]
    return output

def build_opencode_trace(*, events: Iterable[dict[str, Any]], protocol_events: Iterable[dict[str, Any]] = (), selected_server: str, transport: str, status: str, session_id: str | None = None) -> dict[str, Any]:
    raw = list(events); protocol = []
    for sequence, record in enumerate(sorted(list(protocol_events), key=lambda x: float(x.get("offset_ms", 0) or 0)), 1):
        item = dict(record); payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        item.setdefault("sequence", sequence); item.setdefault("jsonrpc_id", payload.get("id")); item.setdefault("method", payload.get("method")); item.setdefault("status", "error" if payload.get("error") is not None else ("request" if payload.get("method") else "response")); protocol.append(item)
    calls = from_opencode_events(raw, selected_server, transport)
    pending = {}; wire_links: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for record in sorted(protocol, key=lambda x: float(x.get("offset_ms", 0) or 0)):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        ident = payload.get("id")
        if record.get("direction") in {"client_to_server", "request"} and ident is not None and payload.get("method") == "tools/call":
            pending[str(ident)] = record
        elif ident is not None and str(ident) in pending:
            request = pending.pop(str(ident)); request_payload = request.get("payload") or {}
            tool = str(request_payload.get("params", {}).get("name") or "")
            target = next((c for c in calls if c["tool"] == tool and c["wire_request"] is None), None)
            if target:
                target["arguments"] = _canonical_arguments(request_payload, target["arguments"]); target["wire_request"] = request_payload; target["wire_response"] = payload; target["server_latency_ms"] = max(0.0, float(record.get("offset_ms", 0) or 0) - float(request.get("offset_ms", 0) or 0)); target["provenance"]["wire"] = True
                target["limitations"] = [limitation for limitation in target.get("limitations", []) if limitation != UNMATCHED_LIMITATION]
                wire_links[target["id"]] = (request, record)
                if isinstance(payload, dict) and payload.get("error"):
                    target["status"], target["error"] = "error", payload["error"]
    for call in calls:
        if not call["provenance"].get("wire"):
            call["limitations"] = [UNMATCHED_LIMITATION]
    spans: list[dict[str, Any]] = [
        {"id":"run","parent_id":None,"kind":"run","name":"OpenCode run","status":status,"start_ms":0.0,"end_ms":0.0,"duration_ms":0.0,"transport":transport,"input":None,"output":None,"metadata":{"harness":"opencode","session_id":session_id}},
        {"id":"run-mcp","parent_id":"run","kind":"mcp_session","name":f"MCP session · {transport}","status":"completed" if protocol else "unobserved","start_ms":0.0,"end_ms":0.0,"duration_ms":0.0,"transport":transport,"input":None,"output":None,"metadata":{"harness":"opencode","transport":transport}},
    ]
    turns: list[dict[str, Any]] = []; active_turn: dict[str, Any] | None = None
    total_cost = 0.0; has_cost = False; input_tokens = output_tokens = 0; thinking_count = 0

    def ensure_turn(offset: float) -> dict[str, Any]:
        nonlocal active_turn
        if active_turn is None:
            active_turn={"id":f"turn-{len(turns)+1}","parent_id":"run","kind":"model_turn","name":f"Agent step {len(turns)+1}","status":"completed" if status == "completed" else status,"start_ms":offset,"end_ms":offset,"duration_ms":0.0,"transport":transport,"input":None,"output":None,"metadata":{"harness":"opencode"}}
            turns.append(active_turn); spans.append(active_turn)
        return active_turn

    for wrapper in raw:
        event, receipt = _unwrap(wrapper if isinstance(wrapper, dict) else {})
        offset = float(receipt or 0.0); typ = str(event.get("type") or "")
        part = event.get("part") if isinstance(event.get("part"), dict) else event
        if typ == "step_start":
            if active_turn is not None:
                active_turn["end_ms"] = max(float(active_turn["end_ms"] or 0), offset)
            active_turn = None; ensure_turn(offset)
        elif typ == "step_finish":
            turn=ensure_turn(offset); turn["end_ms"] = max(float(turn["end_ms"] or 0), offset)
            cost=part.get("cost")
            if isinstance(cost,(int,float)): total_cost += float(cost); has_cost = True
            tokens=part.get("tokens") if isinstance(part.get("tokens"),dict) else {}
            input_tokens += int(tokens.get("input",0) or 0); output_tokens += int(tokens.get("output",0) or 0)
            active_turn=None
        elif typ in {"reasoning","text"}:
            turn=ensure_turn(offset); kind="thinking" if typ == "reasoning" else "text"
            if kind == "thinking": thinking_count += 1
            span={"id":f"{turn['id']}-{kind}-{len(spans)}","parent_id":turn["id"],"kind":kind,"name":"Thinking" if kind == "thinking" else "Response","status":"completed","start_ms":offset,"end_ms":offset,"duration_ms":0.0,"transport":transport,"input":None,"output":part.get("text", ""),"metadata":{"harness":"opencode"}}
            spans.append(span); turn["end_ms"] = max(float(turn["end_ms"] or 0), offset)

    def parent_turn(offset: float) -> dict[str, Any]:
        candidates=[turn for turn in turns if float(turn.get("start_ms") or 0) <= offset]
        if candidates: return candidates[-1]
        return ensure_turn(offset)

    tool_spans: dict[str, dict[str, Any]] = {}
    for call in calls:
        link=wire_links.get(call["id"]); start=float(call.get("start_ms") or 0); end=float(call.get("end_ms") or start)
        if link:
            start=min(start,float(link[0].get("offset_ms",start) or start)); end=max(end,float(link[1].get("offset_ms",end) or end))
        turn=parent_turn(start); turn["end_ms"] = max(float(turn["end_ms"] or 0), end)
        span={"id":f"opencode-tool-{call['id']}","parent_id":turn["id"],"kind":"tool_call","name":f"MCP tool · {call['tool']}","status":call["status"],"start_ms":start,"end_ms":end,"duration_ms":max(0.0,end-start),"transport":transport,"input":call["arguments"],"output":call["result"],"error":call["error"],"metadata":{"harness":"opencode","mcp_selected":True,"tool_use_id":call["id"],"correlation":"wire" if link else "native"}}
        spans.append(span); tool_spans[call["id"]]=span

    protocol_pending: dict[str, tuple[int, dict[str, Any]]] = {}; protocol_spans: list[dict[str, Any]] = []
    tool_queues: dict[str, list[dict[str, Any]]] = {}
    for call in calls: tool_queues.setdefault(call["tool"],[]).append(call)
    for sequence, record in enumerate(protocol,1):
        payload=record.get("payload") if isinstance(record.get("payload"),dict) else {}; ident=payload.get("id"); direction=record.get("direction"); offset=float(record.get("offset_ms",0) or 0)
        if direction in {"client_to_server","request"} and ident is not None:
            protocol_pending[str(ident)]=(sequence,record); continue
        request=protocol_pending.pop(str(ident),None) if ident is not None else None
        if request:
            request_sequence,request_record=request; request_payload=request_record.get("payload") or {}; method=str(request_payload.get("method") or "MCP response"); begin=float(request_record.get("offset_ms",0) or 0); parent_id="run-mcp"
            if method == "tools/call":
                tool=str((request_payload.get("params") or {}).get("name") or ""); queue=tool_queues.get(tool,[])
                if queue:
                    matched=queue.pop(0); parent_id=tool_spans.get(matched["id"],{}).get("id","run-mcp")
            protocol_spans.append({"id":f"opencode-mcp-{request_sequence}","parent_id":parent_id,"kind":"mcp","name":method,"status":"error" if payload.get("error") is not None else "completed","start_ms":begin,"end_ms":offset,"duration_ms":max(0.0,offset-begin),"transport":transport,"input":request_payload,"output":payload,"metadata":{"harness":"opencode","jsonrpc_id":ident,"request_sequence":request_sequence,"response_sequence":sequence}})
        else:
            protocol_spans.append({"id":f"opencode-mcp-event-{sequence}","parent_id":"run-mcp","kind":"mcp_event","name":str(payload.get("method") or "MCP frame"),"status":"error" if payload.get("error") is not None else "completed","start_ms":offset,"end_ms":offset,"duration_ms":0.0,"transport":transport,"input":payload if direction in {"client_to_server","request"} else None,"output":payload if direction not in {"client_to_server","request"} else None,"metadata":{"harness":"opencode","direction":direction,"jsonrpc_id":ident}})
    for request_sequence,record in protocol_pending.values():
        payload=record.get("payload") if isinstance(record.get("payload"),dict) else {}; offset=float(record.get("offset_ms",0) or 0)
        protocol_spans.append({"id":f"opencode-mcp-event-{request_sequence}","parent_id":"run-mcp","kind":"mcp_event","name":str(payload.get("method") or "MCP request"),"status":"pending","start_ms":offset,"end_ms":offset,"duration_ms":0.0,"transport":transport,"input":payload,"output":None,"metadata":{"harness":"opencode","direction":record.get("direction"),"jsonrpc_id":payload.get("id")}})
    spans.extend(protocol_spans)
    end_ms=max([float(span.get("end_ms") or 0) for span in spans]+[float(record.get("offset_ms",0) or 0) for record in protocol])
    for turn in turns:
        turn["end_ms"] = max(float(turn.get("start_ms") or 0),float(turn.get("end_ms") or 0)); turn["duration_ms"] = max(0.0,turn["end_ms"]-float(turn.get("start_ms") or 0))
    spans[0]["end_ms"]=end_ms; spans[0]["duration_ms"]=end_ms
    if protocol:
        spans[1]["start_ms"]=min(float(record.get("offset_ms",0) or 0) for record in protocol); spans[1]["end_ms"]=max(float(record.get("offset_ms",0) or 0) for record in protocol); spans[1]["duration_ms"]=max(0.0,spans[1]["end_ms"]-spans[1]["start_ms"])
    capture = "empty" if not raw else ("complete" if status == "completed" else "partial")
    limitations = [] if calls and all(c["provenance"].get("wire") for c in calls) else ([UNMATCHED_LIMITATION] if calls else [])
    summary={"transport":transport,"duration_ms":end_ms,"turns":len(turns),"mcp_calls":len(calls),"input_tokens":input_tokens or None,"output_tokens":output_tokens or None,"total_tokens":input_tokens+output_tokens or None,"cost_usd":total_cost if has_cost else None,"thinking":{"state":"visible" if thinking_count else "omitted","count":thinking_count}}
    return {"schema": "opencode.v1", "harness": "opencode", "capture_status": capture, "summary": summary, "mcp_calls_schema": SCHEMA_VERSION, "mcp_calls": calls, "spans": spans, "protocol_events": protocol, "result_metadata": {"session_id": session_id, "status": status}, "limitations": limitations}

__all__ = ["SCHEMA_VERSION", "from_claude_trace", "from_opencode_events", "build_opencode_trace"]
