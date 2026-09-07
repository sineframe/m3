"""Pure projection of finalized stable events into :class:`TraceView`."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from math import isfinite
from typing import Any, Literal, TypeVar, cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from ..errors import TraceNotFinalized, TraceUnavailable
from ..observability import (
    ACPTrace,
    ArtifactEntry,
    ClaudeCodeTrace,
    CorrelationState,
    DiagnosticEntry,
    DirectTrace,
    EvaluationEntry,
    EvidenceConflict,
    InitializationEntry,
    InitializationValue,
    InteractionEntry,
    LifecycleEntry,
    MessageEntry,
    MessageRole,
    Observation,
    ObservationReason,
    ObservationState,
    OpenCodeTrace,
    ProcessEntry,
    ProtocolEntry,
    ProtocolErrorInfo,
    ProtocolKind,
    ProviderEntry,
    EvidenceCapture,
    RawEvidenceSource,
    RawMessageEntry,
    ReasoningEntry,
    ReportedToolCall,
    RuntimeTraceInfo,
    ToolCallEntry,
    ToolCallStatus,
    ToolResult,
    TransportEntry,
    TraceEntry,
    TraceStatus,
    TraceSummary,
    TraceTiming,
    TraceView,
    UsageEntry,
    UsageValue,
    WireToolCall,
    WorkspaceEntry,
)
from ..types import (
    ActivityHealth,
    ArtifactRef,
    Event,
    ContentBlock,
    EventDirection,
    EventKind,
    EventOrigin,
    ExecutionId,
    ExecutionOutcome,
    JsonRpcId,
    LifecyclePhase,
    ReasoningState,
    ReasoningVisibility,
    TraceId,
    TraceResult,
    TransportKind,
)

_ACP_INTERACTION_REQUESTS = frozenset({
    EventKind.FILESYSTEM_READ_REQUEST,
    EventKind.FILESYSTEM_WRITE_REQUEST,
    EventKind.TERMINAL_CREATE_REQUEST,
    EventKind.TERMINAL_OUTPUT_REQUEST,
    EventKind.TERMINAL_WAIT_REQUEST,
    EventKind.TERMINAL_RELEASE_REQUEST,
    EventKind.TERMINAL_KILL_REQUEST,
})
_ACP_INTERACTION_RESPONSES = frozenset({
    EventKind.FILESYSTEM_READ_RESPONSE,
    EventKind.FILESYSTEM_WRITE_RESPONSE,
    EventKind.TERMINAL_CREATE_RESPONSE,
    EventKind.TERMINAL_OUTPUT_RESPONSE,
    EventKind.TERMINAL_WAIT_RESPONSE,
    EventKind.TERMINAL_RELEASE_RESPONSE,
    EventKind.TERMINAL_KILL_RESPONSE,
})

_JSON_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_CONTENT_ADAPTER: TypeAdapter[ContentBlock] = TypeAdapter(ContentBlock)
_JSON_INVALID = object()
_MALFORMED_INITIALIZATION = "__mcp_pal_malformed_initialization__"
_T = TypeVar("_T")



def _not_emitted() -> Observation[Any]:
    return Observation(
        state=ObservationState.NOT_EMITTED,
        reason=ObservationReason.PROVIDER_DID_NOT_EMIT,
    )


def _observed(value: _T) -> Observation[_T]:
    return Observation(state=ObservationState.OBSERVED, value=value)


def _unavailable(
    reason: ObservationReason = ObservationReason.CAPTURE_FAILED,
) -> Observation[Any]:
    return Observation(state=ObservationState.UNAVAILABLE, reason=reason)


def _capture_marker(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("capture") == "unavailable"
        and value.get("reason") == "malformed_source"
    )


def _safe_json(value: Any) -> JsonValue | object:
    try:
        return _JSON_ADAPTER.validate_python(_thaw_json(value))
    except (TypeError, ValueError, ValidationError):
        return _JSON_INVALID


def _json_observation(value: Any, *, present: bool = True) -> Observation[Any]:
    """Represent JSON validation failure explicitly instead of as null."""
    if not present:
        return _not_emitted()
    if _capture_marker(value):
        return _unavailable(ObservationReason.MALFORMED_SOURCE)
    safe = _safe_json(value)
    return (
        _unavailable(ObservationReason.MALFORMED_SOURCE)
        if safe is _JSON_INVALID
        else _observed(cast(JsonValue, safe))
    )


def _string_observation(
    value: Any, *, present: bool = True, allow_empty: bool = False
) -> Observation[str]:
    if not present:
        return _not_emitted()
    return (
        _observed(value)
        if isinstance(value, str) and (allow_empty or value)
        else _unavailable(ObservationReason.MALFORMED_SOURCE)
    )


def _transport_observation(
    payload: Mapping[str, Any], key: str
) -> Observation[TransportKind]:
    """Project a transport kind without accepting malformed source values."""

    if key in payload:
        raw = payload[key]
    elif "transport" in payload:
        # Older/direct lifecycle events carried one transport value.  It is
        # authoritative for both configured and instrumented views.
        raw = payload["transport"]
    else:
        return _not_emitted()
    if isinstance(raw, str):
        try:
            return _observed(TransportKind(raw))
        except ValueError:
            pass
    return _unavailable(ObservationReason.MALFORMED_SOURCE)


def _identifier_observation(value: Any, *, present: bool = True) -> Observation[str]:
    """Project bounded printable identifiers without guessing malformed data."""

    if not present:
        return _not_emitted()
    if (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 256
        and all(ord(char) >= 0x20 and ord(char) != 0x7F for char in value)
    ):
        return _observed(value)
    return _unavailable(ObservationReason.MALFORMED_SOURCE)


def _int_observation(value: Any, *, present: bool = True) -> Observation[int]:
    if not present:
        return _not_emitted()
    return (
        _observed(value)
        if isinstance(value, int) and not isinstance(value, bool)
        else _unavailable(ObservationReason.MALFORMED_SOURCE)
    )


def _float_observation(value: Any, *, present: bool = True) -> Observation[float]:
    if not present:
        return _not_emitted()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _unavailable(ObservationReason.MALFORMED_SOURCE)
    if not isfinite(value) or value < 0:
        return _unavailable(ObservationReason.MALFORMED_SOURCE)
    return _observed(float(value))


def _stderr_observation(payload: Mapping[str, Any]) -> Observation[str]:
    state = payload.get("stderr_state")
    unavailable = {
        "disabled": ObservationReason.CAPTURE_DISABLED,
        "unavailable": ObservationReason.CAPTURE_FAILED,
        "truncated": ObservationReason.EVIDENCE_TRUNCATED,
        "redacted": ObservationReason.REDACTED_BY_POLICY,
    }
    if isinstance(state, str) and state in unavailable:
        return Observation(
            state=(
                ObservationState.TRUNCATED
                if state == "truncated"
                else ObservationState.REDACTED
                if state == "redacted"
                else ObservationState.UNAVAILABLE
            ),
            reason=unavailable[state],
        )
    return _string_observation(
        payload.get("stderr"), present="stderr" in payload, allow_empty=True
    )


def _same_observed_text(left: Observation[Any], right: Observation[Any]) -> bool:
    return (
        left.state is ObservationState.OBSERVED
        and right.state is ObservationState.OBSERVED
        and isinstance(left.value, str)
        and left.value == right.value
    )


def _merge_tool_evidence(reported: ToolCallEntry, wire: ToolCallEntry) -> ToolCallEntry:
    """Build one correlated entry with wire fields authoritative."""
    first_timing = min(
        (reported.timing, wire.timing), key=lambda timing: timing.start_offset_ms
    )
    last_timing = max(
        (reported.timing, wire.timing), key=lambda timing: timing.end_offset_ms
    )
    timing = first_timing.model_copy(
        update={
            "finished_at": last_timing.finished_at,
            "end_offset_ms": last_timing.end_offset_ms,
            "duration_ms": max(
                0.0, last_timing.end_offset_ms - first_timing.start_offset_ms
            ),
        }
    )
    provider_call_id = (
        reported.provider_call_id
        if reported.provider_call_id.state is ObservationState.OBSERVED
        else wire.provider_call_id
    )
    return wire.model_copy(
        update={
            "call_id": reported.call_id,
            "provider_call_id": provider_call_id,
            "sequence_start": min(reported.sequence_start, wire.sequence_start),
            "sequence_end": max(reported.sequence_end, wire.sequence_end),
            "timing": timing,
            "provenance": reported.provenance + wire.provenance,
            "reported": reported.reported,
            "wire": wire.wire,
            "correlation": CorrelationState.CORRELATED,
            "conflicts": _tool_evidence_conflicts(reported, wire),
        }
    )


def _tool_evidence_conflicts(
    reported: ToolCallEntry, wire: ToolCallEntry
) -> tuple[EvidenceConflict, ...]:
    if reported.reported.state is not ObservationState.OBSERVED:
        return ()
    reported_value = reported.reported.value
    if reported_value is None:
        return ()
    wire_value = (
        wire.wire.value if wire.wire.state is ObservationState.OBSERVED else None
    )
    if wire_value is None:
        return ()
    wire_result = wire_value.result
    if wire_result.state is ObservationState.OBSERVED and wire_result.value is not None:
        wire_result_observation = _observed(_tool_result_projection(wire_result.value))
    else:
        wire_result_observation = _unavailable(
            ObservationReason.CORRELATION_UNAVAILABLE
        )
    pairs: list[
        tuple[
            Literal["server", "tool", "arguments", "result", "status"],
            Observation[Any],
            Observation[Any],
        ]
    ] = [
        ("server", reported_value.server, wire_value.server),
        ("tool", reported_value.tool, wire_value.tool),
        ("arguments", reported_value.arguments, wire_value.arguments),
        (
            "result",
            reported_value.result,
            wire_result_observation,
        ),
        (
            "status",
            reported_value.status,
            _observed(wire.tool_status.value),
        ),
    ]
    conflicts: list[EvidenceConflict] = []
    for field, left, right in pairs:
        if (
            left.state is ObservationState.OBSERVED
            and right.state is ObservationState.OBSERVED
            and not _json_equal(
                _normalize_result_json(left.value) if field == "result" else left.value,
                _normalize_result_json(right.value)
                if field == "result"
                else right.value,
            )
        ):
            conflicts.append(EvidenceConflict(field=field, reported=left, wire=right))
    return tuple(conflicts)


def _tool_result_projection(result: ToolResult) -> JsonValue:
    value: dict[str, JsonValue] = {
        "content": [block.model_dump(mode="json") for block in result.content]
    }
    if result.structured_content.state is ObservationState.OBSERVED:
        value["structuredContent"] = cast(JsonValue, result.structured_content.value)
    if result.is_error:
        value["isError"] = True
    if (
        result.error.state is ObservationState.OBSERVED
        and result.error.value is not None
    ):
        value["error"] = cast(JsonValue, result.error.value.model_dump(mode="json"))
    return value


def _json_equal(left: Any, right: Any) -> bool:
    return _thaw_json(left) == _thaw_json(right)


def _normalize_result_json(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    normalized: dict[str, Any] = {}
    if "content" in value:
        content = value["content"]
        normalized["content"] = (
            [
                _normalize_content(item) if isinstance(item, Mapping) else item
                for item in content
            ]
            if isinstance(content, (list, tuple))
            else content
        )
    for source, target in (
        ("structured_content", "structuredContent"),
        ("structuredContent", "structuredContent"),
        ("is_error", "isError"),
        ("isError", "isError"),
        ("error", "error"),
    ):
        if source in value:
            normalized_value = value[source]
            # ToolResult's false error flag is the typed default and is
            # equivalent to an omitted flag. Preserve explicit null for all
            # other JSON fields so availability remains distinguishable.
            if target == "isError" and normalized_value is False:
                continue
            normalized[target] = normalized_value
    return normalized


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [_thaw_json(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json(item) for item in value]
    return value


def _entry_id(events: Sequence[Event]) -> str:
    seed = "|".join(str(event.event_id.root) for event in events)
    return f"entry:{hashlib.sha256(seed.encode('utf-8')).hexdigest()}"


def _timing(events: Sequence[Event]) -> TraceTiming:
    first, last = events[0], events[-1]
    start = first.monotonic_offset_ms
    end = max(start, last.monotonic_offset_ms)
    started_at = first.timestamp
    finished_at = last.timestamp
    if first.provenance.origin is EventOrigin.HARNESS_REPORTED:
        source_start = _source_clock(first)
        source_end = _source_clock(last)
        if source_start is not None and source_end is not None:
            started_at, start = source_start
            finished_at, end = source_end
            if end < start:
                end = start
                finished_at = started_at
    return TraceTiming(
        started_at=started_at,
        finished_at=finished_at,
        start_offset_ms=start,
        end_offset_ms=end,
        duration_ms=end - start,
    )


def _source_clock(event: Event) -> tuple[datetime, float] | None:
    wall_time = event.payload.get("wall_time")
    offset = event.payload.get("monotonic_offset_ms")
    if not isinstance(wall_time, str) or not isinstance(offset, (int, float)):
        return None
    if isinstance(offset, bool) or offset < 0 or not isfinite(offset):
        return None
    try:
        timestamp = datetime.fromisoformat(wall_time)
    except ValueError:
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    return timestamp, float(offset)


def _base_kwargs(
    events: Sequence[Event], *, status: TraceStatus = TraceStatus.COMPLETED
) -> dict[str, Any]:
    first = events[0]
    return {
        "entry_id": _entry_id(events),
        "execution_id": first.execution_id,
        "session_id": first.session_id,
        "turn_id": first.turn_id,
        "server_binding": first.server_binding,
        "connection_id": first.connection_id,
        "sequence_start": first.sequence,
        "sequence_end": events[-1].sequence,
        "timing": _timing(events),
        "status": status,
        "provenance": tuple(event.provenance for event in events),
    }


def _payload(event: Event, key: str, default: Any = None) -> Any:
    value = event.payload.get(key, default)
    return value


def _field(event: Event, *names: str) -> Any:
    for name in names:
        if name in event.payload:
            return event.payload[name]
    return None


def _id_observation(event: Event) -> Observation[JsonRpcId]:
    correlation = event.correlation
    if correlation is None or correlation.jsonrpc_id is None:
        return _not_emitted()
    return _observed(correlation.jsonrpc_id)


def _expects_response(event: Event) -> bool:
    if event.kind not in {
        EventKind.MCP_REQUEST,
        EventKind.TOOL_CALL_REQUESTED,
        EventKind.PERMISSION_REQUEST,
        EventKind.SAMPLING_REQUEST,
        EventKind.ELICITATION_REQUEST,
    }:
        return False
    method = event.payload.get("method")
    return not (isinstance(method, str) and method.startswith("notifications/"))


def _call_id(event: Event) -> str:
    explicit = event.payload.get("call_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    identifier = event.correlation.jsonrpc_id if event.correlation is not None else None
    if isinstance(identifier, bool):
        return f"event:{event.event_id.root}"
    if isinstance(identifier, int):
        return f"int:{identifier}"
    if isinstance(identifier, str):
        return f"str:{identifier}"
    return f"event:{event.event_id.root}"


def _content(value: Any) -> tuple[ContentBlock, ...]:
    if not isinstance(value, (list, tuple)):
        return (
            _CONTENT_ADAPTER.validate_python(
                {
                    "kind": "opaque",
                    "provider": "mcp",
                    "payload": {"malformed": True},
                }
            ),
        )
    result: list[ContentBlock] = []
    for item in value:
        try:
            if isinstance(item, Mapping):
                item = _normalize_content(item)
            else:
                safe_item = _safe_json(item)
                item = {
                    "kind": "opaque",
                    "provider": "mcp",
                    "payload": (
                        {"value": cast(JsonValue, safe_item)}
                        if safe_item is not _JSON_INVALID
                        else {"malformed": True}
                    ),
                }
            result.append(_CONTENT_ADAPTER.validate_python(item))
        except (TypeError, ValueError, ValidationError):
            result.append(
                _CONTENT_ADAPTER.validate_python(
                    {
                        "kind": "opaque",
                        "provider": "mcp",
                        "payload": {"malformed": True},
                    }
                )
            )
    return tuple(result)


def _content_is_malformed(value: Any) -> bool:
    if not isinstance(value, (list, tuple)):
        return True
    for item in value:
        if not isinstance(item, Mapping):
            return True
        kind = item.get("kind", item.get("type"))
        if kind == "text" and not isinstance(item.get("text"), str):
            return True
        if kind in {"image", "audio"} and not (
            isinstance(item.get("data"), str) or isinstance(item.get("uri"), str)
        ):
            return True
        if kind in {"resource", "resource_link"} and not isinstance(
            item.get("uri"), str
        ):
            return True
        if kind == "file" and not isinstance(item.get("path"), str):
            return True
    return False


def _normalize_content(item: Mapping[str, Any]) -> Mapping[str, Any]:
    """Convert official MCP content block keys to the SDK's stable keys."""

    kind = item.get("kind", item.get("type"))
    if kind == "text" and isinstance(item.get("text"), str):
        return {"kind": "text", "text": item.get("text", "")}
    if kind in {"image", "audio"} and (
        isinstance(item.get("data"), str) or isinstance(item.get("uri"), str)
    ):
        return {
            "kind": kind,
            "media_type": item.get(
                "media_type", item.get("mimeType", "application/octet-stream")
            ),
            **(
                {"data": item["data"]}
                if item.get("data") is not None
                else {"uri": item.get("uri")}
            ),
        }
    if (
        kind in {"resource", "resource_link"}
        and isinstance(item.get("uri"), str)
        and item.get("uri")
    ):
        return {
            "kind": "resource_link",
            "uri": item.get("uri", ""),
            "name": item.get("name"),
            "description": item.get("description"),
            "media_type": item.get("media_type", item.get("mimeType")),
        }
    if kind == "file" and isinstance(item.get("path"), str) and item.get("path"):
        return {
            "kind": "file",
            "path": item.get("path", ""),
            "media_type": item.get("mediaType"),
        }
    return {"kind": "opaque", "provider": "mcp", "payload": dict(item)}


