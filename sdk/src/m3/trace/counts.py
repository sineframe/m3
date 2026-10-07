"""Shared event-derived counters used by execution snapshots."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..observability import ToolCallEntry
from ..types import Event, EventKind, EventOrigin
from .pairing import (
    CallKey,
    canonical_arguments,
    pair_reported_wire,
    wire_provider_call_id,
)
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
    # A new tools/call request may be a retry linked to an earlier
    # input-required result. Reproject only MRTR histories so snapshot counts
    # use the same logical-operation grouping as TraceView without regressing
    # the incremental counter for ordinary calls.
    if any(_is_mrtr_event(event) for event in events):
        return tool_call_count(events)
    requests = tuple(_tool_requests(events))
    origins = {
        event.provenance.origin is EventOrigin.HARNESS_REPORTED for event in requests
    }
    if len(origins) == 1:
        return previous + tool_call_count(appended)
    return _mixed_request_count(requests)


def has_tool_request(events: Sequence[Event]) -> bool:
    """Report whether appending these events can change a stored count."""
    return bool(_tool_requests(events))


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


def _is_mrtr_event(event: Event) -> bool:
    if event.kind is not EventKind.TOOL_CALL_REQUESTED and not (
        event.kind is EventKind.MCP_REQUEST
        and event.payload.get("method") == "tools/call"
    ):
        return False
    params = event.payload.get("params")
    if isinstance(params, Mapping) and (
        "requestState" in params
        or "request_state" in params
        or "inputResponses" in params
        or "input_responses" in params
    ):
        return True
    return False


@dataclass(frozen=True)
class _RequestDescriptor:
    reported: bool
    key: CallKey


def _request_descriptor(event: Event) -> _RequestDescriptor | None:
    params = event.payload.get("params")
    if "params" in event.payload and not isinstance(params, Mapping):
        return None
    params = params if isinstance(params, Mapping) else {}
    name = params.get("name", event.payload.get("tool"))
    if not isinstance(name, str) or not name:
        return None
    reported = event.provenance.origin is EventOrigin.HARNESS_REPORTED
    call_id = event.payload.get("call_id")
    if "call_id" not in event.payload and not reported:
        call_id = wire_provider_call_id(params)
    arguments_present = "arguments" in params or "arguments" in event.payload
    return _RequestDescriptor(
        reported=reported,
        key=CallKey(
            provider_call_id=call_id if isinstance(call_id, str) and call_id else None,
            turn_id=str(event.turn_id.root) if event.turn_id is not None else None,
            server=event.server_binding if event.server_binding else None,
            tool=name,
            arguments=(
                canonical_arguments(
                    params.get("arguments", event.payload.get("arguments"))
                )
                if arguments_present
                else None
            ),
        ),
    )


def _mixed_request_count(events: Sequence[Event]) -> int:
    descriptors = [
        descriptor
        for event in events
        if (descriptor := _request_descriptor(event)) is not None
    ]
    reported = [item.key for item in descriptors if item.reported]
    wire = [item.key for item in descriptors if not item.reported]
    return len(descriptors) - len(pair_reported_wire(reported, wire))


__all__ = ["has_tool_request", "tool_call_count", "tool_call_count_after"]
