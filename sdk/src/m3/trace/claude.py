"""Build a stable, redacted trace from Claude stream-json and MCP captures.

The builder intentionally reports only reasoning that Claude actually emitted.  It
does not attempt to reconstruct hidden chain-of-thought from timing or tool calls.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

SCHEMA_VERSION = "claude.v2"
TRANSPORTS = {"stdio", "http"}


def transport_for_server(server: dict[str, Any] | None) -> str:
    """Return the configured MCP transport, with stdio as Claude's default."""
    kind = str((server or {}).get("type", "stdio")).lower()
    return kind if kind in TRANSPORTS else "stdio"


def _number(value: Any) -> float | int | None:
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _offset(event: dict[str, Any], fallback: float) -> float:
    value = _number(event.get("offset_ms"))
    return max(0.0, float(value if value is not None else fallback))


def _duration(start: float, end: float | None) -> float | None:
    return round(max(0.0, end - start), 3) if end is not None else None


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("type") or event.get("event_type") or "system")


def _message_content(raw: dict[str, Any]) -> list[dict[str, Any]]:
    message = raw.get("message")
    content = (
        message.get("content") if isinstance(message, dict) else raw.get("content")
    )
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def _usage(raw: dict[str, Any]) -> dict[str, Any]:
    candidates = [raw.get("usage")]
    stream = raw.get("event")
    if isinstance(stream, dict):
        candidates.extend(
            [
                stream.get("usage"),
                (stream.get("message") or {}).get("usage")
                if isinstance(stream.get("message"), dict)
                else None,
            ]
        )
    message = raw.get("message")
    if isinstance(message, dict):
        candidates.append(message.get("usage"))
    for value in candidates:
        if isinstance(value, dict):
            output = {
                key: value[key]
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                    "server_tool_use",
                    "service_tier",
                )
                if key in value
            }
            if output:
                return output
    return {}


def _protocol_kind(record: dict[str, Any]) -> str:
    payload = record.get("payload")
    if isinstance(payload, dict):
        if payload.get("method") == "tools/call":
            return "mcp.tool_call"
        if payload.get("result") is not None:
            return "mcp.response"
        if payload.get("error") is not None:
            return "mcp.error"
        if payload.get("method"):
            return "mcp.request"
    return "mcp.frame"


def _protocol_name(record: dict[str, Any]) -> str:
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("method"):
        return str(payload["method"])
    return _protocol_kind(record)