def _normalize_init_item(item: Any, adapter: Any) -> Any:
    if not isinstance(item, Mapping):
        return item
    values = dict(item)
    aliases = {
        "inputSchema": "input_schema",
        "outputSchema": "output_schema",
        "mimeType": "mime_type",
        "uriTemplate": "uri_template",
    }
    for source, target in aliases.items():
        if source in values and target not in values:
            values[target] = values.pop(source)
    return TypeAdapter(adapter).validate_python(values)


def _tool_result(event: Event) -> ToolResult:
    if (
        event.kind in {EventKind.MCP_RESPONSE, EventKind.TOOL_RESULT_RECEIVED}
        and "result" not in event.payload
    ):
        return ToolResult(error=_unavailable(ObservationReason.MALFORMED_SOURCE))
    result = _payload(event, "result", {})
    if not isinstance(result, Mapping):
        return ToolResult(error=_unavailable(ObservationReason.MALFORMED_SOURCE))
    content = _content(result.get("content", ()))
    structured_present = "structuredContent" in result or "structured_content" in result
    structured = result.get("structuredContent", result.get("structured_content"))
    error = result.get("error")
    if error is None and event.kind is EventKind.MCP_ERROR:
        error = event.payload.get("error")
    error_observation: Observation[Any]
    if isinstance(error, Mapping):
        try:
            from ..types import ErrorCode, ErrorInfo

            raw_message = error.get("message", "tool error")
            if "message" in error and (
                not isinstance(raw_message, str) or not raw_message
            ):
                raise ValueError("tool error message is malformed")
            try:
                error_code = ErrorCode(error.get("code", "protocol_error"))
            except (TypeError, ValueError):
                error_code = ErrorCode.PROTOCOL_ERROR
            error_observation = _observed(
                ErrorInfo(
                    code=error_code,
                    message=raw_message[:4096],
                )
            )
        except (TypeError, ValueError, ValidationError):
            error_observation = _unavailable(ObservationReason.MALFORMED_SOURCE)
    elif "error" in result or (
        event.kind is EventKind.MCP_ERROR and "error" in event.payload
    ):
        error_observation = _unavailable(ObservationReason.MALFORMED_SOURCE)
    else:
        error_observation = _not_emitted()
    raw_is_error = result.get("isError", result.get("is_error", False))
    is_error = (
        raw_is_error
        if isinstance(raw_is_error, bool)
        else event.kind is EventKind.MCP_ERROR
    )
    return ToolResult(
        content=content,
        structured_content=_json_observation(structured, present=structured_present),
        is_error=is_error,
        error=error_observation,
    )


