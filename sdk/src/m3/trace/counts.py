"""Shared event-derived counters used by execution snapshots."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..observability import ToolCallEntry
from ..types import Event, EventKind, EventOrigin
from .projector import TraceProjector


def tool_call_count(events: Sequence[Event]) -> int:
    """Count calls using the same reported/wire coalescing as TraceView."""
    entries = TraceProjector._timeline(events)
    return sum(isinstance(entry, ToolCallEntry) for entry in entries)


def tool_call_count_after(
    previous: int, events: Sequence[Event], appended: Sequence[Event]
) -> int:
    """Update a stored count without projecting for unrelated appends.

    Tool responses can complete an existing entry but cannot create one. A
    homogeneous request source can be counted from the new batch; mixed
    reported and wire evidence uses a lightweight request matcher because
    requests may merge across those sources.
    """
    appended_requests = tuple(_tool_requests(appended))
    if not appended_requests:
        return previous
    requests = tuple(_tool_requests(events))
    origins = {
        event.provenance.origin is EventOrigin.HARNESS_REPORTED for event in requests
    }
    if len(origins) == 1:
        return previous + tool_call_count(appended)
    return _mixed_request_count(requests)


def _tool_requests(events: Sequence[Event]) -> list[Event]:
    return [
        event
        for event in events
        if event.kind is EventKind.TOOL_CALL_REQUESTED
        or (
            event.kind is EventKind.MCP_REQUEST
            and event.payload.get("method") == "tools/call"
        )
    ]


@dataclass(frozen=True)
class _RequestDescriptor:
    reported: bool
    call_id: str | None
    turn_id: str | None
    server: str | None
    tool: str


def _request_descriptor(event: Event) -> _RequestDescriptor | None:
    params = event.payload.get("params")
    if "params" in event.payload and not isinstance(params, Mapping):
        return None
    params = params if isinstance(params, Mapping) else {}
    name = params.get("name", event.payload.get("tool"))
    if not isinstance(name, str) or not name:
        return None
    call_id = event.payload.get("call_id")
    return _RequestDescriptor(
        reported=event.provenance.origin is EventOrigin.HARNESS_REPORTED,
        call_id=call_id if isinstance(call_id, str) and call_id else None,
        turn_id=str(event.turn_id.root) if event.turn_id is not None else None,
        server=event.server_binding if event.server_binding else None,
        tool=name,
    )


def _mixed_request_count(events: Sequence[Event]) -> int:
    descriptors = [
        descriptor
        for event in events
        if (descriptor := _request_descriptor(event)) is not None
    ]
    reported = [descriptor for descriptor in descriptors if descriptor.reported]
    wire = [descriptor for descriptor in descriptors if not descriptor.reported]
    used: set[int] = set()
    merged = 0
    for provider in reported:
        candidates: list[int] = []
        for index, candidate in enumerate(wire):
            if index in used:
                continue
            if provider.call_id is not None and candidate.call_id is not None:
                if candidate.call_id != provider.call_id:
                    continue
            elif (
                candidate.turn_id != provider.turn_id
                or candidate.server is None
                or provider.server is None
                or candidate.server != provider.server
                or candidate.tool != provider.tool
            ):
                continue
            candidates.append(index)
        if len(candidates) == 1:
            used.add(candidates[0])
            merged += 1
    return len(descriptors) - merged


__all__ = ["tool_call_count", "tool_call_count_after"]
