"""Pure extractors over an M3 execution result.

SDK facts relied on (sf-m3 0.2.38):
- result.snapshot: ExecutionState (outcome, execution_id, agent.harness.resolved_version/digest,
  agent.model.observed_id).
- result.trace_view.timeline: entries ordered by sequence_start (sorted explicitly here).
- ToolCallEntry.correlation: CORRELATED / WIRE_ONLY mean the call reached the server
  (wire evidence); REPORTED_ONLY means only the harness reported it (a native tool such as
  Bash/Read/shell, or an MCP call that never reached the wire).
- result text: ToolCallEntry.result.value.content[*].text.
- usage: trace_view.summary.usage (latest UsageEntry) plus UsageEntry timeline items.

Every function swallows missing-attribute errors and returns None / empty instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

TRACKER = "tracker"
_WIRE = {"correlated", "wire_only"}


def plain(value: Any) -> Any:
    """Convert M3 frozen models/mappings into JSON-safe values."""
    if hasattr(value, "model_dump"):
        return plain(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "value") and isinstance(value.value, (str, int, float)):
        return value.value  # enums
    return str(value)


def _obs(observation: Any) -> Any:
    """Value of an Observation, or None if absent/unobserved."""
    return plain(getattr(observation, "value", None))


def _enum(value: Any) -> Any:
    return getattr(value, "value", value)


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def final_text(result: Any) -> str | None:
    """Final answer: last turn response text, else last non-empty assistant message."""
    turns = _safe(lambda: result.turns) or ()
    if turns:
        text = _safe(lambda: turns[-1].response.text)
        if text:
            return text
    for entry in reversed(_safe(lambda: result.trace_view.messages) or ()):
        if _enum(entry.role) != "assistant":
            continue
        text = "".join(
            getattr(block, "text", "") or ""
            for block in entry.content
            if getattr(block, "kind", None) == "text"
        )
        if text.strip():
            return text
    return None


def tool_call_entries(result: Any) -> list[Any]:
    """Tool-call timeline entries in chronological order."""
    entries = _safe(lambda: list(result.trace_view.tool_calls)) or []
    return sorted(
        entries,
        key=lambda e: (
            _safe(lambda: e.sequence_start, 0) or 0,
            _safe(lambda: e.sequence_end, 0) or 0,
        ),
    )


def is_wire(entry: Any) -> bool:
    return _enum(_safe(lambda: entry.correlation)) in _WIRE


def is_tracker_wire(entry: Any) -> bool:
    return is_wire(entry) and _obs(_safe(lambda: entry.server)) == TRACKER


def is_native(entry: Any) -> bool:
    """Harness-reported-only call that is not a tracker MCP call (Bash, Read, shell...)."""
    if _enum(_safe(lambda: entry.correlation)) != "reported_only":
        return False
    reported = _safe(lambda: entry.reported.value)
    server = _obs(getattr(reported, "server", None)) if reported else None
    tool = _obs(getattr(reported, "tool", None)) if reported else _obs(entry.tool)
    if server == TRACKER or (isinstance(tool, str) and tool.startswith("mcp__tracker")):
        return False
    return True


def wire_tracker_calls(result: Any) -> list[tuple[str, dict]]:
    calls = []
    for entry in tool_call_entries(result):
        if not is_tracker_wire(entry):
            continue
        args = _obs(entry.arguments)
        calls.append((_obs(entry.tool) or "", args if isinstance(args, dict) else {}))
    return calls


def _result_chars(entry: Any) -> int | None:
    value = _safe(lambda: entry.result.value)
    if value is None:
        return None
    return sum(len(getattr(b, "text", "") or "") for b in (value.content or ()))


def tool_call_stats(result: Any) -> dict[str, Any]:
    entries = tool_call_entries(result)
    tracker = [e for e in entries if is_tracker_wire(e)]
    sizes = [n for n in (_result_chars(e) for e in tracker) if n is not None]
    return {
        "tracker_total": len(tracker),
        "tracker_errors": sum(
            1 for e in tracker if _enum(_safe(lambda: e.tool_status)) != "success"
        ),
        "native_total": sum(1 for e in entries if is_native(e)),
        "max_result_chars": max(sizes) if sizes else None,
    }


def usage_stats(result: Any) -> dict[str, Any]:
    usage = _safe(lambda: result.trace_view.summary.usage.value)

    def field(name: str):
        return _obs(getattr(usage, name, None)) if usage is not None else None

    per_turn = None
    timeline = _safe(lambda: list(result.trace_view.timeline)) or []
    values = [
        _obs(e.input_tokens)
        for e in sorted(
            (e for e in timeline if getattr(e, "kind", None) == "usage"),
            key=lambda e: e.sequence_start,
        )
    ]
    if values and any(v is not None for v in values):
        per_turn = values
    currency = field("currency")
    cost = field("cost")
    return {
        "input_tokens": field("input_tokens"),
        "output_tokens": field("output_tokens"),
        "reasoning_tokens": field("reasoning_tokens"),
        "cache_read_tokens": field("cache_read_tokens"),
        "cache_write_tokens": field("cache_write_tokens"),
        "total_tokens": field("total_tokens"),
        "reported_cost_usd": cost if currency in (None, "USD") else None,
        "per_turn_input_tokens": per_turn,
    }


def duration_ms(result: Any) -> float | None:
    return _safe(lambda: float(result.trace_view.summary.timing.duration_ms))


def turn_count(result: Any) -> int | None:
    return _safe(lambda: int(result.trace_view.summary.turn_count))


def outcome_value(result: Any) -> str | None:
    return _enum(_safe(lambda: result.snapshot.outcome))


def error_text(result: Any) -> str | None:
    import json

    err = _safe(lambda: result.error)
    if err is None:
        return None
    return json.dumps(plain(err), sort_keys=True)


def identity(result: Any) -> dict[str, Any]:
    snap = _safe(lambda: result.snapshot)
    agent = getattr(snap, "agent", None)
    harness = getattr(agent, "harness", None)
    model = getattr(agent, "model", None)
    exec_id = _safe(lambda: snap.execution_id)
    return {
        "observed_model": getattr(model, "observed_id", None),
        "version": getattr(harness, "resolved_version", None),
        "digest": getattr(harness, "digest", None),
        "execution_id": str(getattr(exec_id, "root", exec_id)) if exec_id else None,
    }