def _raw_entry(event: Event) -> RawMessageEntry:
    reference = event.raw_evidence_ref
    assert reference is not None
    source = RawEvidenceSource.MCP
    source_name = event.provenance.source.lower()
    if "stderr" in source_name:
        source = RawEvidenceSource.PROCESS_STDERR
    elif "acp" in source_name:
        source = RawEvidenceSource.ACP
    elif "opencode" in source_name:
        source = RawEvidenceSource.OPENCODE
    elif "claude" in source_name:
        source = RawEvidenceSource.CLAUDE_CODE
    preview: Observation[Any] = _not_emitted()
    size_bytes = reference.size_bytes or 0
    raw_capture = event.payload.get("raw_capture")
    if isinstance(raw_capture, Mapping):
        try:
            capture = EvidenceCapture.model_validate(raw_capture)
            if capture.reference != reference:
                raise ValueError("raw capture reference does not match event")
            preview = capture.preview
            size_bytes = capture.stored_size_bytes
        except (TypeError, ValueError, ValidationError):
            preview = _unavailable(ObservationReason.MALFORMED_SOURCE)
    return RawMessageEntry(
        **_base_kwargs((event,)),
        source=source,
        direction=event.correlation.direction
        if event.correlation
        else EventDirection.INTERNAL,
        media_type=reference.media_type or "application/octet-stream",
        preview=preview,
        evidence_ref=reference,
        size_bytes=size_bytes,
    )


def _protocol_entry(events: Sequence[Event]) -> ProtocolEntry:
    first, last = events[0], events[-1]
    payload = first.payload
    method = _field(first, "method")
    error_value = last.payload.get("error")
    error_observation: Observation[Any]
    if isinstance(error_value, Mapping):
        try:
            raw_code = error_value.get("code")
            if raw_code is not None and (
                isinstance(raw_code, bool) or not isinstance(raw_code, (int, str))
            ):
                raise ValueError("protocol error code is malformed")
            raw_message = error_value.get("message", "protocol error")
            if "message" in error_value and (
                not isinstance(raw_message, str) or not raw_message
            ):
                raise ValueError("protocol error message is malformed")
            error_observation = _observed(
                ProtocolErrorInfo(
                    code=cast(int | str | None, raw_code),
                    message=raw_message[:4096],
                    data=_json_observation(
                        error_value.get("data"), present="data" in error_value
                    ),
                )
            )
        except (TypeError, ValueError, ValidationError):
            error_observation = _unavailable(ObservationReason.MALFORMED_SOURCE)
    elif "error" in last.payload:
        error_observation = _unavailable(ObservationReason.MALFORMED_SOURCE)
    else:
        error_observation = _not_emitted()
    return ProtocolEntry(
        **_base_kwargs(
            events,
            status=(
                TraceStatus.PROTOCOL_ERROR
                if last.kind is EventKind.MCP_ERROR or isinstance(error_value, Mapping)
                else TraceStatus.INCOMPLETE
                if len(events) == 1 and _expects_response(first)
                else TraceStatus.COMPLETED
            ),
        ),
        protocol=ProtocolKind.MCP,
        method=_string_observation(method, present="method" in payload),
        direction=first.correlation.direction
        if first.correlation
        else EventDirection.INTERNAL,
        jsonrpc_id=_id_observation(first),
        request=_json_observation(
            payload.get("params", payload.get("request")),
            present=("params" in payload or "request" in payload),
        ),
        response=_json_observation(
            last.payload.get("result", last.payload.get("response")),
            present=("result" in last.payload or "response" in last.payload),
        ),
        error=error_observation,
    )


def _transport_entry(event: Event) -> TransportEntry:
    phase: Literal["connected", "disconnected"] = (
        "connected" if event.kind is EventKind.TRANSPORT_CONNECTED else "disconnected"
    )
    return TransportEntry(
        **_base_kwargs((event,)),
        phase=phase,
        configured=_transport_observation(event.payload, "configured_transport"),
        instrumented=_transport_observation(event.payload, "instrumented_transport"),
    )


def _interaction_entry(events: Sequence[Event]) -> InteractionEntry:
    first, last = events[0], events[-1]
    request_present = "request" in first.payload or "params" in first.payload
    request = first.payload.get("request", first.payload.get("params"))
    response_present = "response" in last.payload or "result" in last.payload
    response = last.payload.get("response", last.payload.get("result"))
    return InteractionEntry(
        **_base_kwargs(
            events,
            status=(
                TraceStatus.PROTOCOL_ERROR
                if last.kind is EventKind.MCP_ERROR
                else TraceStatus.INCOMPLETE
                if len(events) == 1 and _expects_response(first)
                else TraceStatus.COMPLETED
            ),
        ),
        interaction_kind=first.kind.value,
        request=_json_observation(request, present=request_present),
        response=_json_observation(response, present=response_present),
    )


def _tool_entry(events: Sequence[Event]) -> TraceEntry:
    first, last = events[0], events[-1]
    reported_evidence = first.provenance.origin is EventOrigin.HARNESS_REPORTED
    params_value = first.payload.get("params")
    if "params" in first.payload and not isinstance(params_value, Mapping):
        return DiagnosticEntry(
            **_base_kwargs(events, status=TraceStatus.INCOMPLETE),
            code="malformed_tool_request",
            message="tool request parameters are malformed",
        )
    params = params_value if isinstance(params_value, Mapping) else {}
    name = params.get("name", first.payload.get("tool"))
    if not isinstance(name, str) or not name:
        return DiagnosticEntry(
            **_base_kwargs(events, status=TraceStatus.INCOMPLETE),
            code="malformed_tool_request",
            message="tool request name is malformed",
        )
    arguments = params.get("arguments", first.payload.get("arguments"))
    result = (
        _tool_result(last)
        if last.kind
        in {EventKind.TOOL_RESULT_RECEIVED, EventKind.MCP_RESPONSE, EventKind.MCP_ERROR}
        else None
    )
    wire_result = (
        _observed(result)
        if result is not None and not reported_evidence
        else _not_emitted()
    )
    reported_status = last.payload.get("tool_status", last.payload.get("status"))
    try:
        status = (
            ToolCallStatus(reported_status)
            if isinstance(reported_status, str)
            else None
        )
    except ValueError:
        status = None
    reported_status_present = "tool_status" in last.payload or "status" in last.payload
    raw_result = last.payload.get("result")
    reported_error_present = isinstance(last.payload.get("error"), Mapping)
    reported_is_error = last.payload.get("isError", last.payload.get("is_error"))
    malformed_error_flag = (
        ("isError" in last.payload and not isinstance(last.payload["isError"], bool))
        or ("is_error" in last.payload and not isinstance(last.payload["is_error"], bool))
        or (
            isinstance(raw_result, Mapping)
            and (
                ("isError" in raw_result and not isinstance(raw_result["isError"], bool))
                or ("is_error" in raw_result and not isinstance(raw_result["is_error"], bool))
            )
        )
    )
    malformed_tool_result = last.kind in {
        EventKind.MCP_RESPONSE,
        EventKind.TOOL_RESULT_RECEIVED,
    } and ("result" not in last.payload or not isinstance(raw_result, Mapping))
    if last.kind in {
        EventKind.MCP_CANCELLATION_REQUESTED,
        EventKind.MCP_CANCELLATION_COMPLETED,
    }:
        status = ToolCallStatus.CANCELLED
    elif (
        reported_evidence
        and reported_error_present
        and status
        in {
            None,
            ToolCallStatus.SUCCESS,
        }
    ):
        # A harness may report a tool failure without a result body. Preserve
        # that explicit status instead of inventing a malformed/incomplete
        # wire result.
        status = ToolCallStatus.TOOL_ERROR
    elif (
        reported_evidence
        and reported_is_error is True
        and status in {None, ToolCallStatus.SUCCESS}
    ):
        status = ToolCallStatus.TOOL_ERROR
    elif (
        (reported_status_present and status is None)
        or (
            malformed_error_flag
            and status
            not in {
                ToolCallStatus.TOOL_ERROR,
                ToolCallStatus.PROTOCOL_ERROR,
                ToolCallStatus.TRANSPORT_ERROR,
                ToolCallStatus.CANCELLED,
                ToolCallStatus.TIMED_OUT,
                ToolCallStatus.INCOMPLETE,
            }
        )
        or (
            malformed_tool_result
            and not (
                reported_evidence
                and status
                in {
                    ToolCallStatus.TOOL_ERROR,
                    ToolCallStatus.PROTOCOL_ERROR,
                    ToolCallStatus.TRANSPORT_ERROR,
                    ToolCallStatus.CANCELLED,
                    ToolCallStatus.TIMED_OUT,
                    ToolCallStatus.INCOMPLETE,
                }
            )
        )
    ):
        status = ToolCallStatus.INCOMPLETE
    elif status is None and last.kind is EventKind.MCP_ERROR:
        status = ToolCallStatus.PROTOCOL_ERROR
    elif status is None and result is not None and result.is_error:
        status = ToolCallStatus.TOOL_ERROR
    elif status is None and result is not None:
        status = ToolCallStatus.SUCCESS
    elif status is None:
        status = ToolCallStatus.INCOMPLETE
    latency = None
    if len(events) > 1:
        latency = max(0.0, (last.monotonic_offset_ms - first.monotonic_offset_ms))
    wire = WireToolCall(
        jsonrpc_id=_id_observation(first),
        server=(
            _observed(first.server_binding) if first.server_binding else _not_emitted()
        ),
        tool=_observed(name),
        arguments=_json_observation(
            arguments,
            present=("arguments" in params or "arguments" in first.payload),
        ),
        result=wire_result,
        latency_ms=(_observed(latency) if latency is not None else _not_emitted()),
    )
    if result is not None:
        result_observation = _observed(result)
    else:
        result_observation = _not_emitted()
    entry_status = {
        ToolCallStatus.SUCCESS: TraceStatus.COMPLETED,
        ToolCallStatus.TOOL_ERROR: TraceStatus.TOOL_ERROR,
        ToolCallStatus.PROTOCOL_ERROR: TraceStatus.PROTOCOL_ERROR,
        ToolCallStatus.TRANSPORT_ERROR: TraceStatus.TRANSPORT_ERROR,
        ToolCallStatus.CANCELLED: TraceStatus.CANCELLED,
        ToolCallStatus.TIMED_OUT: TraceStatus.TIMED_OUT,
        ToolCallStatus.INCOMPLETE: TraceStatus.INCOMPLETE,
    }[status]
    reported_call = ReportedToolCall(
        provider_call_id=_string_observation(
            first.payload.get("call_id"), present="call_id" in first.payload
        ),
        server=_string_observation(
            first.payload.get("server", first.server_binding),
            present="server" in first.payload or first.server_binding is not None,
        ),
        tool=_string_observation(name, present=True),
        arguments=_json_observation(
            arguments,
            present=("arguments" in params or "arguments" in first.payload),
        ),
        result=(
            _json_observation(
                last.payload.get("result"), present="result" in last.payload
            )
            if len(events) > 1 and "result" in last.payload
            else _not_emitted()
        ),
        status=_string_observation(
            reported_status,
            present=reported_status_present,
        ),
    )
    return ToolCallEntry(
        **_base_kwargs(
            events,
            status=entry_status,
        ),
        call_id=_call_id(first),
        provider_call_id=_string_observation(
            first.payload.get("call_id"), present="call_id" in first.payload
        ),
        server=(
            _observed(first.server_binding) if first.server_binding else _not_emitted()
        ),
        tool=_observed(name),
        arguments=_json_observation(
            arguments,
            present=("arguments" in params or "arguments" in first.payload),
        ),
        result=result_observation,
        tool_status=status,
        correlation=(
            CorrelationState.REPORTED_ONLY
            if reported_evidence
            else CorrelationState.WIRE_ONLY
        ),
        jsonrpc_id=_id_observation(first),
        server_latency_ms=(
            _observed(latency) if latency is not None else _not_emitted()
        ),
        reported=(_observed(reported_call) if reported_evidence else _not_emitted()),
        wire=(_not_emitted() if reported_evidence else _observed(wire)),
    )


