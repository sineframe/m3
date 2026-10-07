"""Pair harness-reported tool calls with the wire calls they describe.

An agent's MCP tool call is usually seen twice: once on the wire and once in
the provider's own history. Both the trace projector and the incremental
snapshot counter use this module, so the trace view and the stored tool call
count always agree on which reports and wire calls are the same call.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# `_meta` keys a harness uses to stamp its own tool call id on `tools/call`.
# Claude Code sends the provider's `tool_use` id here.
PROVIDER_CALL_ID_META_KEYS = ("claudecode/toolUseId",)


def wire_provider_call_id(params: Mapping[str, Any]) -> str | None:
    """The provider call id a harness stamped on a wire `tools/call` request."""
    metadata = params.get("_meta")
    if not isinstance(metadata, Mapping):
        return None
    for key in PROVIDER_CALL_ID_META_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def canonical_arguments(value: Any) -> str | None:
    """Canonical JSON text of plain tool arguments, or None if not JSON."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CallKey:
    """What pairing may look at for one tool call request."""

    provider_call_id: str | None
    turn_id: str | None
    server: str | None
    tool: str | None
    arguments: str | None


def pair_reported_wire(
    reported: Sequence[CallKey], wire: Sequence[CallKey]
) -> dict[int, int]:
    """Map reported call positions to the wire call each one describes.

    Both sequences must be in trace order. A shared provider call id is
    authoritative. Without one, calls are compared only within the same turn,
    server and tool: equal arguments pair a call when they pick out exactly
    one partner on each side, and whatever is left pairs in order only when
    both sides have the same number of calls. Anything still ambiguous stays
    unpaired, so a guess never joins two different calls.
    """
    pairs: dict[int, int] = {}
    used: set[int] = set()
    by_id: dict[str, list[int]] = {}
    for index, call in enumerate(wire):
        if call.provider_call_id is not None:
            by_id.setdefault(call.provider_call_id, []).append(index)
    for index, call in enumerate(reported):
        if call.provider_call_id is None:
            continue
        matches = by_id.get(call.provider_call_id, [])
        if len(matches) == 1 and matches[0] not in used:
            pairs[index] = matches[0]
            used.add(matches[0])

    groups: dict[tuple[str | None, str, str], tuple[list[int], list[int]]] = {}
    for index, call in enumerate(reported):
        if index not in pairs and call.server and call.tool:
            key = (call.turn_id, call.server, call.tool)
            groups.setdefault(key, ([], []))[0].append(index)
    for index, call in enumerate(wire):
        if index not in used and call.server and call.tool:
            key = (call.turn_id, call.server, call.tool)
            if key in groups:
                groups[key][1].append(index)

    def compatible(left: int, right: int) -> bool:
        # Two different ids are two different calls; matching ids were
        # already paired above.
        return (
            reported[left].provider_call_id is None
            or wire[right].provider_call_id is None
        )

    for group_reported, group_wire in groups.values():
        by_arguments: dict[int, list[int]] = {
            left: [
                right
                for right in group_wire
                if compatible(left, right)
                and reported[left].arguments is not None
                and reported[left].arguments == wire[right].arguments
            ]
            for left in group_reported
        }
        for left, rights in by_arguments.items():
            if len(rights) != 1:
                continue
            right = rights[0]
            if sum(right in other for other in by_arguments.values()) != 1:
                continue
            pairs[left] = right
            used.add(right)
        left_over = [left for left in group_reported if left not in pairs]
        right_over = [right for right in group_wire if right not in used]
        if len(left_over) != len(right_over):
            continue
        ordered = list(zip(left_over, right_over, strict=True))
        if all(compatible(left, right) for left, right in ordered):
            for left, right in ordered:
                pairs[left] = right
                used.add(right)
    return pairs


__all__ = [
    "PROVIDER_CALL_ID_META_KEYS",
    "CallKey",
    "canonical_arguments",
    "pair_reported_wire",
    "wire_provider_call_id",
]
