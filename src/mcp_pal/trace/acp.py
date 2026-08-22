"""Backend normalization for ACP v1 and captured MCP wire evidence.

ACP observations and MCP observations deliberately have different homes in the
returned trace.  The former describes what the agent reported, while the latter
is the transport-level authority for arguments, results, errors, and latency.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable


WIRE_LIMITATION = (
    "No correlated MCP transport capture was available for this call; wire "
    "request/response and server latency are unavailable."
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _offset(frame: Any) -> float:
    try:
        value = frame.get("offset_ms", 0) if isinstance(frame, dict) else 0
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _payload(frame: Any) -> Any:
    if isinstance(frame, dict) and "payload" in frame:
        return frame.get("payload")
    return frame


def _direction(frame: Any) -> str | None:
    direction = frame.get("direction") if isinstance(frame, dict) else None
    return str(direction) if direction is not None else None


def _is_request(frame: Any, payload: dict[str, Any]) -> bool:
    # A method alone is not enough: a server notification also has a method.
    return (
        _direction(frame)
        in {"client_to_server", "request", "outgoing", "client-to-server"}
        and payload.get("method") is not None
    )


def _is_response(frame: Any, payload: dict[str, Any]) -> bool:
    return (
        _direction(frame)
        in {"server_to_client", "response", "incoming", "server-to-client"}
        and payload.get("method") is None
        and payload.get("id") is not None
    )


def _id_key(value: Any) -> tuple[str, str]:
    """JSON-RPC ids are typed: integer ``1`` is not string ``"1"``."""
    return (type(value).__name__, repr(value))


def _protocol_record(frame: Any, sequence: int, *, source: str) -> dict[str, Any]:
    """Copy a captured frame and add stable, non-invasive classification."""

    item = dict(frame) if isinstance(frame, dict) else {"payload": frame}
    payload = _payload(frame)
    payload_dict = _dict(payload)
    item.setdefault("sequence", sequence)
    item.setdefault("jsonrpc_id", payload_dict.get("id"))
    item.setdefault("method", payload_dict.get("method"))
    direction = _direction(frame)
    if payload_dict.get("error") is not None:
        state = "error"
    elif _is_request(frame, payload_dict):
        state = "request"
    elif _is_response(frame, payload_dict):
        state = "response"
    elif payload_dict.get("method") is not None:
        state = "notification"
    else:
        state = "unknown"
    item.setdefault("status", state)
    item.setdefault("source", source)
    return item


def _tool_name(value: Any, selected_server: str | None = None) -> str:
    name = str(value or "")
    if name.startswith("MCP tool · "):
        name = name.removeprefix("MCP tool · ")
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            name = parts[2]
    if selected_server and name.lower().startswith(selected_server.lower() + "_"):
        name = name[len(selected_server) + 1 :]
    return name


def _update_from_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    params = _dict(payload.get("params"))
    update = params.get("update")
    if not isinstance(update, dict):
        # A few early ACP implementations put update fields directly in params.
        update = params
    kind = str(update.get("sessionUpdate") or update.get("type") or "unknown_update")
    return update, kind


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text") or content.get("value") or "")
    if isinstance(content, list):
        return "".join(_content_text(item) for item in content)
    return ""


def _normalized_status(value: Any, *, error: Any = None) -> str:
    if error is not None:
        return "error"
    status = str(value or "pending").lower()
    if status in {"success", "succeeded", "done", "complete"}:
        return "completed"
    if status in {"failed", "failure"}:
        return "error"
    return status


def _call_base(*, ident: Any, server: str, tool: str, transport: str, start: float) -> dict[str, Any]:
    return {
        "id": ident,
        "server": server,
        "tool": tool,
        "arguments": None,
        "result": None,
        "error": None,
        "status": "pending",
        "start_ms": start,
        "end_ms": start,
        "duration_ms": 0.0,
        "transport": transport,
        "wire_request": None,
        "wire_response": None,
        "server_latency_ms": None,
        "provenance": {"model": True, "wire": False, "correlation": "acp"},
        "limitations": [WIRE_LIMITATION],
    }


def _set_end(call: dict[str, Any], end: float) -> None:
    call["end_ms"] = max(float(call.get("start_ms") or 0), end)
    call["duration_ms"] = max(0.0, call["end_ms"] - float(call.get("start_ms") or 0))


def _acp_evidence(
    frames: list[Any],
    *,
    selected_server: str,
    transport: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ACP protocol records, activity spans, and model tool calls."""

    protocol: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    calls_by_key: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    update_count = 0
    for sequence, frame in enumerate(
        sorted(enumerate(frames), key=lambda pair: (_offset(pair[1]), pair[0])), 1
    ):
        # Keep the input sequence available for deterministic IDs after sorting.
        _, raw_frame = frame
        item = _protocol_record(raw_frame, sequence, source="acp")
        protocol.append(item)
        payload = _dict(_payload(raw_frame))
        if payload.get("method") != "session/update":
            if payload.get("method") and item.get("status") == "notification":
                offset = _offset(raw_frame)
                spans.append(
                    {
                        "id": f"acp-notification-{sequence}",
                        "parent_id": "run",
                        "kind": "update",
                        "name": str(payload.get("method")),
                        "status": "completed",
                        "start_ms": offset,
                        "end_ms": offset,
                        "duration_ms": 0.0,
                        "transport": transport,
                        "input": payload.get("params"),
                        "output": None,
                        "metadata": {"harness": "acp", "unknown": True, "raw_notification": payload},
                    }
                )
            continue
        update, update_type = _update_from_payload(payload)
        offset = _offset(raw_frame)
        update_count += 1
        tool_id = update.get("toolCallId") or update.get("tool_call_id") or update.get("callId")
        lower_type = update_type.lower()
        if "message" in lower_type:
            kind, name, output = "text", "Response", _content_text(update.get("content"))
            metadata = {"harness": "acp", "update_type": update_type, "raw_update": update}
        elif "thought" in lower_type or "reason" in lower_type:
            kind, name, output = "thinking", "Thinking", _content_text(update.get("content"))
            metadata = {"harness": "acp", "update_type": update_type, "raw_update": update}
        elif lower_type == "tool_call" or lower_type.endswith("_tool_call"):
            tool = update.get("title") or update.get("name") or update.get("tool") or "tool"
            ident = str(tool_id) if tool_id is not None else f"acp-tool-{sequence}"
            call = _call_base(
                ident=ident,
                server=selected_server,
                tool=_tool_name(tool, selected_server),
                transport=transport,
                start=offset,
            )
            call["tool_call_id"] = tool_id
            if "rawInput" in update:
                call["arguments"] = update["rawInput"]
            elif "input" in update:
                call["arguments"] = update["input"]
            elif "arguments" in update:
                call["arguments"] = update["arguments"]
            if "rawOutput" in update:
                call["result"] = update["rawOutput"]
            elif "output" in update:
                call["result"] = update["output"]
            call["status"] = _normalized_status(update.get("status"), error=update.get("error"))
            if "status" not in update and (call["result"] is not None or call["error"] is not None):
                call["status"] = "error" if call["error"] is not None else "completed"
            call["error"] = update.get("error")
            _set_end(call, offset)
            calls.append(call)
            if tool_id is not None:
                calls_by_key[str(tool_id)] = call
            kind, name, output = "tool_call", f"MCP tool · {call['tool']}", call["result"]
            metadata = {
                "harness": "acp",
                "mcp_selected": True,
                "update_type": update_type,
                "tool_call_id": tool_id,
                "raw_update": update,
            }
        elif lower_type == "tool_call_update" or lower_type.endswith("_tool_call_update"):
            call = calls_by_key.get(str(tool_id)) if tool_id is not None else None
            if call is None:
                ident = str(tool_id) if tool_id is not None else f"acp-tool-{sequence}"
                call = _call_base(ident=ident, server=selected_server, tool="tool", transport=transport, start=offset)
                call["tool_call_id"] = tool_id
                calls.append(call)
                if tool_id is not None:
                    calls_by_key[str(tool_id)] = call
            if update.get("title") or update.get("name") or update.get("tool"):
                call["tool"] = _tool_name(update.get("title") or update.get("name") or update.get("tool"), selected_server)
            if "rawInput" in update:
                call["arguments"] = update["rawInput"]
            elif "input" in update:
                call["arguments"] = update["input"]
            elif "arguments" in update:
                call["arguments"] = update["arguments"]
            if "rawOutput" in update:
                call["result"] = update["rawOutput"]
            elif "output" in update:
                call["result"] = update["output"]
            if "error" in update:
                call["error"] = update["error"]
            call["status"] = _normalized_status(update.get("status"), error=call.get("error"))
            if "status" not in update and (call["result"] is not None or call["error"] is not None):
                call["status"] = "error" if call["error"] is not None else "completed"
            _set_end(call, offset)
            # The lifecycle remains one normalized tool span; each update is
            # retained as a separate backend update child for the waterfall.
            kind, name, output = "update", f"Tool update · {call['tool']}", call["result"]
            metadata = {
                "harness": "acp",
                "mcp_selected": True,
                "update_type": update_type,
                "tool_call_id": tool_id,
                "lifecycle": "update",
                "raw_update": update,
            }
        elif "plan" in lower_type:
            kind, name, output = "plan", "Plan", update.get("entries") if "entries" in update else update
            metadata = {"harness": "acp", "update_type": update_type, "raw_update": update}
        elif any(token in lower_type for token in ("state", "mode", "config", "command")):
            kind, name, output = "state", update_type, update
            metadata = {"harness": "acp", "update_type": update_type, "raw_update": update}
        else:
            kind, name, output = "update", update_type, update
            metadata = {"harness": "acp", "update_type": update_type, "raw_update": update, "unknown": True}
        span = {
            "id": f"acp-update-{sequence}",
            "parent_id": "run",
            "kind": kind,
            "name": name,
            "status": "completed" if kind not in {"tool_call"} or output is not None else "pending",
            "start_ms": offset,
            "end_ms": offset,
            "duration_ms": 0.0,
            "transport": transport,
            "input": update if kind in {"plan", "state", "update"} else None,
            "output": output,
            "metadata": metadata,
        }
        if kind == "tool_call":
            span["id"] = f"acp-tool-{calls[-1]['id']}"
            span["status"] = calls[-1]["status"]
            span["start_ms"] = calls[-1]["start_ms"]
            span["end_ms"] = calls[-1]["end_ms"]
            span["duration_ms"] = calls[-1]["duration_ms"]
            span["input"] = calls[-1]["arguments"]
            span["output"] = calls[-1]["result"]
            span["error"] = calls[-1]["error"]
        spans.append(span)
    # Collapse a tool_call/tool_call_update sequence into one authoritative ACP
    # lifecycle span plus update children. This also gives update-first agents a
    # usable tool span rather than duplicating IDs in the trace.
    lifecycle: list[dict[str, Any]] = []
    for call in calls:
        ident = str(call["id"])
        existing = next((s for s in spans if s["id"] == f"acp-tool-{ident}"), None)
        if existing is None:
            existing = {
                "id": f"acp-tool-{ident}",
                "parent_id": "run",
                "kind": "tool_call",
                "name": f"MCP tool · {call['tool']}",
                "status": call["status"],
                "start_ms": call["start_ms"], "end_ms": call["end_ms"], "duration_ms": call["duration_ms"],
                "transport": transport, "input": call["arguments"], "output": call["result"], "error": call["error"],
                "metadata": {"harness": "acp", "mcp_selected": True, "tool_call_id": call.get("tool_call_id")},
            }
        else:
            existing.update({"status": call["status"], "start_ms": call["start_ms"], "end_ms": call["end_ms"], "duration_ms": call["duration_ms"], "input": call["arguments"], "output": call["result"], "error": call["error"]})
        lifecycle.append(existing)
    spans = [s for s in spans if s.get("kind") != "tool_call"] + lifecycle
    return protocol, spans, calls


