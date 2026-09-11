"""Normalize emitted model activity into cohesive, ordered turn steps."""

from __future__ import annotations

from typing import Any

CONTENT_KINDS = {"thinking", "text"}
# These are model-produced activities.  ``update``, ``plan``, and ``state``
# are emitted by ACP and remain public spans, but are also useful in the same
# ordered turn view as content and tool calls.
STEP_KINDS = CONTENT_KINDS | {"tool_call", "update", "plan", "state"}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _append_text(existing: Any, chunk: Any) -> Any:
    if chunk in (None, ""):
        return existing
    if existing in (None, ""):
        return chunk
    if isinstance(existing, str) and isinstance(chunk, str):
        return existing + chunk
    return chunk


def _merge_status(existing: Any, incoming: Any) -> Any:
    """Keep terminal status when a replayed stream chunk is encountered."""

    current = str(existing or "")
    next_status = str(incoming or "")
    if not next_status:
        return existing
    if current in {"error", "failed"}:
        return existing
    if next_status in {"error", "failed"}:
        return incoming
    if next_status in {"completed", "complete", "done", "success", "succeeded"}:
        return incoming
    return existing or incoming


def _source_id(span: dict[str, Any]) -> Any:
    value = span.get("id")
    return value if value is not None else None


def attach_model_steps(spans: list[dict[str, Any]]) -> int:
    """Attach coalesced content/tool steps to model turns in chronological order.

    Thinking and response spans are an ingestion detail.  They are removed from
    the public span list after their content is folded into their owning turn.
    Tool spans stay in the list because MCP correlation and latency views refer
    to their IDs.

    Returns the number of cohesive thinking sections, not stream chunks.
    """

    turns = [span for span in spans if span.get("kind") == "model_turn"]
    if not turns:
        # A malformed/partial capture can contain content without a turn.  It
        # still must not leak each stream chunk as a public top-level span.
        spans[:] = [span for span in spans if span.get("kind") not in CONTENT_KINDS]
        return 0

    turns_by_id = {
        str(turn.get("id")): turn for turn in turns if turn.get("id") is not None
    }
    ordered_turns = sorted(turns, key=lambda turn: _number(turn.get("start_ms")))

    def owning_turn(span: dict[str, Any]) -> dict[str, Any]:
        parent_id = span.get("parent_id")
        parent = turns_by_id.get(str(parent_id)) if parent_id is not None else None
        if parent is not None:
            return parent
        start = _number(span.get("start_ms"))
        candidates = [
            turn for turn in ordered_turns if _number(turn.get("start_ms")) <= start
        ]
        return candidates[-1] if candidates else ordered_turns[0]

    # Keep the original list position as the final tie-breaker.  Stream
    # captures frequently assign the same receipt timestamp to several
    # chunks; sorting those by their generated IDs can turn ``10`` before
    # ``2`` and silently corrupt the model's sequence.
    activity = [
        (position, span)
        for position, span in enumerate(spans)
        if span.get("kind") in STEP_KINDS
    ]
    activity.sort(key=lambda item: (_number(item[1].get("start_ms")), item[0]))

    for turn in turns:
        turn["steps"] = []

    for _, span in activity:
        turn = owning_turn(span)
        kind = str(span.get("kind"))
        steps = turn["steps"]
        previous = steps[-1] if steps else None
        if kind in CONTENT_KINDS and previous and previous.get("kind") == kind:
            previous["output"] = _append_text(
                previous.get("output"), span.get("output")
            )
            previous["end_ms"] = max(
                _number(previous.get("end_ms")), _number(span.get("end_ms"))
            )
            previous["duration_ms"] = max(
                0.0, previous["end_ms"] - _number(previous.get("start_ms"))
            )
            previous["status"] = _merge_status(
                previous.get("status"), span.get("status")
            )
            source_id = _source_id(span)
            if source_id is not None:
                previous.setdefault("source_span_ids", []).append(source_id)
            continue

        start = _number(span.get("start_ms"))
        end = max(start, _number(span.get("end_ms")))
        source_id = _source_id(span)
        step = {
            "sequence": len(steps) + 1,
            "kind": kind,
            "name": span.get("name"),
            "status": span.get("status"),
            "start_ms": start,
            "end_ms": end,
            "duration_ms": max(0.0, end - start),
            "input": span.get("input"),
            "output": span.get("output"),
            "source_span_ids": [source_id] if source_id is not None else [],
        }
        if kind == "tool_call":
            step["span_id"] = span.get("id")
        steps.append(step)
        # ACP creates activity spans before its synthetic model turn exists;
        # normalize their parent now while leaving MCP wire children attached
        # to the tool span for exact transport correlation.
        parent_id = span.get("parent_id")
        if parent_id is None or str(parent_id) not in turns_by_id:
            span["parent_id"] = turn.get("id")

    # Content belongs to the model turn now. Keeping these spans would expose
    # every streaming chunk as a separate top-level activity again.
    spans[:] = [span for span in spans if span.get("kind") not in CONTENT_KINDS]
    return sum(
        1
        for turn in turns
        for step in turn.get("steps", [])
        if step.get("kind") == "thinking"
    )


__all__ = ["attach_model_steps"]