def _initialization_value(
    event: Event, value: Mapping[str, Any] | None = None
) -> InitializationValue:
    value = value if value is not None else event.payload
    if value.get(_MALFORMED_INITIALIZATION) is True:
        unavailable = _unavailable(ObservationReason.MALFORMED_SOURCE)
        return InitializationValue(
            protocol_version=unavailable,
            server_name=unavailable,
            server_version=unavailable,
            instructions=unavailable,
            capabilities=unavailable,
            tools=unavailable,
            resources=unavailable,
            resource_templates=unavailable,
            prompts=unavailable,
        )
    server_info_present = "serverInfo" in value or "server_info" in value
    server_info = value.get("serverInfo", value.get("server_info", {}))
    server_info_malformed = server_info_present and not isinstance(server_info, Mapping)
    server_info = server_info if isinstance(server_info, Mapping) else {}

    def _typed_items(keys: tuple[str, ...], adapter: Any) -> Observation[Any]:
        present_key = next((key for key in keys if key in value), None)
        if present_key is None:
            return _not_emitted()
        raw = value[present_key]
        if raw is None:
            return _unavailable(ObservationReason.MALFORMED_SOURCE)
        if not isinstance(raw, (list, tuple)):
            return _unavailable(ObservationReason.MALFORMED_SOURCE)
        try:
            return _observed(tuple(_normalize_init_item(item, adapter) for item in raw))
        except (TypeError, ValueError, ValidationError):
            return _unavailable(ObservationReason.MALFORMED_SOURCE)

    from ..types import PromptInfo, ResourceInfo, TemplateInfo, ToolInfo

    return InitializationValue(
        protocol_version=_string_observation(
            value.get("protocolVersion"), present="protocolVersion" in value
        ),
        server_name=(
            _unavailable(ObservationReason.MALFORMED_SOURCE)
            if server_info_malformed
            else _string_observation(
                server_info.get("name"), present="name" in server_info
            )
        ),
        server_version=(
            _unavailable(ObservationReason.MALFORMED_SOURCE)
            if server_info_malformed
            else _string_observation(
                server_info.get("version"), present="version" in server_info
            )
        ),
        instructions=_string_observation(
            value.get("instructions"), present="instructions" in value, allow_empty=True
        ),
        capabilities=_json_observation(
            value.get("capabilities"), present="capabilities" in value
        ),
        tools=_typed_items(("tools",), ToolInfo),
        resources=_typed_items(("resources",), ResourceInfo),
        resource_templates=_typed_items(
            ("resourceTemplates", "resource_templates"), TemplateInfo
        ),
        prompts=_typed_items(("prompts",), PromptInfo),
    )


def _entry_for_event(
    event: Event, *, initialization_payload: Mapping[str, Any] | None = None
) -> TraceEntry:
    kwargs = _base_kwargs((event,))
    payload = event.payload
    kind = event.kind
    if kind is EventKind.TOOL_CALL_REQUESTED or (
        kind is EventKind.MCP_REQUEST and payload.get("method") == "tools/call"
    ):
        return _tool_entry((event,))
    if kind in {
        EventKind.EXECUTION_CREATED,
        EventKind.EXECUTION_STATE_CHANGED,
        EventKind.EXECUTION_FINISHED,
        EventKind.SESSION_CREATED,
        EventKind.SESSION_STATE_CHANGED,
        EventKind.TURN_CREATED,
        EventKind.TURN_STATE_CHANGED,
        EventKind.CLEANUP_STARTED,
        EventKind.CLEANUP_FINISHED,
    }:
        phase_present = "lifecycle" in payload or "state" in payload
        phase = payload.get("lifecycle", payload.get("state"))
        if phase_present and (not isinstance(phase, str) or not phase):
            return DiagnosticEntry(
                **kwargs,
                code="malformed_lifecycle",
                message="lifecycle phase is malformed",
            )
        if (
            not isinstance(phase, str)
            or not phase
            or phase == LifecyclePhase.UNKNOWN.value
        ):
            phase = event.lifecycle_phase.value
        return LifecycleEntry(
            **kwargs,
            phase=str(phase) if phase != LifecyclePhase.UNKNOWN.value else kind.value,
        )
    if kind in {EventKind.PROCESS_STARTED, EventKind.PROCESS_EXITED}:
        return ProcessEntry(
            **kwargs,
            executable=_string_observation(
                payload.get("executable"), present="executable" in payload
            ),
            pid=_int_observation(payload.get("pid"), present="pid" in payload),
            exit_code=_int_observation(
                payload.get("exit_code"), present="exit_code" in payload
            ),
            signal=_int_observation(payload.get("signal"), present="signal" in payload),
            stderr=_stderr_observation(payload),
        )
    if kind in {
        EventKind.MCP_REQUEST,
        EventKind.MCP_RESPONSE,
        EventKind.MCP_ERROR,
        EventKind.MCP_NOTIFICATION,
        EventKind.MCP_PROGRESS,
        EventKind.MCP_CANCELLATION_REQUESTED,
        EventKind.MCP_CANCELLATION_COMPLETED,
    }:
        return _protocol_entry((event,))
    if kind in {EventKind.TRANSPORT_CONNECTED, EventKind.TRANSPORT_DISCONNECTED}:
        return _transport_entry(event)
    if kind in {EventKind.AGENT_MESSAGE, EventKind.ASSISTANT_CONTENT}:
        role = payload.get(
            "role", "user" if kind is EventKind.AGENT_MESSAGE else "assistant"
        )
        try:
            message_role = MessageRole(role)
        except (TypeError, ValueError):
            return DiagnosticEntry(
                **kwargs,
                code="malformed_message_role",
                message="message role is not recognized",
            )
        message_content = _content(payload.get("content", ()))
        return MessageEntry(
            **kwargs,
            message_id=_identifier_observation(
                payload.get("message_id", payload.get("block_id")),
                present="message_id" in payload or "block_id" in payload,
            ),
            role=message_role,
            content=message_content,
            stop_reason=_string_observation(
                payload.get("stop_reason"), present="stop_reason" in payload
            ),
        )
    if kind is EventKind.REASONING:
        reasoning_state = event.reasoning
        if isinstance(reasoning_state, Mapping):
            try:
                reasoning_state = TypeAdapter(ReasoningState).validate_python(
                    reasoning_state
                )
            except (TypeError, ValueError, ValidationError):
                reasoning_state = None
        state = (
            reasoning_state.visibility
            if isinstance(reasoning_state, ReasoningState)
            else ReasoningVisibility.UNAVAILABLE
        )
        content: Observation[Any]
        if state is ReasoningVisibility.VISIBLE:
            raw_content = payload.get("content")
            content = (
                _unavailable(ObservationReason.MALFORMED_SOURCE)
                if _content_is_malformed(raw_content)
                else _observed(_content(raw_content))
            )
        elif state is ReasoningVisibility.ENCRYPTED:
            content = Observation(
                state=ObservationState.ENCRYPTED,
                reason=ObservationReason.PROVIDER_ENCRYPTED,
            )
        elif state is ReasoningVisibility.PROVIDER_HIDDEN:
            content = Observation(
                state=ObservationState.PROVIDER_HIDDEN,
                reason=ObservationReason.PROVIDER_HIDDEN,
            )
        else:
            content = _unavailable()
        return ReasoningEntry(
            **kwargs,
            block_id=_string_observation(
                payload.get("block_id"), present="block_id" in payload
            ),
            content=content,
        )
    if kind is EventKind.PROVIDER_EVENT:
        if (
            not isinstance(payload.get("provider"), str)
            or not payload["provider"]
            or not isinstance(payload.get("category"), str)
            or not payload["category"]
        ):
            return DiagnosticEntry(
                **kwargs,
                code="malformed_provider_event",
                message="provider event identity is missing",
            )
        category = payload["category"]
        if category == "usage":
            return UsageEntry(
                **kwargs,
                input_tokens=_int_observation(
                    payload.get("input_tokens"), present="input_tokens" in payload
                ),
                output_tokens=_int_observation(
                    payload.get("output_tokens"), present="output_tokens" in payload
                ),
                reasoning_tokens=_int_observation(
                    payload.get("reasoning_tokens"),
                    present="reasoning_tokens" in payload,
                ),
                cache_creation_tokens=_int_observation(
                    payload.get("cache_creation_tokens"),
                    present="cache_creation_tokens" in payload,
                ),
                cache_read_tokens=_int_observation(
                    payload.get("cache_read_tokens"),
                    present="cache_read_tokens" in payload,
                ),
                cache_write_tokens=_int_observation(
                    payload.get("cache_write_tokens"),
                    present="cache_write_tokens" in payload,
                ),
                total_tokens=_int_observation(
                    payload.get("total_tokens"), present="total_tokens" in payload
                ),
                cost=_float_observation(payload.get("cost"), present="cost" in payload),
                currency=_string_observation(
                    payload.get("currency"), present="currency" in payload
                ),
            )
        return ProviderEntry(
            **kwargs,
            provider=payload["provider"],
            category=category,
            data=_json_observation(payload.get("data"), present="data" in payload),
        )
    if kind is EventKind.MCP_INITIALIZED:
        value = _initialization_value(event, initialization_payload)
        return InitializationEntry(
            **kwargs,
            protocol_version=value.protocol_version,
            server_name=value.server_name,
            server_version=value.server_version,
            instructions=value.instructions,
            capabilities=value.capabilities,
            tools=value.tools,
            resources=value.resources,
            resource_templates=value.resource_templates,
            prompts=value.prompts,
        )
    if kind in {
        EventKind.PERMISSION_REQUEST,
        EventKind.PERMISSION_RESPONSE,
        EventKind.SAMPLING_REQUEST,
        EventKind.SAMPLING_RESPONSE,
        EventKind.ELICITATION_REQUEST,
        EventKind.ELICITATION_RESPONSE,
        *_ACP_INTERACTION_REQUESTS,
        *_ACP_INTERACTION_RESPONSES,
    }:
        request_present = "request" in payload or "params" in payload
        request = payload.get("request", payload.get("params", payload))
        response_present = "response" in payload or "result" in payload
        response = payload.get("response", payload.get("result"))
        return InteractionEntry(
            **kwargs,
            interaction_kind=kind.value,
            request=_json_observation(
                request, present=request_present or kind.value.endswith("request")
            ),
            response=_json_observation(response, present=response_present),
        )
    if kind is EventKind.WORKSPACE_CHANGED:
        return WorkspaceEntry(**kwargs, change=_json_observation(payload))
    if kind is EventKind.ARTIFACT_RECORDED:
        try:
            artifact = TypeAdapter(ArtifactRef).validate_python(
                payload.get("artifact", payload)
            )
            return ArtifactEntry(**kwargs, artifact=artifact)
        except (TypeError, ValueError, ValidationError):
            return DiagnosticEntry(
                **kwargs,
                code="malformed_artifact",
                message="artifact evidence is malformed",
            )
    if kind is EventKind.EVALUATION_RECORDED:
        evaluation = payload.get("evaluation")
        if isinstance(evaluation, Mapping):
            try:
                from ..types import EvaluationResult

                return EvaluationEntry(
                    **kwargs, evaluation=EvaluationResult.model_validate(evaluation)
                )
            except (TypeError, ValueError, ValidationError):
                pass
        return DiagnosticEntry(
            **kwargs,
            code="malformed_evaluation",
            message="evaluation evidence is malformed",
        )
    if kind is EventKind.DIAGNOSTIC:
        code = payload.get("code", "diagnostic")
        message = payload.get("message", "diagnostic event")
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(message, str)
            or not message
        ):
            return DiagnosticEntry(
                **kwargs,
                code="malformed_diagnostic",
                message="diagnostic evidence is malformed",
            )
        return DiagnosticEntry(
            **kwargs,
            code=code[:128],
            message=message[:4096],
        )
    if event.raw_evidence_ref is not None:
        return RawMessageEntry(
            **kwargs,
            source=RawEvidenceSource.MCP,
            direction=event.correlation.direction
            if event.correlation
            else EventDirection.INTERNAL,
            media_type=event.raw_evidence_ref.media_type or "application/octet-stream",
            preview=_not_emitted(),
            evidence_ref=event.raw_evidence_ref,
            size_bytes=event.raw_evidence_ref.size_bytes or 0,
        )
    return DiagnosticEntry(
        **kwargs,
        code=kind.value.replace(".", "_")[:128],
        message="stable event projected without a specialized public entry",
    )


