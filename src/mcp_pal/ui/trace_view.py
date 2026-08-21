"""Presentation helpers for the post-run trace waterfall."""

from __future__ import annotations

from typing import Any

OVERVIEW_KINDS = {"model_turn", "thinking", "text", "tool_call"}
PROTOCOL_KINDS = {"mcp", "mcp_event"}


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_duration(value: Any) -> str:
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
        return f"MCP · {short_tool_name(span.get('name'))}"
    if kind in PROTOCOL_KINDS:
        return f"Server · {span.get('name') or 'protocol event'}"
    return str(span.get("name") or kind or "Span")


def actor(span: dict[str, Any]) -> str:
    kind = str(span.get("kind") or "")
    if kind in {"model_turn", "thinking", "text"}:
        return "Claude"
    if kind == "tool_call":
        return "MCP call"
    if kind in PROTOCOL_KINDS:
        return "MCP server"
    return "System"


def visible_spans(spans: list[dict[str, Any]], *, include_protocol: bool = False) -> list[dict[str, Any]]:
    kinds = OVERVIEW_KINDS | (PROTOCOL_KINDS if include_protocol else set())
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


__all__ = ["actor", "display_name", "format_duration", "number", "server_latency_for", "visible_spans"]
