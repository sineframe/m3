"""Presentation helpers for the post-run trace waterfall."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from mcp_pal.trace.model_steps import attach_model_steps

# Content blocks are represented inside ``model_turn.steps`` in v2.  Keep the
# old names out of the overview so a v1 trace cannot turn every stream chunk
# into a separate top-level activity row after it is adapted below.
OVERVIEW_KINDS = {"model_turn", "tool_call"}
PROTOCOL_KINDS = {"mcp", "mcp_event"}


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_duration(value: Any) -> str:
    if value is None:
        return "—"
    milliseconds = max(0.0, number(value))
    if milliseconds >= 1000:
        return f"{milliseconds / 1000:.2f} s"
    if milliseconds >= 100:
        return f"{milliseconds:.0f} ms"
    if milliseconds >= 10:
        return f"{milliseconds:.1f} ms"
    return f"{milliseconds:.2f} ms"


def short_tool_name(value: Any) -> str:
    name = str(value or "tool")
    if name.startswith("MCP tool · "):
        name = name.removeprefix("MCP tool · ")
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            return parts[2]
    return name


def display_name(span: dict[str, Any]) -> str:
    kind = str(span.get("kind") or "")
    if kind == "model_turn":
        return str(span.get("name") or "Claude turn")
    if kind == "thinking":
        availability = str((span.get("metadata") or {}).get("availability") or "")
        return "Thinking" + (f" · {availability}" if availability in {"empty", "encrypted", "omitted"} else "")
    if kind == "text":
        return "Response"
    if kind == "tool_call":
        prefix = "MCP" if (span.get("metadata") or {}).get("mcp_selected", True) else "Tool"
        return f"{prefix} · {short_tool_name(span.get('name'))}"
    if kind in PROTOCOL_KINDS:
        return f"Server · {span.get('name') or 'protocol event'}"
    return str(span.get("name") or kind or "Span")


def actor(span: dict[str, Any]) -> str:
    kind = str(span.get("kind") or "")
    harness = str((span.get("metadata") or {}).get("harness") or "claude-code")
    agent = "OpenCode" if harness == "opencode" else "Claude"
    if kind in {"model_turn", "thinking", "text"}:
        return agent
    if kind == "tool_call":
        return "MCP call" if (span.get("metadata") or {}).get("mcp_selected", True) else f"{agent} tool"
    if kind in PROTOCOL_KINDS:
        return "MCP server"
    return "System"


def model_step_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return spans in the v2 display shape, including legacy v1 traces.

    Early persisted traces exposed ``thinking`` and ``text`` as individual
    spans.  They remain valid data and must still render, but showing them as
    rows recreates the streaming-chunk problem.  Adapt a copy only when those
    legacy content spans are present; v2 turns already contain their steps and
    must not be reprocessed (which would discard their coalesced content).
    """
    source = list(spans or [])
    if not any(span.get("kind") in {"thinking", "text"} for span in source):
        return source
    # Some early partial captures contain content blocks but no model-turn
    # envelope.  There is nowhere safe to attach those blocks, so preserve
    # them for the legacy overview instead of dropping them.
    if not any(span.get("kind") == "model_turn" for span in source):
        return source
    adapted = deepcopy(source)
    attach_model_steps(adapted)
    return adapted


def visible_spans(spans: list[dict[str, Any]], *, include_protocol: bool = False) -> list[dict[str, Any]]:
    source = list(spans or [])
    has_turn = any(span.get("kind") == "model_turn" for span in source)
    spans = model_step_spans(source)
    kinds = OVERVIEW_KINDS | (PROTOCOL_KINDS if include_protocol else set())
    if not has_turn:
        kinds |= {"thinking", "text"}
    visible = [span for span in spans if span.get("kind") in kinds]
    return sorted(
        visible,
        key=lambda span: (
            number(span.get("start_ms")),
            0 if span.get("kind") == "model_turn" else 1,
            number(span.get("end_ms")),
        ),
    )


def server_latency_for(span: dict[str, Any], spans: list[dict[str, Any]]) -> float | None:
    children = [item for item in spans if item.get("parent_id") == span.get("id") and item.get("kind") == "mcp" and item.get("name") == "tools/call"]
    if not children:
        return None
    return sum(number(item.get("duration_ms")) for item in children)

def wire_unavailable_message(harness: Any) -> str:
    if str(harness or "").lower() == "opencode":
        return "Wire view unavailable: OpenCode emitted output is not transport-level verification."
    return "Wire view unavailable: no correlated transport capture was available for this call."


__all__ = ["actor", "display_name", "format_duration", "model_step_spans", "number", "server_latency_for", "visible_spans", "wire_unavailable_message"]