class TraceProjector:
    """Pure deterministic projector with no storage or adapter dependencies."""

    @staticmethod
    def project(trace: TraceResult) -> TraceView:
        if not isinstance(trace, TraceResult):
            raise TraceUnavailable("trace evidence is invalid")
        events = trace.events
        created_events = [
            event for event in events if event.kind is EventKind.EXECUTION_CREATED
        ]
        if (
            not events
            or events[0].sequence != 0
            or events[0].kind is not EventKind.EXECUTION_CREATED
            or len(created_events) != 1
        ):
            raise TraceUnavailable("trace execution.created evidence is malformed")
        persisted_trace_id = events[0].payload.get("trace_id")
        if (
            not isinstance(persisted_trace_id, str)
            or persisted_trace_id != trace.trace_id.root
        ):
            raise TraceUnavailable(
                "trace identity evidence conflicts with execution.created"
            )
        terminals = [
            event for event in events if event.kind is EventKind.EXECUTION_FINISHED
        ]
        if not terminals:
            raise TraceNotFinalized("execution has not been finalized")
        if len(terminals) != 1 or terminals[0] is not events[-1]:
            raise TraceUnavailable("trace terminal evidence is malformed")
        terminal = terminals[0]
        try:
            outcome = ExecutionOutcome(terminal.payload["outcome"])
        except (KeyError, TypeError, ValueError):
            raise TraceUnavailable("trace outcome evidence is malformed") from None
        completeness = terminal.payload.get("completeness")
        raw_limitations = terminal.payload.get("limitations")
        if (
            completeness not in {"complete", "partial"}
            or not isinstance(raw_limitations, (list, tuple))
            or any(
                not isinstance(item, str) or not item.strip()
                for item in raw_limitations
            )
        ):
            raise TraceUnavailable("trace terminal completeness evidence is malformed")
        if (
            completeness != trace.completeness
            or tuple(raw_limitations) != trace.limitations
        ):
            raise TraceUnavailable("trace terminal evidence conflicts with TraceResult")
        timeline = TraceProjector._timeline(events)
        runtime = TraceProjector._runtime(events)
        summary = TraceProjector._summary(trace, events, timeline, outcome)
        return TraceView(
            trace_id=trace.trace_id,
            execution_id=trace.execution_id,
            outcome=outcome,
            completeness=trace.completeness,
            limitations=trace.limitations,
            runtime=runtime,
            summary=summary,
            timeline=timeline,
        )

    @staticmethod
    def from_events(
        events: Sequence[Event],
        *,
        trace_id: TraceId | str,
        execution_id: ExecutionId | str,
        outcome: ExecutionOutcome,
        completeness: str = "complete",
        limitations: Sequence[str] = (),
    ) -> TraceView:
        if not events:
            raise TraceNotFinalized("execution has not been finalized")
        typed_trace_id = (
            trace_id if isinstance(trace_id, TraceId) else TraceId(trace_id)
        )
        typed_execution_id = (
            execution_id
            if isinstance(execution_id, ExecutionId)
            else ExecutionId(execution_id)
        )
        trace = TraceResult(
            trace_id=typed_trace_id,
            execution_id=typed_execution_id,
            completeness=cast(Any, completeness),
            highest_sequence=events[-1].sequence,
            events=tuple(events),
            limitations=tuple(limitations),
        )
        if trace.events[-1].payload.get("outcome") != outcome.value:
            raise TraceUnavailable("trace outcome evidence is malformed")
        return TraceProjector.project(trace)

    @staticmethod
    def _timeline(events: Sequence[Event]) -> tuple[TraceEntry, ...]:
        requests: dict[tuple[Any, ...], list[Event]] = {}
        reported_requests: dict[str, list[Event]] = {}
        responses: dict[int, Event] = {}
        for event in events:
            if event.kind in {
                EventKind.MCP_REQUEST,
                EventKind.TOOL_CALL_REQUESTED,
                EventKind.PERMISSION_REQUEST,
                EventKind.SAMPLING_REQUEST,
                EventKind.ELICITATION_REQUEST,
                *_ACP_INTERACTION_REQUESTS,
            }:
                key = _correlation_key(event)
                if key is not None:
                    requests.setdefault(key, []).append(event)
                call_id = _reported_call_id(event)
                if call_id is not None and event.kind is EventKind.TOOL_CALL_REQUESTED:
                    reported_requests.setdefault(call_id, []).append(event)
        used: set[str] = set()
        paired: dict[int, Event] = {}
        all_requests = tuple(item for values in requests.values() for item in values)
        for event in events:
            if event.kind not in {
                EventKind.MCP_RESPONSE,
                EventKind.MCP_ERROR,
                EventKind.TOOL_RESULT_RECEIVED,
                EventKind.MCP_CANCELLATION_COMPLETED,
                EventKind.PERMISSION_RESPONSE,
                EventKind.SAMPLING_RESPONSE,
                EventKind.ELICITATION_RESPONSE,
                *_ACP_INTERACTION_RESPONSES,
            }:
                continue
            key = _correlation_key(event)
            candidates = requests.get(key, []) if key is not None else []
            if not candidates and event.kind in _ACP_INTERACTION_RESPONSES:
                request_kind = EventKind(event.kind.value.removesuffix(".response") + ".request")
                candidates = [
                    item for item in all_requests
                    if item.kind is request_kind
                    and item.turn_id == event.turn_id
                    and item.sequence < event.sequence
                    and str(item.event_id.root) not in used
                ]
            reported_id = _reported_call_id(event)
            if event.kind in _ACP_INTERACTION_RESPONSES and candidates:
                candidates = [
                    item for item in candidates
                    if item.sequence < event.sequence
                    and str(item.event_id.root) not in used
                ]
            elif (
                reported_id is not None
                and event.provenance.origin is EventOrigin.HARNESS_REPORTED
            ):
                candidates = [
                    item
                    for item in reported_requests.get(reported_id, [])
                    if str(item.event_id.root) not in used
                    and item.sequence < event.sequence
                    and _same_reported_turn(item, event)
                ]
            if event.kind in _ACP_INTERACTION_RESPONSES and candidates:
                pass
            elif (
                reported_id is not None
                and event.provenance.origin is EventOrigin.HARNESS_REPORTED
            ):
                candidates = [
                    item
                    for item in candidates
                    if item.sequence < event.sequence
                    and str(item.event_id.root) not in used
                ]
            else:
                candidates = [
                    item
                    for item in candidates
                    if item.sequence < event.sequence
                    and str(item.event_id.root) not in used
                    and _compatible_pair(item, event)
                ]
            if not candidates:
                # Some sources retain only the typed JSON-RPC ID and omit a
                # request sequence.  Permit that loss only when the typed
                # ID, connection, direction, family, and temporal ordering
                # identify exactly one request.  Ambiguous IDs remain
                # unpaired and therefore visible as incomplete evidence.
                fallback = [
                    item
                    for item in all_requests
                    if str(item.event_id.root) not in used
                    and _fallback_pair(item, event)
                ]
                if len(fallback) == 1:
                    candidates = fallback
            if len(candidates) == 1:
                request = candidates[0]
                used.add(str(request.event_id.root))
                paired[request.sequence] = event
                responses[event.sequence] = event
        output: list[TraceEntry] = []

        def append_event(event: Event, entry: TraceEntry) -> None:
            output.append(entry)
            if event.raw_evidence_ref is not None and not isinstance(
                entry, RawMessageEntry
            ):
                output.append(_raw_entry(event))

        for event in events:
            if event.sequence in responses:
                continue
            if event.kind in {
                EventKind.MCP_REQUEST,
                EventKind.TOOL_CALL_REQUESTED,
                EventKind.PERMISSION_REQUEST,
                EventKind.SAMPLING_REQUEST,
                EventKind.ELICITATION_REQUEST,
                *_ACP_INTERACTION_REQUESTS,
            }:
                response = paired.get(event.sequence)
                is_tool = event.kind is EventKind.TOOL_CALL_REQUESTED or (
                    event.kind is EventKind.MCP_REQUEST
                    and event.payload.get("method") == "tools/call"
                )
                if is_tool:
                    append_event(
                        event, _tool_entry((event, response) if response else (event,))
                    )
                elif event.kind in {
                    EventKind.PERMISSION_REQUEST,
                    EventKind.SAMPLING_REQUEST,
                    EventKind.ELICITATION_REQUEST,
                    *_ACP_INTERACTION_REQUESTS,
                }:
                    append_event(
                        event,
                        _interaction_entry((event, response) if response else (event,)),
                    )
                else:
                    append_event(
                        event,
                        _protocol_entry((event, response) if response else (event,)),
                    )
                if response is not None and response.raw_evidence_ref is not None:
                    output.append(_raw_entry(response))
            elif event.kind in {
                EventKind.MCP_RESPONSE,
                EventKind.MCP_ERROR,
                EventKind.TOOL_RESULT_RECEIVED,
                EventKind.PERMISSION_RESPONSE,
                EventKind.SAMPLING_RESPONSE,
                EventKind.ELICITATION_RESPONSE,
            }:
                diagnostic = DiagnosticEntry(
                    **_base_kwargs((event,), status=TraceStatus.INCOMPLETE),
                    code="unmatched_response",
                    message="response had no uniquely matching request",
                )
                append_event(event, diagnostic)
            else:
                init_payload = None
                if (
                    event.kind is EventKind.MCP_INITIALIZED
                    and event.correlation is not None
                ):
                    init_requests = tuple(
                        candidate
                        for candidate in events
                        if candidate.kind is EventKind.MCP_REQUEST
                        and candidate.payload.get("method") == "initialize"
                        and candidate.connection_id == event.connection_id
                        and _initialization_request_matches(event, candidate)
                    )
                    if len(init_requests) == 1:
                        init_responses = _strict_response_candidates(
                            init_requests[0], events
                        )
                        if len(init_responses) == 1:
                            response = init_responses[0]
                            if response.kind is EventKind.MCP_RESPONSE and isinstance(
                                response.payload.get("result"), Mapping
                            ):
                                init_payload = response.payload["result"]
                            else:
                                init_payload = {_MALFORMED_INITIALIZATION: True}
                append_event(
                    event, _entry_for_event(event, initialization_payload=init_payload)
                )
        # Aggregate entries begin at their request sequence, while response
        # raw evidence belongs at the response sequence.  Sort after all
        # projections so an intervening event is never displaced by a paired
        # response's raw frame.
        output.sort(
            key=lambda entry: (entry.sequence_start, entry.sequence_end, entry.entry_id)
        )
        return TraceProjector._coalesce_harness_chunks(
            TraceProjector._correlate_reported_wire(tuple(output))
        )

    @staticmethod
    def _correlate_reported_wire(
        entries: tuple[TraceEntry, ...],
    ) -> tuple[TraceEntry, ...]:
        """Join provider and wire calls only on conservative evidence.

        An explicit provider call ID is authoritative, even when the wire
        evidence arrived first. When an adapter has no shared ID, correlation
        is permitted only if exactly one unused wire call has the same turn,
        server, and tool. The resulting sequence order remains deterministic,
        but arrival direction is not treated as identity: a provider history
        snapshot commonly arrives after the wire exchange it reports.
        Raw evidence references are intentionally not used here: the typed
        tool-call model does not expose per-source refs, so treating an event
        ref as a call identity would be an unsafe guess. A name-only match is
        never sufficient when there is more than one candidate.
        """
        reported = [
            index
            for index, entry in enumerate(entries)
            if isinstance(entry, ToolCallEntry)
            and entry.correlation is CorrelationState.REPORTED_ONLY
        ]
        wire = [
            index
            for index, entry in enumerate(entries)
            if isinstance(entry, ToolCallEntry)
            and entry.correlation is CorrelationState.WIRE_ONLY
        ]
        used: set[int] = set()
        replacements: dict[int, ToolCallEntry] = {}
        removed: set[int] = set()
        for reported_index in reported:
            reported_entry = entries[reported_index]
            assert isinstance(reported_entry, ToolCallEntry)
            provider_id = (
                reported_entry.provider_call_id.value
                if reported_entry.provider_call_id.state is ObservationState.OBSERVED
                else None
            )
            candidates: list[int] = []
            for wire_index in wire:
                if wire_index in used:
                    continue
                wire_entry = entries[wire_index]
                assert isinstance(wire_entry, ToolCallEntry)
                wire_id = (
                    wire_entry.provider_call_id.value
                    if wire_entry.provider_call_id.state is ObservationState.OBSERVED
                    else None
                )
                if provider_id is not None and wire_id is not None:
                    if wire_id != provider_id:
                        continue
                    candidates.append(wire_index)
                    continue
                if wire_entry.turn_id != reported_entry.turn_id:
                    continue
                if (
                    not _same_observed_text(reported_entry.server, wire_entry.server)
                    or not _same_observed_text(reported_entry.tool, wire_entry.tool)
                ):
                    continue
                candidates.append(wire_index)
            # An explicit ID may select one candidate directly; absent IDs
            # require uniqueness to avoid silently joining repeated calls.
            if provider_id is None and len(candidates) != 1:
                continue
            if len(candidates) != 1:
                continue
            wire_index = candidates[0]
            wire_entry = entries[wire_index]
            assert isinstance(wire_entry, ToolCallEntry)
            used.add(wire_index)
            replacements[reported_index] = _merge_tool_evidence(
                reported_entry, wire_entry
            )
            removed.add(wire_index)
        result: list[TraceEntry] = []
        for index, entry in enumerate(entries):
            if index in removed:
                continue
            result.append(replacements.get(index, entry))
        return tuple(result)

    @staticmethod
    def _coalesce_harness_chunks(
        entries: tuple[TraceEntry, ...],
    ) -> tuple[TraceEntry, ...]:
        """Coalesce adjacent provider-neutral message/reasoning chunks.

        Explicit IDs are authoritative.  Without an ID only immediately
        adjacent events in the same turn and role are combined; no provider
        message identity is guessed across interleaved events.
        """
        result: list[TraceEntry] = []
        for entry in entries:
            if result and isinstance(entry, MessageEntry):
                candidate_index: int | None = None
                if entry.message_id.state is ObservationState.OBSERVED:
                    for index in range(len(result) - 1, -1, -1):
                        candidate = result[index]
                        if (
                            isinstance(candidate, MessageEntry)
                            and candidate.turn_id == entry.turn_id
                            and candidate.role is entry.role
                            and candidate.message_id.state is ObservationState.OBSERVED
                            and candidate.message_id.value == entry.message_id.value
                        ):
                            candidate_index = index
                            break
                if candidate_index is None and isinstance(result[-1], MessageEntry):
                    previous = result[-1]
                    if (
                        previous.sequence_end + 1 == entry.sequence_start
                        and previous.turn_id == entry.turn_id
                        and previous.role is entry.role
                        and previous.message_id.state is ObservationState.NOT_EMITTED
                        and entry.message_id.state is ObservationState.NOT_EMITTED
                    ):
                        candidate_index = len(result) - 1
                if candidate_index is not None:
                    previous_message = cast(MessageEntry, result[candidate_index])
                    result[candidate_index] = previous_message.model_copy(
                        update={
                            "content": previous_message.content + entry.content,
                            "sequence_end": entry.sequence_end,
                            "timing": previous_message.timing.model_copy(
                                update={
                                    "end_offset_ms": entry.timing.end_offset_ms,
                                    "duration_ms": max(
                                        0.0,
                                        entry.timing.end_offset_ms
                                        - previous_message.timing.start_offset_ms,
                                    ),
                                    "finished_at": entry.timing.finished_at,
                                }
                            ),
                            "provenance": previous_message.provenance
                            + entry.provenance,
                        }
                    )
                    continue
            if result and isinstance(entry, ReasoningEntry):
                reasoning_index: int | None = None
                if entry.block_id.state is ObservationState.OBSERVED:
                    for index in range(len(result) - 1, -1, -1):
                        candidate = result[index]
                        if (
                            isinstance(candidate, ReasoningEntry)
                            and candidate.turn_id == entry.turn_id
                            and candidate.block_id.state is ObservationState.OBSERVED
                            and candidate.block_id.value == entry.block_id.value
                        ):
                            reasoning_index = index
                            break
                if reasoning_index is None and isinstance(result[-1], ReasoningEntry):
                    adjacent_candidate = result[-1]
                    if (
                        adjacent_candidate.sequence_end + 1 == entry.sequence_start
                        and adjacent_candidate.turn_id == entry.turn_id
                        and adjacent_candidate.block_id.state
                        is ObservationState.NOT_EMITTED
                        and entry.block_id.state is ObservationState.NOT_EMITTED
                    ):
                        reasoning_index = len(result) - 1
                previous_reasoning = (
                    cast(ReasoningEntry, result[reasoning_index])
                    if reasoning_index is not None
                    else None
                )
                if (
                    isinstance(previous_reasoning, ReasoningEntry)
                    and previous_reasoning.content.state is ObservationState.OBSERVED
                    and entry.content.state is ObservationState.OBSERVED
                ):
                    assert reasoning_index is not None
                    result[reasoning_index] = previous_reasoning.model_copy(
                        update={
                            "content": _observed(
                                cast(
                                    tuple[ContentBlock, ...],
                                    previous_reasoning.content.value,
                                )
                                + cast(tuple[ContentBlock, ...], entry.content.value)
                            ),
                            "sequence_end": entry.sequence_end,
                            "timing": previous_reasoning.timing.model_copy(
                                update={
                                    "end_offset_ms": entry.timing.end_offset_ms,
                                    "duration_ms": max(
                                        0.0,
                                        entry.timing.end_offset_ms
                                        - previous_reasoning.timing.start_offset_ms,
                                    ),
                                    "finished_at": entry.timing.finished_at,
                                }
                            ),
                            "provenance": previous_reasoning.provenance
                            + entry.provenance,
                        }
                    )
                    continue
            result.append(entry)
        return tuple(result)

    @staticmethod
    def _runtime(events: Sequence[Event]) -> RuntimeTraceInfo:
        opencode_events = tuple(
            event
            for event in events
            if event.provenance.origin is EventOrigin.HARNESS_REPORTED
            and event.provenance.source == "opencode"
        )
        if opencode_events:
            metadata: dict[str, Any] = {}
            usage_payload: Mapping[str, Any] | None = None
            for event in opencode_events:
                if event.kind is not EventKind.PROVIDER_EVENT:
                    continue
                category = event.payload.get("category")
                if category == "usage":
                    usage_payload = event.payload
                elif isinstance(category, str) and "data" in event.payload:
                    metadata[category] = event.payload["data"]
            def string_value(name: str) -> Observation[Any]:
                if name not in metadata:
                    return _not_emitted()
                value = metadata[name]
                if _capture_marker(value):
                    return _unavailable(ObservationReason.MALFORMED_SOURCE)
                return _identifier_observation(value)

            usage = _not_emitted()
            if usage_payload is not None:
                def usage_value(field: str, value: Observation[Any]) -> Observation[Any]:
                    return (
                        _unavailable(ObservationReason.MALFORMED_SOURCE)
                        if _capture_marker(metadata.get(f"usage_{field}_state"))
                        else value
                    )

                usage = _observed(
                    UsageValue(
                        input_tokens=usage_value("input_tokens", _int_observation(
                            usage_payload.get("input_tokens"),
                            present="input_tokens" in usage_payload,
                        )),
                        output_tokens=usage_value("output_tokens", _int_observation(
                            usage_payload.get("output_tokens"),
                            present="output_tokens" in usage_payload,
                        )),
                        reasoning_tokens=usage_value("reasoning_tokens", _int_observation(
                            usage_payload.get("reasoning_tokens"),
                            present="reasoning_tokens" in usage_payload,
                        )),
                        cache_creation_tokens=usage_value("cache_creation_tokens", _int_observation(
                            usage_payload.get("cache_creation_tokens"),
                            present="cache_creation_tokens" in usage_payload,
                        )),
                        cache_read_tokens=usage_value("cache_read_tokens", _int_observation(
                            usage_payload.get("cache_read_tokens"),
                            present="cache_read_tokens" in usage_payload,
                        )),
                        cache_write_tokens=usage_value("cache_write_tokens", _int_observation(
                            usage_payload.get("cache_write_tokens"),
                            present="cache_write_tokens" in usage_payload,
                        )),
                        total_tokens=usage_value("total_tokens", _int_observation(
                            usage_payload.get("total_tokens"),
                            present="total_tokens" in usage_payload,
                        )),
                        cost=usage_value("cost", _float_observation(
                            usage_payload.get("cost"),
                            present="cost" in usage_payload,
                        )),
                        currency=usage_value("currency", _string_observation(
                            usage_payload.get("currency"),
                            present="currency" in usage_payload,
                        )),
                    )
                )
            elif any(key.startswith("usage_") for key in metadata):
                usage = _unavailable(ObservationReason.MALFORMED_SOURCE)
            http_lifecycle: Observation[Any]
            http_fields: dict[str, Any] = {}
            for source, target in (
                ("http.method", "method"),
                ("http.route", "route"),
                ("http.content_type", "content_type"),
            ):
                value = metadata.get(source)
                if isinstance(value, str) and value:
                    http_fields[target] = value
            http_status = metadata.get("http.status_code")
            if isinstance(http_status, int) and not isinstance(http_status, bool):
                http_fields["status_code"] = http_status
            for source, target in (
                ("http.start_offset_ms", "start_offset_ms"),
                ("http.end_offset_ms", "end_offset_ms"),
                ("http.duration_ms", "duration_ms"),
            ):
                value = metadata.get(source)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0:
                    http_fields[target] = float(value)
            if "status_code" in http_fields:
                http_lifecycle = _observed(http_fields)
            else:
                http_lifecycle = _unavailable(ObservationReason.CAPTURE_FAILED)
            return OpenCodeTrace(
                session_id=string_value("session_id"),
                provider_id=string_value("provider"),
                model_id=string_value("model"),
                finish_reason=string_value("finish"),
                http_lifecycle=http_lifecycle,
                usage=usage,
            )
        claude_events = tuple(
            event
            for event in events
            if event.provenance.origin is EventOrigin.HARNESS_REPORTED
            and event.provenance.source == "claude-code"
        )
        if claude_events:
            claude_metadata: dict[str, Any] = {}
            claude_usage_payload: Mapping[str, Any] | None = None
            for event in claude_events:
                if event.kind is not EventKind.PROVIDER_EVENT:
                    continue
                category = event.payload.get("category")
                if category == "usage":
                    claude_usage_payload = event.payload
                elif isinstance(category, str) and "data" in event.payload:
                    claude_metadata[category] = event.payload["data"]

            def string_value(name: str) -> Observation[Any]:
                if name not in claude_metadata:
                    return _not_emitted()
                value = claude_metadata[name]
                return (
                    _unavailable(ObservationReason.MALFORMED_SOURCE)
                    if _capture_marker(value)
                    else _identifier_observation(value)
                )

            claude_usage: Observation[Any]
            if claude_usage_payload is None:
                claude_usage = (
                    _unavailable(ObservationReason.MALFORMED_SOURCE)
                    if any(
                        key.startswith("usage_") or key == "cost_usd"
                        for key in claude_metadata
                    )
                    else _not_emitted()
                )
            else:

                def usage_value(
                    field: str, value: Observation[Any]
                ) -> Observation[Any]:
                    return (
                        _unavailable(ObservationReason.MALFORMED_SOURCE)
                        if _capture_marker(claude_metadata.get(f"usage_{field}_state"))
                        else value
                    )

                claude_usage = _observed(
                    UsageValue(
                        input_tokens=usage_value(
                            "input_tokens",
                            _int_observation(
                                claude_usage_payload.get("input_tokens"),
                                present="input_tokens" in claude_usage_payload,
                            ),
                        ),
                        output_tokens=usage_value(
                            "output_tokens",
                            _int_observation(
                                claude_usage_payload.get("output_tokens"),
                                present="output_tokens" in claude_usage_payload,
                            ),
                        ),
                        reasoning_tokens=usage_value(
                            "reasoning_tokens",
                            _int_observation(
                                claude_usage_payload.get("reasoning_tokens"),
                                present="reasoning_tokens" in claude_usage_payload,
                            ),
                        ),
                        cache_creation_tokens=usage_value(
                            "cache_creation_tokens",
                            _int_observation(
                                claude_usage_payload.get("cache_creation_tokens"),
                                present="cache_creation_tokens" in claude_usage_payload,
                            ),
                        ),
                        cache_read_tokens=usage_value(
                            "cache_read_tokens",
                            _int_observation(
                                claude_usage_payload.get("cache_read_tokens"),
                                present="cache_read_tokens" in claude_usage_payload,
                            ),
                        ),
                        cache_write_tokens=usage_value(
                            "cache_write_tokens",
                            _int_observation(
                                claude_usage_payload.get("cache_write_tokens"),
                                present="cache_write_tokens" in claude_usage_payload,
                            ),
                        ),
                        total_tokens=usage_value(
                            "total_tokens",
                            _int_observation(
                                claude_usage_payload.get("total_tokens"),
                                present="total_tokens" in claude_usage_payload,
                            ),
                        ),
                        cost=usage_value(
                            "cost",
                            _float_observation(
                                claude_metadata.get("cost_usd"),
                                present="cost_usd" in claude_metadata,
                            ),
                        ),
                        currency=usage_value(
                            "currency",
                            _string_observation(
                                claude_usage_payload.get("currency"),
                                present="currency" in claude_usage_payload,
                            ),
                        ),
                    )
                )
            return ClaudeCodeTrace(
                session_id=string_value("session_id"),
                model_id=string_value("model"),
                result_subtype=string_value("result_subtype"),
                stop_reason=string_value("stop_reason"),
                service_tier=string_value("service_tier"),
                api_duration_ms=_float_observation(
                    claude_metadata.get("api_duration_ms"),
                    present="api_duration_ms" in claude_metadata,
                ),
                encrypted_reasoning=_observed(claude_metadata["encrypted_reasoning"])
                if isinstance(claude_metadata.get("encrypted_reasoning"), bool)
                else _not_emitted(),
                usage=claude_usage,
            )
        acp_events = tuple(
            event
            for event in events
            if event.provenance.origin is EventOrigin.HARNESS_REPORTED
            and event.provenance.source == "acp"
        )
        if acp_events:
            acp_metadata: dict[str, Any] = {}
            plan_state = False
            for event in acp_events:
                if event.kind is not EventKind.PROVIDER_EVENT:
                    continue
                category = event.payload.get("category")
                if category == "plan":
                    plan_state = True
                if isinstance(category, str) and "data" in event.payload:
                    acp_metadata[category] = event.payload["data"]

            def acp_value(name: str) -> Observation[Any]:
                if name not in acp_metadata:
                    return _not_emitted()
                value = acp_metadata[name]
                if _capture_marker(value):
                    return _unavailable(ObservationReason.MALFORMED_SOURCE)
                return _observed(value)

            current_mode = acp_value("current_mode")
            if current_mode.state is ObservationState.OBSERVED:
                current_mode = _string_observation(current_mode.value)
            return ACPTrace(
                session_id=_identifier_observation(
                    acp_metadata.get("session_id"),
                    present="session_id" in acp_metadata,
                ),
                protocol_version=_string_observation(
                    acp_metadata.get("protocol_version"),
                    present="protocol_version" in acp_metadata,
                ),
                agent_identity=acp_value("agent_identity"),
                available_modes=acp_value("available_modes"),
                current_mode=current_mode,
                config_options=acp_value("config_options"),
                selected_config=acp_value("selected_config"),
                plan_state_available=_observed(True) if plan_state else _not_emitted(),
                usage=Observation(
                    state=ObservationState.UNSUPPORTED,
                    reason=ObservationReason.HARNESS_UNSUPPORTED,
                ),
            )
        transport_event = next(
            (event for event in events if event.kind is EventKind.TRANSPORT_CONNECTED),
            None,
        )
        transport: Observation[Any]
        transport_present = (
            transport_event is not None
            and (
                "transport" in transport_event.payload
                or "configured_transport" in transport_event.payload
            )
        )
        raw_transport = (
            transport_event.payload.get(
                "transport", transport_event.payload.get("configured_transport")
            )
            if transport_event is not None
            and (
                "transport" in transport_event.payload
                or "configured_transport" in transport_event.payload
            )
            else None
        )
        if transport_present and isinstance(raw_transport, str):
            try:
                transport = _observed(TransportKind(raw_transport))
            except ValueError:
                transport = _unavailable(ObservationReason.MALFORMED_SOURCE)
        elif transport_present:
            transport = _unavailable(ObservationReason.MALFORMED_SOURCE)
        else:
            transport = _not_emitted()
        initialized = next(
            (event for event in events if event.kind is EventKind.MCP_INITIALIZED), None
        )
        initialization_payload: Mapping[str, Any] | None = None
        if initialized is not None and initialized.correlation is not None:
            for event in events:
                if (
                    event.kind is EventKind.MCP_REQUEST
                    and event.payload.get("method") == "initialize"
                    and event.connection_id == initialized.connection_id
                    and _initialization_request_matches(initialized, event)
                ):
                    candidates = _strict_response_candidates(event, events)
                    if len(candidates) != 1:
                        continue
                    response = candidates[0]
                    if response.kind is EventKind.MCP_RESPONSE and isinstance(
                        response.payload.get("result"), Mapping
                    ):
                        initialization_payload = response.payload["result"]
                    else:
                        initialization_payload = {_MALFORMED_INITIALIZATION: True}
                    break
        initialization = (
            _observed(_initialization_value(initialized, initialization_payload))
            if initialized is not None
            else _not_emitted()
        )
        protocol = (
            _observed("MCP")
            if any(event.kind.value.startswith("mcp.") for event in events)
            else _not_emitted()
        )
        return DirectTrace(
            transport=transport, protocol=protocol, initialization=initialization
        )

    @staticmethod
    def _summary(
        trace: TraceResult,
        events: Sequence[Event],
        timeline: Sequence[TraceEntry],
        outcome: ExecutionOutcome,
    ) -> TraceSummary:
        timing = _timing((events[0], events[-1]))
        tool_calls = tuple(item for item in timeline if isinstance(item, ToolCallEntry))
        successful = sum(
            item.tool_status is ToolCallStatus.SUCCESS for item in tool_calls
        )
        # The summary has no separate incomplete bucket: every non-successful
        # call is therefore counted as failed so the aggregate counts remain
        # consistent with ``tool_call_count`` and the typed timeline status.
        failed = sum(
            item.tool_status is not ToolCallStatus.SUCCESS for item in tool_calls
        )
        usage_entries = tuple(item for item in timeline if isinstance(item, UsageEntry))
        usage = _not_emitted()
        if usage_entries:
            latest = usage_entries[-1]
            usage = _observed(
                UsageValue(
                    input_tokens=latest.input_tokens,
                    output_tokens=latest.output_tokens,
                    reasoning_tokens=latest.reasoning_tokens,
                    cache_creation_tokens=latest.cache_creation_tokens,
                    cache_read_tokens=latest.cache_read_tokens,
                    cache_write_tokens=latest.cache_write_tokens,
                    total_tokens=latest.total_tokens,
                    cost=latest.cost,
                    currency=latest.currency,
                )
            )
        incomplete = len(tool_calls) - successful - failed
        if not tool_calls:
            health = ActivityHealth.NO_CALLS
        elif successful == len(tool_calls):
            health = ActivityHealth.ALL_SUCCEEDED
        elif successful == 0 and failed + incomplete == len(tool_calls):
            health = ActivityHealth.ALL_FAILED
        else:
            health = ActivityHealth.MIXED
        cleanup = TraceStatus.COMPLETED
        if "cleanup_failed" in trace.limitations:
            cleanup = TraceStatus.FAILED
        return TraceSummary(
            timing=timing,
            usage=usage,
            turn_count=len(
                {entry.turn_id for entry in timeline if entry.turn_id is not None}
            ),
            message_count=sum(isinstance(item, MessageEntry) for item in timeline),
            reasoning_count=sum(isinstance(item, ReasoningEntry) for item in timeline),
            tool_call_count=len(tool_calls),
            successful_tool_call_count=successful,
            failed_tool_call_count=failed,
            protocol_error_count=sum(
                (
                    isinstance(item, ProtocolEntry)
                    and item.status is TraceStatus.PROTOCOL_ERROR
                )
                or (
                    isinstance(item, ToolCallEntry)
                    and item.tool_status is ToolCallStatus.PROTOCOL_ERROR
                )
                for item in timeline
            ),
            activity_health=health,
            cleanup_status=cleanup,
        )