def _protocol_spans(
    protocol: list[dict[str, Any]], transport: str, base_id: str = "mcp"
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    seq = 0
    for record in sorted(protocol, key=lambda item: _float(item.get("offset_ms", 0))):
        seq += 1
        start = _float(record.get("offset_ms", 0) or 0)
        payload = record.get("payload")
        direction = record.get("direction", "unknown")
        request_id = payload.get("id") if isinstance(payload, dict) else None
        method = payload.get("method") if isinstance(payload, dict) else None
        if direction in {"client_to_server", "request"} and request_id is not None:
            pending[str(request_id)] = {
                "start": start,
                "method": method,
                "seq": seq,
                "record": record,
            }
            continue
        parent = pending.pop(str(request_id), None) if request_id is not None else None
        end = start
        if parent:
            name = str(parent.get("method") or _protocol_kind(record))
            span_start = float(parent["start"])
            span = {
                "id": f"{base_id}-{parent['seq']}",
                "parent_id": "run-mcp",
                "kind": "mcp",
                "name": name,
                "status": "error"
                if isinstance(payload, dict) and payload.get("error")
                else "completed",
                "start_ms": span_start,
                "end_ms": end,
                "duration_ms": _duration(span_start, end),
                "transport": transport,
                "input": parent["record"].get("payload"),
                "output": payload,
                "metadata": {
                    "jsonrpc_id": request_id,
                    "request_sequence": parent["seq"],
                    "response_sequence": seq,
                },
            }
            spans.append(span)
        else:
            spans.append(
                {
                    "id": f"{base_id}-{seq}",
                    "parent_id": "run-mcp",
                    "kind": "mcp_event",
                    "name": _protocol_name(record),
                    "status": "error"
                    if isinstance(payload, dict) and payload.get("error")
                    else "completed",
                    "start_ms": start,
                    "end_ms": end,
                    "duration_ms": 0.0,
                    "transport": transport,
                    "input": payload
                    if direction in {"client_to_server", "request"}
                    else None,
                    "output": payload
                    if direction not in {"client_to_server", "request"}
                    else None,
                    "metadata": {
                        "direction": direction,
                        "jsonrpc_id": request_id,
                        "capture_kind": record.get("kind", "jsonrpc"),
                    },
                }
            )
    return spans


def build_claude_trace(
    *,
    events: Iterable[dict[str, Any] | str],
    protocol_events: Iterable[dict[str, Any]] = (),
    transport: str = "stdio",
    status: str = "completed",
    cost_usd: float | None = None,
    session_id: str | None = None,
    selected_server: str | None = None,
) -> dict[str, Any]:
    """Lifecycle-aware builder that merges partial and complete messages."""
    if transport not in TRANSPORTS:
        transport = "stdio"
    ordered = list(events)
    protocol_list: list[dict[str, Any]] = []
    for sequence, record in enumerate(protocol_events, 1):
        item = dict(record) if isinstance(record, dict) else {"payload": record}
        payload = item.get("payload")
        if isinstance(payload, dict):
            item.setdefault("jsonrpc_id", payload.get("id"))
            item.setdefault("method", payload.get("method"))
            item.setdefault(
                "status",
                "error"
                if payload.get("error") is not None
                else ("request" if payload.get("method") else "response"),
            )
        item.setdefault("sequence", sequence)
        protocol_list.append(item)
    spans: list[dict[str, Any]] = [
        {
            "id": "run",
            "parent_id": None,
            "kind": "run",
            "name": "Claude run",
            "status": status,
            "start_ms": 0.0,
            "end_ms": None,
            "duration_ms": None,
            "transport": transport,
            "input": None,
            "output": None,
            "metadata": {"harness": "claude-code", "session_id": session_id},
        },
        {
            "id": "run-mcp",
            "parent_id": "run",
            "kind": "mcp_session",
            "name": f"MCP session · {transport}",
            "status": "completed" if protocol_list else "unobserved",
            "start_ms": 0.0,
            "end_ms": None,
            "duration_ms": None,
            "transport": transport,
            "input": None,
            "output": None,
            "metadata": {"transport": transport},
        },
    ]
    turns: list[dict[str, Any]] = []
    turns_by_message: dict[str, dict[str, Any]] = {}
    pending_turn: dict[str, Any] | None = None
    block_spans: dict[tuple[str, str, str], dict[str, Any]] = {}
    tool_calls: dict[str, dict[str, Any]] = {}
    first_output: float | None = None
    thinking_state, thinking_count = "omitted", 0
    final_offset = 0.0
    result_metadata: dict[str, Any] = {}

    def raw_of(event: dict[str, Any]) -> dict[str, Any]:
        raw = (
            event.get("raw_event")
            if isinstance(event.get("raw_event"), dict)
            else event
        )
        return raw if isinstance(raw, dict) else {}

    def message_id(raw: dict[str, Any], typ: str) -> str | None:
        if typ == "stream_event":
            stream = _dict(raw.get("event"))
            message = _dict(stream.get("message"))
            value = message.get("id")
        else:
            message = _dict(raw.get("message"))
            value = message.get("id") or raw.get("message_id") or raw.get("id")
        return str(value) if value else None

    def merge_usage(turn: dict[str, Any], usage: dict[str, Any]) -> None:
        current = turn.setdefault("usage", {})
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            value = _int(usage.get(key, 0) or 0)
            if value > _int(current.get(key, 0) or 0):
                current[key] = value

    def ensure_turn(
        start: float, raw: dict[str, Any], typ: str, partial: bool
    ) -> dict[str, Any]:
        nonlocal pending_turn
        ident = message_id(raw, typ)
        if ident and ident in turns_by_message:
            turn = turns_by_message[ident]
            if partial:
                pending_turn = turn
            return turn
        if (
            not ident
            and not partial
            and pending_turn
            and pending_turn.get("partial")
            and not pending_turn.get("closed")
        ):
            pending_turn["closed"] = True
            return pending_turn
        turn = {
            "id": f"turn-{len(turns) + 1}",
            "message_id": ident,
            "start": start,
            "partial": partial,
            "closed": False,
            "usage": {},
        }
        turns.append(turn)
        if ident:
            turns_by_message[ident] = turn
        pending_turn = turn
        spans.append(
            {
                "id": turn["id"],
                "parent_id": "run",
                "kind": "model_turn",
                "name": f"Model turn {len(turns)}",
                "status": "streaming" if partial else "completed",
                "start_ms": start,
                "end_ms": start,
                "duration_ms": 0.0,
                "transport": transport,
                "input": None,
                "output": None,
                "metadata": {"usage": {}, "message_id": ident},
                "tokens": {},
            }
        )
        return turn

    def add_block(
        turn: dict[str, Any],
        block: dict[str, Any],
        start: float,
        block_index: Any,
        partial: bool,
    ) -> dict[str, Any] | None:
        nonlocal first_output, thinking_state, thinking_count
        block_type = str(block.get("type") or "unknown")
        if block_type in {"text", "text_delta"}:
            kind, name, output = "text", "Text", block.get("text", "")
            if output and first_output is None:
                first_output = start
        elif block_type in {"thinking", "thinking_delta", "signature_delta"}:
            kind, name = "thinking", "Thinking"
            # Signatures prove a hidden thinking block existed but are not
            # displayable reasoning and should not become UI output.
            output = (
                None
                if block_type == "signature_delta"
                else (block.get("thinking") or "")
            )
            if block_type == "signature_delta" or block.get("signature"):
                thinking_state = "encrypted"
            elif output:
                thinking_state = "visible"
            elif thinking_state == "omitted":
                thinking_state = "empty"
        elif block_type in {"tool_use", "tool_call"}:
            kind, name, output = "tool_call", str(block.get("name") or "MCP tool"), None
            if first_output is None:
                first_output = start
        elif block_type == "input_json_delta":
            kind, name, output = (
                "tool_call",
                "Tool input",
                block.get("partial_json", ""),
            )
        else:
            return None
        key = (turn["id"], str(block_index if block_index is not None else 0), kind)
        span = block_spans.get(key)
        if span is None:
            span = {
                "id": f"{turn['id']}-{kind}-{len(block_spans)}",
                "parent_id": turn["id"],
                "kind": kind,
                "name": name,
                "status": "streaming" if partial else "completed",
                "start_ms": start,
                "end_ms": start,
                "duration_ms": 0.0,
                "transport": transport,
                "input": block.get("input"),
                "output": output,
                "metadata": {"partial": partial, "block_index": block_index},
            }
            if kind == "tool_call":
                span["metadata"]["mcp_selected"] = bool(
                    selected_server
                    and str(block.get("name") or "")
                    .lower()
                    .startswith(f"mcp__{selected_server.lower()}__")
                )
            block_spans[key] = span
            spans.append(span)
            if kind == "thinking":
                thinking_count += 1
        else:
            # Claude emits a complete assistant message after the partial stream.
            # Use it to complete payloads, but do not stretch an already-timed
            # stream block to the end of the full message replay.
            partial_replay = (
                bool(span.get("metadata", {}).get("partial")) and not partial
            )
            if not partial_replay:
                span["end_ms"] = max(_float(span.get("end_ms")), start)
                span["duration_ms"] = _duration(
                    _float(span.get("start_ms")), _float(span["end_ms"])
                )
            span["status"] = "streaming" if partial else "completed"
            if partial and output not in (None, ""):
                existing = span.get("output")
                if isinstance(existing, str) and isinstance(output, str):
                    span["output"] = existing + output
                elif existing in (None, ""):
                    span["output"] = output
            if not partial:
                if output not in (None, ""):
                    span["output"] = output
                if block.get("input") is not None:
                    span["input"] = block["input"]
                if kind == "tool_call" and block.get("name"):
                    span.setdefault("metadata", {})["mcp_selected"] = bool(
                        selected_server
                        and str(block.get("name") or "")
                        .lower()
                        .startswith(f"mcp__{selected_server.lower()}__")
                    )
        if kind == "tool_call":
            ident = block.get("id") or block.get("tool_use_id")
            if ident:
                tool_calls[str(ident)] = {
                    "span": span,
                    "start": _float(span.get("start_ms")),
                    "name": name,
                    "turn": turn["id"],
                }
                span.setdefault("metadata", {})["tool_use_id"] = ident
        return span

    for index, event in enumerate(ordered):
        if not isinstance(event, dict):
            continue
        start = _offset(event, float(index))
        final_offset = max(final_offset, start)
        typ = _event_type(event)
        raw = raw_of(event)
        if typ in {"assistant", "message"}:
            turn = ensure_turn(start, raw, typ, partial=False)
            merge_usage(turn, _usage(raw))
            turn["closed"], turn["partial"] = True, False
            turn.setdefault("end", start)
            turn_span = next(span for span in spans if span["id"] == turn["id"])
            turn_span["metadata"]["usage"], turn_span["tokens"] = (
                turn["usage"],
                turn["usage"],
            )
            message = _dict(raw.get("message"))
            turn_span["metadata"]["model"] = raw.get("model") or message.get("model")
            for block_index, block in enumerate(_message_content(raw)):
                add_block(turn, block, start, block_index, partial=False)
            if pending_turn is turn:
                pending_turn = None
        elif typ == "stream_event":
            stream = _dict(raw.get("event"))
            stream_type = stream.get("type", "")
            if stream_type == "message_start":
                turn = ensure_turn(start, raw, typ, partial=True)
                merge_usage(turn, _usage(raw))
                turn_span = next(span for span in spans if span["id"] == turn["id"])
                turn_span["metadata"]["usage"], turn_span["tokens"] = (
                    turn["usage"],
                    turn["usage"],
                )
            elif stream_type in {"content_block_start", "content_block_delta"}:
                turn = pending_turn or ensure_turn(start, raw, typ, partial=True)
                if stream_type == "content_block_start":
                    block = _dict(stream.get("content_block"))
                else:
                    delta = _dict(stream.get("delta"))
                    block = dict(delta)
                    block["type"] = {
                        "thinking_delta": "thinking_delta",
                        "text_delta": "text_delta",
                        "input_json_delta": "input_json_delta",
                        "signature_delta": "signature_delta",
                    }.get(cast(str, delta.get("type")), delta.get("type"))
                add_block(turn, block, start, stream.get("index"), partial=True)
            elif stream_type == "content_block_stop" and pending_turn:
                stop_index = str(
                    stream.get("index") if stream.get("index") is not None else 0
                )
                for (turn_id, index_key, _kind), span in block_spans.items():
                    if turn_id == pending_turn["id"] and index_key == stop_index:
                        span["end_ms"] = max(_float(span.get("end_ms")), start)
                        span["duration_ms"] = _duration(
                            _float(span.get("start_ms")), _float(span["end_ms"])
                        )
                        span["status"] = "completed"
            elif stream_type == "message_delta" and pending_turn:
                merge_usage(pending_turn, _usage(raw))
                delta = _dict(stream.get("delta"))
                if delta.get("stop_reason"):
                    next(span for span in spans if span["id"] == pending_turn["id"])[
                        "metadata"
                    ]["stop_reason"] = delta["stop_reason"]
            elif stream_type == "message_stop" and pending_turn:
                pending_turn["closed"] = True
                pending_turn["end"] = start
        elif typ in {"user", "tool_result", "tool_response"}:
            blocks = _message_content(raw)
            if not blocks and typ != "user":
                blocks = [raw]
            for block_index, block in enumerate(blocks):
                if typ == "user" and block.get("type") != "tool_result":
                    continue
                ident = block.get("tool_use_id") or raw.get("tool_use_id")
                call = tool_calls.get(str(ident)) if ident else None
                if call:
                    span = call["span"]
                    span["kind"], span["name"] = (
                        "tool_call",
                        f"MCP tool · {call['name']}",
                    )
                    span["status"], span["end_ms"] = (
                        ("error" if block.get("is_error") else "completed"),
                        start,
                    )
                    span["duration_ms"], span["output"] = (
                        _duration(call["start"], start),
                        block.get("content", block),
                    )
                    span.setdefault("metadata", {})["correlation"] = "exact"
                else:
                    spans.append(
                        {
                            "id": f"tool-result-{index}-{block_index}",
                            "parent_id": "run",
                            "kind": "tool_result",
                            "name": "MCP tool result",
                            "status": "error" if block.get("is_error") else "completed",
                            "start_ms": start,
                            "end_ms": start,
                            "duration_ms": 0.0,
                            "transport": transport,
                            "input": None,
                            "output": block.get("content", block),
                            "metadata": {
                                "tool_use_id": ident,
                                "correlation": "unmatched",
                            },
                        }
                    )
        elif typ in {"result", "final"}:
            result_metadata = {
                key: raw[key]
                for key in (
                    "duration_ms",
                    "duration_api_ms",
                    "num_turns",
                    "total_cost_usd",
                    "usage",
                    "is_error",
                    "subtype",
                )
                if key in raw
            }
            spans.append(
                {
                    "id": "result",
                    "parent_id": "run",
                    "kind": "result",
                    "name": "Final result",
                    "status": "error" if raw.get("is_error") else status,
                    "start_ms": start,
                    "end_ms": start,
                    "duration_ms": 0.0,
                    "transport": transport,
                    "input": None,
                    "output": raw.get("result", raw.get("text")),
                    "metadata": result_metadata,
                }
            )
            final_offset = max(final_offset, start)

    final_usage = (
        _usage(raw_of(ordered[-1])) if ordered and isinstance(ordered[-1], dict) else {}
    )
    if not any(turn["usage"] for turn in turns) and final_usage:
        if turns:
            merge_usage(turns[-1], final_usage)
        else:
            turns.append({"id": "turn-1", "start": 0.0, "usage": final_usage})
            spans.append(
                {
                    "id": "turn-1",
                    "parent_id": "run",
                    "kind": "model_turn",
                    "name": "Model turn 1",
                    "status": "completed",
                    "start_ms": 0.0,
                    "end_ms": final_offset,
                    "duration_ms": final_offset,
                    "transport": transport,
                    "input": None,
                    "output": None,
                    "metadata": {"usage": final_usage},
                    "tokens": final_usage,
                }
            )
    usage_total: dict[str, int] = {}
    for turn in turns:
        if not turn.get("id"):
            continue
        turn_span = next(span for span in spans if span["id"] == turn["id"])
        turn_span["metadata"]["usage"], turn_span["tokens"] = (
            turn["usage"],
            turn["usage"],
        )
        for key, value in turn["usage"].items():
            usage_total[key] = usage_total.get(key, 0) + _int(value)

    mcp_spans = _protocol_spans(protocol_list, transport)
    intents = [
        span
        for span in spans
        if span.get("kind") == "tool_call"
        and (
            not selected_server or span.get("metadata", {}).get("mcp_selected") is True
        )
    ]
    intent_queues: dict[str, list[dict[str, Any]]] = {}
    for intent in intents:
        # The tool identity is the emitted tool name, never an arbitrary
        # argument named ``name`` (which is valid MCP input).
        identity = str(intent.get("name") or "").removeprefix("MCP tool · ")
        if identity.startswith("mcp__"):
            identity = identity.split("__", 2)[-1]
        intent_queues.setdefault(identity, []).append(intent)
    for mcp in mcp_spans:
        if mcp.get("name") != "tools/call" or not isinstance(mcp.get("input"), dict):
            continue
        params = (
            mcp["input"].get("params")
            if isinstance(mcp["input"].get("params"), dict)
            else {}
        )
        tool_name = str(params.get("name") or "")
        matches = intent_queues.get(tool_name, [])
        mcp.setdefault("metadata", {})["correlation"] = (
            "heuristic" if matches else "unmatched"
        )
        if matches:
            intent = matches.pop(0)
            mcp["parent_id"] = intent["id"]
            mcp["metadata"]["tool_use_id"] = (intent.get("metadata") or {}).get(
                "tool_use_id"
            )
    spans.extend(mcp_spans)
    end_ms = max(
        final_offset, max((_float(span.get("end_ms")) for span in spans), default=0.0)
    )
    for index, turn in enumerate(turns):
        if not turn.get("id"):
            continue
        turn_span = next(span for span in spans if span["id"] == turn["id"])
        turn_end = turn.get("end")
        if turn_end is None:
            turn_end = turns[index + 1]["start"] if index + 1 < len(turns) else end_ms
        turn_span["end_ms"] = max(_float(turn_span["start_ms"]), _float(turn_end))
        turn_span["duration_ms"] = _duration(
            _float(turn_span["start_ms"]), _float(turn_span["end_ms"])
        )
        turn_span["status"] = "completed" if status == "completed" else status
    spans[0]["end_ms"], spans[0]["duration_ms"] = end_ms, _duration(0.0, end_ms)
    if protocol_list:
        protocol_start = min(_float(event.get("offset_ms")) for event in protocol_list)
        protocol_end = max(_float(event.get("offset_ms")) for event in protocol_list)
        spans[1]["start_ms"], spans[1]["end_ms"] = protocol_start, protocol_end
        spans[1]["duration_ms"] = _duration(protocol_start, protocol_end)
    else:
        spans[1]["end_ms"], spans[1]["duration_ms"] = 0.0, 0.0
    from .model_steps import attach_model_steps

    thinking_count = attach_model_steps(spans)
    summary = {
        "transport": transport,
        "duration_ms": result_metadata.get("duration_ms", end_ms),
        "duration_api_ms": result_metadata.get("duration_api_ms"),
        "time_to_first_output_ms": first_output,
        "turns": result_metadata.get(
            "num_turns", len([turn for turn in turns if turn.get("id")])
        ),
        "input_tokens": usage_total.get("input_tokens") or None,
        "output_tokens": usage_total.get("output_tokens") or None,
        "cache_read_input_tokens": usage_total.get("cache_read_input_tokens") or None,
        "cache_creation_input_tokens": usage_total.get("cache_creation_input_tokens")
        or None,
        "total_tokens": (
            usage_total.get("input_tokens", 0) + usage_total.get("output_tokens", 0)
        )
        or None,
        "cost_usd": result_metadata.get("total_cost_usd", cost_usd),
        "thinking": {"state": thinking_state, "count": thinking_count},
        "mcp_protocol_events": len(protocol_list),
        "mcp_spans": len(mcp_spans),
    }
    from .normalized import from_claude_trace

    capture_status = (
        "empty"
        if not (ordered or protocol_list)
        else ("complete" if status == "completed" else "partial")
    )
    trace = {
        "schema": SCHEMA_VERSION,
        "harness": "claude-code",
        "capture_status": capture_status,
        "summary": summary,
        "mcp_calls_schema": "mcp.v1",
        "spans": spans,
        "protocol_events": protocol_list,
        "limitations": [
            "Hidden Claude reasoning is not inferred; only emitted thinking blocks are shown."
        ],
        "result_metadata": result_metadata,
    }
    trace["mcp_calls"] = (
        from_claude_trace(trace, selected_server, transport) if selected_server else []
    )
    return trace


__all__ = ["SCHEMA_VERSION", "TRANSPORTS", "build_claude_trace", "transport_for_server"]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