def _wire_evidence(
    frames: list[Any],
    *,
    selected_server: str,
    configured_transport: str,
    instrumented_transport: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    protocol: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    mcp_spans: list[dict[str, Any]] = []
    pending: defaultdict[tuple[str, str], deque[tuple[int, dict[str, Any], dict[str, Any]]]] = defaultdict(deque)
    event_spans: list[dict[str, Any]] = []
    for sequence, pair in enumerate(
        sorted(enumerate(frames), key=lambda item: (_offset(item[1]), item[0])), 1
    ):
        _, frame = pair
        item = _protocol_record(frame, sequence, source="mcp")
        protocol.append(item)
        raw_payload = _payload(frame)
        payload = _dict(raw_payload)
        offset = _offset(frame)
        if _is_request(frame, payload) and payload.get("method") == "tools/call" and payload.get("id") is not None:
            params = _dict(payload.get("params"))
            ident = payload.get("id")
            call = {
                "id": ident,
                "request_sequence": sequence,
                "server": selected_server,
                "tool": _tool_name(params.get("name"), selected_server),
                "arguments": params.get("arguments") if "arguments" in params else None,
                "result": None,
                "error": None,
                "status": "pending",
                "start_ms": offset,
                "end_ms": offset,
                "duration_ms": 0.0,
                "transport": configured_transport,
                "configured_transport": configured_transport,
                "instrumented_transport": instrumented_transport,
                "wire_request": payload,
                "wire_response": None,
                "server_latency_ms": None,
                "provenance": {"model": False, "wire": True, "correlation": "wire"},
                "limitations": [],
            }
            calls.append(call)
            pending[_id_key(ident)].append((len(calls) - 1, item, payload))
            continue
        if _is_response(frame, payload) and _id_key(payload.get("id")) in pending and pending[_id_key(payload.get("id"))]:
            call_index, request_item, request_payload = pending[_id_key(payload.get("id"))].popleft()
            call = calls[call_index]
            call["wire_response"] = payload
            # Do not coerce a result or error: wire JSON is authoritative.
            call["result"] = payload.get("result")
            call["error"] = payload.get("error")
            call["status"] = "error" if payload.get("error") is not None else "completed"
            call["end_ms"] = max(call["start_ms"], offset)
            call["duration_ms"] = call["end_ms"] - call["start_ms"]
            call["server_latency_ms"] = call["duration_ms"]
            continue
        # Preserve initialization, tools/list, notifications, malformed, and
        # unmatched response evidence as protocol spans too. Only tools/call
        # request/response pairs become the normalized call spans above.
        is_req = _is_request(frame, payload)
        event_spans.append(
            {
                "id": f"wire-event-{sequence}",
                "parent_id": "run-mcp",
                "kind": "mcp_event",
                "name": str(payload.get("method") or ("MCP response" if not is_req else "MCP request")),
                "status": item.get("status", "unknown"),
                "start_ms": offset,
                "end_ms": offset,
                "duration_ms": 0.0,
                "transport": configured_transport,
                "input": raw_payload if is_req else None,
                "output": raw_payload if not is_req else None,
                "metadata": {"harness": "acp", "direction": _direction(frame), "jsonrpc_id": payload.get("id")},
            }
        )
    # A pending request is still evidence and must be rendered in a partial trace.
    for call in calls:
        mcp_spans.append(
            {
                "id": f"wire-mcp-{call['request_sequence']}",
                "parent_id": "run-mcp",
                "kind": "mcp",
                "name": "tools/call",
                "status": call["status"],
                "start_ms": call["start_ms"],
                "end_ms": call["end_ms"],
                "duration_ms": call["duration_ms"],
                "transport": configured_transport,
                "input": call["wire_request"],
                "output": call["wire_response"],
                "metadata": {
                    "harness": "acp",
                    "wire_authoritative": True,
                    "jsonrpc_id": call["id"],
                    "request_sequence": call["request_sequence"],
                },
            }
        )
    return protocol, calls, [*mcp_spans, *event_spans], list(pending.values())


def _time_related(acp: dict[str, Any], wire: dict[str, Any]) -> bool:
    a_start, a_end = float(acp.get("start_ms") or 0), float(acp.get("end_ms") or 0)
    w_start, w_end = float(wire.get("start_ms") or 0), float(wire.get("end_ms") or 0)
    # Receipt clocks are shared by the runner. A small tolerance handles an ACP
    # update immediately before/after a relay frame without broad guesses that
    # could falsely join repeated same-name calls.
    gap = max(w_start - a_end, a_start - w_end, 0.0)
    return gap <= 50.0


def _infer_links(acp_calls: list[dict[str, Any]], wire_calls: list[dict[str, Any]], selected_server: str) -> dict[int, int]:
    candidates: dict[int, list[int]] = {}
    for wi, wire in enumerate(wire_calls):
        candidates[wi] = [
            ai
            for ai, acp in enumerate(acp_calls)
            if _tool_name(acp.get("tool"), selected_server).lower() == _tool_name(wire.get("tool"), selected_server).lower()
            and _time_related(acp, wire)
        ]
    links: dict[int, int] = {}
    for wi, values in candidates.items():
        if len(values) != 1:
            continue
        ai = values[0]
        if sum(1 for other in candidates.values() if ai in other) == 1:
            links[wi] = ai
    return links


def build_acp_trace(
    *,
    acp_frames: Iterable[Any] | None,
    mcp_frames: Iterable[Any] = (),
    selected_server: str,
    configured_transport: str = "stdio",
    instrumented_transport: str | None = None,
    status: str = "completed",
    session_id: str | None = None,
    result_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable ``acp.v1`` trace from possibly malformed partial input."""

    acp_input, wire_input = list(acp_frames or ()), list(mcp_frames or ())
    configured = str(configured_transport or "unknown")
    instrumented = str(instrumented_transport or configured)
    acp_protocol, acp_spans, acp_calls = _acp_evidence(
        acp_input, selected_server=selected_server, transport=configured
    )
    wire_protocol, wire_calls, wire_spans, _ = _wire_evidence(
        wire_input,
        selected_server=selected_server,
        configured_transport=configured,
        instrumented_transport=instrumented,
    )
    links = _infer_links(acp_calls, wire_calls, selected_server)
    linked_acp: set[int] = set()
    for wi, ai in links.items():
        linked_acp.add(ai)
        wire = wire_calls[wi]
        wire["provenance"].update({"model": True, "correlation": "inferred"})
        wire["acp_tool_call_id"] = acp_calls[ai].get("tool_call_id")
        wire["limitations"] = []
        for span in wire_spans:
            if (span.get("metadata") or {}).get("request_sequence") == wire["request_sequence"]:
                span["metadata"].update({"correlation": "inferred", "acp_tool_call_id": acp_calls[ai].get("tool_call_id")})
                span["parent_id"] = f"acp-tool-{acp_calls[ai]['id']}"
        # Reflect the relationship on the ACP lifecycle span without replacing
        # the authoritative wire values in the normalized call.
        for span in acp_spans:
            if span["id"] == f"acp-tool-{acp_calls[ai]['id']}":
                span["metadata"].update({"wire": True, "correlation": "inferred"})

    merged_calls = list(wire_calls)
    for index, call in enumerate(acp_calls):
        if index not in linked_acp:
            # An ACP-only call remains visible, but cannot satisfy an MCP wire
            # assertion. Its limitation is intentionally precise.
            merged_calls.append(call)
    merged_calls.sort(key=lambda call: (float(call.get("start_ms") or 0), int(call.get("request_sequence") or 10**9)))

    # Tool spans represent ACP intent or a wire-only authoritative call. Wire
    # spans are children of the matching tool span where an inferred link exists.
    tool_spans: list[dict[str, Any]] = []
    linked_wire = set(links)
    for call in acp_calls:
        span = next((s for s in acp_spans if s["id"] == f"acp-tool-{call['id']}"), None)
        if span is not None:
            tool_spans.append(span)
    for wi, call in enumerate(wire_calls):
        if wi in linked_wire:
            continue
        tool_spans.append(
            {
                "id": f"wire-tool-{call['request_sequence']}",
                "parent_id": "run",
                "kind": "tool_call",
                "name": f"MCP tool · {call['tool']}",
                "status": call["status"],
                "start_ms": call["start_ms"],
                "end_ms": call["end_ms"],
                "duration_ms": call["duration_ms"],
                "transport": configured,
                "input": call["arguments"],
                "output": call["result"],
                "error": call["error"],
                "metadata": {"harness": "acp", "mcp_selected": True, "wire_authoritative": True, "request_sequence": call["request_sequence"]},
            }
        )
    for span in wire_spans:
        if span["parent_id"] == "run-mcp":
            request_sequence = (span.get("metadata") or {}).get("request_sequence")
            target = next((s for s in tool_spans if request_sequence is not None and s["id"] == f"wire-tool-{request_sequence}"), None)
            if target:
                span["parent_id"] = target["id"]

    end = max([_offset(frame) for frame in acp_input + wire_input] + [0.0])
    # ``acp_spans`` already contains one lifecycle span per ACP call. Keep the
    # separate ``tool_spans`` list only for locating parents and wire-only
    # additions; adding both would duplicate ACP tool rows.
    acp_ids = {span.get("id") for span in acp_spans}
    wire_only_tool_spans = [span for span in tool_spans if span.get("id") not in acp_ids]
    activity = [*acp_spans, *wire_only_tool_spans, *wire_spans]
    # One model span gives the UI a backend-normalized parent for all ACP
    # updates, while each update/tool span retains its own receipt timestamp.
    if acp_spans:
        starts = [float(span.get("start_ms") or 0) for span in acp_spans]
        model = {
            "id": "model-1",
            "parent_id": "run",
            "kind": "model_turn",
            "name": "ACP model turn",
            "status": status,
            "start_ms": min(starts),
            "end_ms": max(starts),
            "duration_ms": max(starts) - min(starts),
            "transport": configured,
            "input": None,
            "output": None,
            "metadata": {"harness": "acp", "update_count": len(acp_spans)},
        }
        activity.append(model)
    def span_order(span: dict[str, Any]) -> tuple[float, int, float, str]:
        kind = span.get("kind")
        priority = {"model_turn": 0, "thinking": 1, "text": 1, "tool_call": 1, "update": 1, "plan": 1, "state": 1, "mcp": 2, "mcp_event": 3}.get(kind, 1)
        return (float(span.get("start_ms") or 0), priority, float(span.get("end_ms") or 0), str(span.get("id")))
    activity.sort(key=span_order)
    run_span = {
        "id": "run",
        "parent_id": None,
        "kind": "run",
        "name": "ACP run",
        "status": status,
        "start_ms": 0.0,
        "end_ms": end,
        "duration_ms": end,
        "transport": configured,
        "metadata": {"harness": "acp", "session_id": session_id},
    }
    wire_offsets = [_offset(frame) for frame in wire_input] or [0.0]
    mcp_session = {
        "id": "run-mcp",
        "parent_id": "run",
        "kind": "mcp_session",
        "name": f"MCP session · {configured}",
        "status": "completed" if wire_protocol else "unobserved",
        "start_ms": min(wire_offsets),
        "end_ms": max(wire_offsets),
        "duration_ms": max(wire_offsets) - min(wire_offsets),
        "transport": configured,
        "metadata": {"harness": "acp", "configured_transport": configured, "instrumented_transport": instrumented},
    }
    all_spans = [run_span, mcp_session, *activity]
    all_spans = [all_spans[0], all_spans[1], *sorted(all_spans[2:], key=span_order)]
    # Keep the compact ``kind`` vocabulary used by the waterfall and expose a
    # compatibility ``type`` alias for older report consumers.
    type_aliases = {"thinking": "thought", "text": "message", "tool_call": "tool"}
    for span in all_spans:
        span.setdefault("type", type_aliases.get(span.get("kind"), span.get("kind")))
    limitations = [WIRE_LIMITATION] if any(not c.get("provenance", {}).get("wire") for c in merged_calls) else []
    capture_status = "complete" if status == "completed" and (acp_protocol or wire_protocol) else ("partial" if acp_protocol or wire_protocol else "empty")
    return {
        "schema": "acp.v1",
        "harness": "acp",
        "capture_status": capture_status,
        "summary": {
            "transport": configured,
            "configured_transport": configured,
            "instrumented_transport": instrumented,
            "duration_ms": end,
            "mcp_calls": len(merged_calls),
            "acp_updates": len(acp_spans),
        },
        "mcp_calls_schema": "mcp.v1",
        "mcp_calls": merged_calls,
        "spans": all_spans,
        # ACP and MCP frames are never merged. ``protocol_events`` remains the
        # historical ACP field; the explicit MCP field is for backend/API users.
        "protocol_events": acp_protocol,
        "acp_protocol_events": acp_protocol,
        "mcp_protocol_events": wire_protocol,
        "result_metadata": {"session_id": session_id, "status": status, **(result_metadata or {})},
        "limitations": limitations,
    }


__all__ = ["WIRE_LIMITATION", "build_acp_trace"]