def _reported_call_id(event: Event) -> str | None:
    """Return a harness call identity without applying name-only matching."""
    if event.provenance.origin is not EventOrigin.HARNESS_REPORTED:
        return None
    value = event.payload.get("call_id")
    return value if isinstance(value, str) and value else None


def _same_reported_turn(first: Event, second: Event) -> bool:
    first_turn = first.payload.get("turn_sequence")
    second_turn = second.payload.get("turn_sequence")
    if isinstance(first_turn, int) and isinstance(second_turn, int):
        return first_turn == second_turn
    return True


def _correlation_key(event: Event) -> tuple[Any, ...] | None:
    correlation = event.correlation
    if correlation is None or correlation.jsonrpc_id is None:
        return None
    connection = (
        str(event.connection_id.root) if event.connection_id is not None else None
    )
    direction = correlation.direction
    opposite = {
        EventDirection.CLIENT_TO_SERVER: EventDirection.SERVER_TO_CLIENT,
        EventDirection.SDK_TO_HARNESS: EventDirection.HARNESS_TO_SDK,
        EventDirection.SERVER_TO_CLIENT: EventDirection.CLIENT_TO_SERVER,
        EventDirection.HARNESS_TO_SDK: EventDirection.SDK_TO_HARNESS,
        EventDirection.INTERNAL: EventDirection.INTERNAL,
    }[direction]
    request_direction = (
        opposite
        if event.kind
        in {
            EventKind.MCP_RESPONSE,
            EventKind.MCP_ERROR,
            EventKind.TOOL_RESULT_RECEIVED,
            EventKind.PERMISSION_RESPONSE,
            EventKind.SAMPLING_RESPONSE,
            EventKind.ELICITATION_RESPONSE,
        }
        else direction
    )
    return (
        connection,
        correlation.request_sequence,
        type(correlation.jsonrpc_id),
        correlation.jsonrpc_id,
        request_direction,
    )


