"""Versioned, redacted, harness-neutral MCP call records."""
from __future__ import annotations
from typing import Any, Iterable

SCHEMA_VERSION = "mcp.v1"
LIMITATION = "OpenCode output has no MCP transport interception; wire request/response and server latency are unavailable and this is not wire-level verification."

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
            call.update(server_latency_ms=wire_match.get("duration_ms"), wire_request=wire_match.get("input"), wire_response=wire_match.get("output"), provenance={"model": True, "wire": True})
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
        start = receipt if receipt is not None else (native_start if isinstance(native_start, (int, float)) else None)
        end = start + duration if start is not None and duration is not None else None
        result = state.get("output"); err = _error(result, state.get("error") if state.get("status") == "error" else None)
        native_status = str(state.get("status") or "unknown").lower()
        normalized_status = "error" if err or native_status == "error" else (native_status if native_status in {"running", "pending"} else "completed")
        item = _call(id=ident, server=selected_server, tool=_tool_name(tool_full, selected_server), status=normalized_status, error=err, start_ms=start, end_ms=end, duration_ms=duration, arguments=state.get("input"), result=result, harness="opencode", transport=transport, limitations=[LIMITATION])
        # Terminal records supersede an earlier in-progress duplicate.
        rank = 2 if item["status"] in {"completed", "error", "failed"} and (result is not None or err is not None or duration is not None) else 1
        previous = candidates.get(ident)
        if previous is None or rank >= previous[0]: candidates[ident] = (rank, item)
    return [item for _, item in candidates.values()]

def build_opencode_trace(*, events: Iterable[dict[str, Any]], selected_server: str, transport: str, status: str, session_id: str | None = None) -> dict[str, Any]:
    raw = list(events); calls = from_opencode_events(raw, selected_server, transport)
    capture = "empty" if not raw else ("complete" if status == "completed" else "partial")
    return {"schema": "opencode.v1", "harness": "opencode", "capture_status": capture, "summary": {"transport": transport, "mcp_calls": len(calls)}, "mcp_calls_schema": SCHEMA_VERSION, "mcp_calls": calls, "spans": [], "protocol_events": [], "result_metadata": {"session_id": session_id, "status": status}, "limitations": [LIMITATION]}

__all__ = ["SCHEMA_VERSION", "from_claude_trace", "from_opencode_events", "build_opencode_trace"]