def _compatible_pair(request: Event, response: Event) -> bool:
    if request.kind is EventKind.TOOL_CALL_REQUESTED or (
        request.kind is EventKind.MCP_REQUEST
        and request.payload.get("method") == "tools/call"
    ):
        return response.kind in {
            EventKind.TOOL_RESULT_RECEIVED,
            EventKind.MCP_RESPONSE,
            EventKind.MCP_ERROR,
            EventKind.MCP_CANCELLATION_COMPLETED,
        }
    if request.kind is EventKind.SAMPLING_REQUEST:
        return response.kind in {EventKind.SAMPLING_RESPONSE, EventKind.MCP_ERROR}
    if request.kind is EventKind.ELICITATION_REQUEST:
        return response.kind in {EventKind.ELICITATION_RESPONSE, EventKind.MCP_ERROR}
    if request.kind is EventKind.PERMISSION_REQUEST:
        return response.kind in {EventKind.PERMISSION_RESPONSE, EventKind.MCP_ERROR}
    return response.kind in {EventKind.MCP_RESPONSE, EventKind.MCP_ERROR}


def _fallback_pair(request: Event, response: Event) -> bool:
    """Check the narrow typed-ID fallback correlation contract."""
    request_correlation = request.correlation
    response_correlation = response.correlation
    if request_correlation is None or response_correlation is None:
        return False
    if (
        request_correlation.jsonrpc_id is None
        or response_correlation.jsonrpc_id is None
    ):
        return False
    if (
        type(request_correlation.jsonrpc_id)
        is not type(response_correlation.jsonrpc_id)
        or request_correlation.jsonrpc_id != response_correlation.jsonrpc_id
    ):
        return False
    if request.connection_id != response.connection_id:
        return False
    if (
        request_correlation.request_sequence is not None
        and response_correlation.request_sequence is not None
        and request_correlation.request_sequence
        != response_correlation.request_sequence
    ):
        return False
    if request.sequence >= response.sequence or not _compatible_pair(request, response):
        return False
    request_direction = request_correlation.direction
    response_direction = response_correlation.direction
    opposite = {
        EventDirection.CLIENT_TO_SERVER: EventDirection.SERVER_TO_CLIENT,
        EventDirection.SERVER_TO_CLIENT: EventDirection.CLIENT_TO_SERVER,
        EventDirection.SDK_TO_HARNESS: EventDirection.HARNESS_TO_SDK,
        EventDirection.HARNESS_TO_SDK: EventDirection.SDK_TO_HARNESS,
        EventDirection.INTERNAL: EventDirection.INTERNAL,
    }[request_direction]
    return response_direction is opposite


def _initialization_request_matches(
    initialized: Event, request: Event
) -> bool:
    """Require the initialized marker to describe its initialize request."""
    initialized_correlation = initialized.correlation
    request_correlation = request.correlation
    if initialized_correlation is None or request_correlation is None:
        return False
    if (
        initialized_correlation.request_sequence is None
        or request_correlation.request_sequence
        != initialized_correlation.request_sequence
    ):
        return False
    if initialized_correlation.jsonrpc_id is not None and (
        request_correlation.jsonrpc_id is None
        or type(initialized_correlation.jsonrpc_id)
        is not type(request_correlation.jsonrpc_id)
        or initialized_correlation.jsonrpc_id != request_correlation.jsonrpc_id
    ):
        return False
    opposite = {
        EventDirection.CLIENT_TO_SERVER: EventDirection.SERVER_TO_CLIENT,
        EventDirection.SERVER_TO_CLIENT: EventDirection.CLIENT_TO_SERVER,
        EventDirection.SDK_TO_HARNESS: EventDirection.HARNESS_TO_SDK,
        EventDirection.HARNESS_TO_SDK: EventDirection.SDK_TO_HARNESS,
        EventDirection.INTERNAL: EventDirection.INTERNAL,
    }[request_correlation.direction]
    return initialized_correlation.direction is opposite


def _strict_response_candidates(
    request: Event, events: Sequence[Event]
) -> tuple[Event, ...]:
    response_kinds = {
        EventKind.MCP_RESPONSE,
        EventKind.MCP_ERROR,
        EventKind.TOOL_RESULT_RECEIVED,
        EventKind.MCP_CANCELLATION_COMPLETED,
        EventKind.PERMISSION_RESPONSE,
        EventKind.SAMPLING_RESPONSE,
        EventKind.ELICITATION_RESPONSE,
    }
    key = _correlation_key(request)
    candidates = tuple(
        event
        for event in events
        if event.kind in response_kinds
        and event.sequence > request.sequence
        and _correlation_key(event) == key
        and _compatible_pair(request, event)
    )
    if candidates:
        return candidates
    fallback = tuple(
        event
        for event in events
        if event.kind in response_kinds and _fallback_pair(request, event)
    )
    return fallback


__all__ = ["TraceProjector"]
