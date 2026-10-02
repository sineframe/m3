"""Frozen observable behaviour of the trace projection.

Every corpus trace below is projected and its observable outputs (tool-call
fields, MRTR attempt state, elicitations and matcher outcomes for every
evidence mode) are compared with literal data in
``fixtures/trace_dedup_equivalence.json``.  The expectations were generated from
the projector before tool values stopped being stored several times in the
trace, so they prove that removing the duplicates changed no behaviour.
"""

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from test_mrtr_trace_projection import (
    _mrtr_protocol_trace,
    _mrtr_trace,
    _three_mrtr_rounds_reusing_state,
    _two_concurrent_mrtr_operations,
    _two_sequential_mrtr_operations,
    _without_mrtr_request_state,
)
from test_trace_projector import _trace

from m3.matchers import _UNAVAILABLE, expect
from m3.observability import (
    CorrelationState,
    Observation,
    ProtocolEntry,
    ToolCallEntry,
)
from m3.types import (
    Event,
    EventDirection,
    EventId,
    EventKind,
    EventOrigin,
    EventSource,
    RequestLink,
    TraceResult,
)

_EXPECTED = Path(__file__).parent.parent / "fixtures" / "trace_dedup_equivalence.json"
_EVIDENCE = ("wire", "reported", "any")
_PROMPT = ("prompts/get", {"name": "interactive-prompt", "arguments": {"q": "x"}})
_RESOURCE = ("resources/read", {"uri": "memory://interactive"})


def _rebuild(trace: TraceResult, events: Any) -> TraceResult:
    values = tuple(events)
    return trace.model_copy(
        update={"events": values, "highest_sequence": max(e.sequence for e in values)}
    )


def _resequence(trace: TraceResult, events: Any) -> TraceResult:
    values = [
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(events)
    ]
    return _rebuild(trace, values)


def _with_payload(event: Event, **payload: Any) -> Event:
    return event.model_copy(update={"payload": {**event.payload, **payload}})


def _with_params(event: Event, **params: Any) -> Event:
    return _with_payload(event, params={**event.payload["params"], **params})


def _link(jsonrpc_id: int, direction: EventDirection) -> RequestLink:
    return RequestLink(
        jsonrpc_id=jsonrpc_id, direction=direction, request_sequence=jsonrpc_id
    )


def _normalized(trace: TraceResult) -> TraceResult:
    """Give events unique sequences, stable ids and distinct monotonic offsets."""
    events = tuple(
        event.model_copy(
            update={
                "sequence": index,
                "event_id": EventId(f"event-{index}"),
                "monotonic_offset_ms": index * 7.5,
            }
        )
        for index, event in enumerate(trace.events)
    )
    return _rebuild(trace, events)


def _two_rounds(trace: TraceResult, method: str, retry_id: int) -> TraceResult:
    """Chain a second input_required round (URL elicitation) before completion."""
    retry, completion, terminal = trace.events[9:12]
    second_input = completion.model_copy(
        update={
            "payload": {
                "method": method,
                "result": {
                    "resultType": "input_required",
                    "inputRequests": {
                        "payment": {
                            "method": "elicitation/create",
                            "params": {
                                "mode": "url",
                                "message": "Authorize payment",
                                "url": "https://example.test/pay",
                            },
                        }
                    },
                    "requestState": "opaque-state-2",
                },
            }
        }
    )
    stable = {
        key: value
        for key, value in retry.payload["params"].items()
        if key not in {"requestState", "inputResponses"}
    }
    third_request = retry.model_copy(
        update={
            "correlation": _link(retry_id, EventDirection.CLIENT_TO_SERVER),
            "payload": {
                "method": method,
                "params": {
                    **stable,
                    "requestState": "opaque-state-2",
                    "inputResponses": {"payment": {"action": "accept"}},
                },
            },
        }
    )
    third_result = completion.model_copy(
        update={"correlation": _link(retry_id, EventDirection.SERVER_TO_CLIENT)}
    )
    return _resequence(
        trace,
        (*trace.events[:10], second_input, third_request, third_result, terminal),
    )


def _interleave(trace: TraceResult, request: Event, result: Event) -> TraceResult:
    return _resequence(trace, (*trace.events[:9], request, result, *trace.events[9:]))


def _unrelated_call(trace: TraceResult) -> TraceResult:
    request = trace.events[7].model_copy(
        update={
            "correlation": _link(99, EventDirection.CLIENT_TO_SERVER),
            "payload": {
                "method": "tools/call",
                "params": {"name": "other_tool", "arguments": {}},
            },
        }
    )
    result = trace.events[8].model_copy(
        update={
            "correlation": _link(99, EventDirection.SERVER_TO_CLIENT),
            "payload": {"result": {"content": [{"type": "text", "text": "other"}]}},
        }
    )
    return _interleave(trace, request, result)


def _duplicate_predecessor(trace: TraceResult) -> TraceResult:
    return _interleave(trace, trace.events[7], trace.events[8])


def _interleaved_protocol(trace: TraceResult, method: str, other: Any) -> TraceResult:
    request = trace.events[7].model_copy(
        update={
            "correlation": _link(41, EventDirection.CLIENT_TO_SERVER),
            "payload": {"method": method, "params": other},
        }
    )
    result = trace.events[8].model_copy(
        update={
            "correlation": _link(41, EventDirection.SERVER_TO_CLIENT),
            "kind": EventKind.MCP_RESPONSE,
            "payload": {"method": method, "result": {"content": []}},
        }
    )
    return _interleave(trace, request, result)


def _retry_with(trace: TraceResult, **params: Any) -> TraceResult:
    return _rebuild(
        trace,
        (
            *trace.events[:9],
            _with_params(trace.events[9], **params),
            *trace.events[10:],
        ),
    )


def _replace_retry_params(trace: TraceResult, params: Any) -> TraceResult:
    retry = trace.events[9]
    kept = {
        key: retry.payload["params"][key] for key in ("requestState", "inputResponses")
    }
    retry = _with_payload(retry, params={**params, **kept})
    return _rebuild(trace, (*trace.events[:9], retry, *trace.events[10:]))


def _reported(
    trace: TraceResult, source: str, request: dict[str, Any], result: dict[str, Any]
) -> TraceResult:
    """Append a harness-reported call after the wire events of ``trace``."""
    provenance = EventSource(origin=EventOrigin.HARNESS_REPORTED, source=source)
    sequence = max(event.sequence for event in trace.events[:-1]) + 1
    reported_request = trace.events[9].model_copy(
        update={
            "sequence": sequence,
            "correlation": None,
            "provenance": provenance,
            "payload": request,
        }
    )
    reported_result = trace.events[10].model_copy(
        update={
            "sequence": sequence + 1,
            "correlation": None,
            "provenance": provenance,
            "payload": result,
        }
    )
    terminal = trace.events[-1].model_copy(update={"sequence": sequence + 2})
    return _rebuild(
        trace, (*trace.events[:-1], reported_request, reported_result, terminal)
    )


_BOOKED = {
    "content": [{"type": "text", "text": "booked"}],
    "structuredContent": {"status": "booked"},
}


def _reported_book_shipment(
    trace: TraceResult, source: str, call_id: str, **request: Any
) -> TraceResult:
    return _reported(
        trace,
        source,
        {
            "method": "tools/call",
            "call_id": call_id,
            **request,
            "params": {"name": "book_shipment", "arguments": {"weight_kg": 2}},
        },
        {
            "method": "tools/call",
            "call_id": call_id,
            "result": _BOOKED,
            "tool_status": "success",
        },
    )


def _native_round_limit(trace: TraceResult) -> TraceResult:
    """Codex native failure reported after the wire stopped on input_required."""
    stuck = trace.events[10].model_copy(
        update={
            "payload": {
                "method": "tools/call",
                "result": {
                    "resultType": "input_required",
                    "inputRequests": {
                        "round-10": {
                            "method": "elicitation/create",
                            "params": {
                                "mode": "form",
                                "message": "round-10",
                                "requestedSchema": {"type": "object"},
                            },
                        }
                    },
                    "requestState": "10",
                },
            }
        }
    )
    trace = _rebuild(trace, (*trace.events[:10], stuck, *trace.events[11:]))
    return _reported(
        trace,
        "codex",
        {
            "method": "tools/call",
            "call_id": "codex-native-call-1",
            "server": "fixture",
            "tool": "book_shipment",
            "params": {"name": "book_shipment", "arguments": {"weight_kg": 2}},
        },
        {
            "method": "tools/call",
            "call_id": "codex-native-call-1",
            "tool_status": "tool_error",
            "result": None,
            "error": {"message": "input_required did not complete within 10 rounds"},
        },
    )


def _plain_reported(
    *,
    tool: str = "echo",
    call_id: str = "call-3",
    arguments: Any = None,
    result: dict[str, Any] | None = None,
    server: str | None = None,
    status: str = "success",
) -> TraceResult:
    """Correlate (or not) a harness report with the plain wire ``echo`` call."""
    trace = _trace()
    provenance = EventSource(origin=EventOrigin.HARNESS_REPORTED, source="harness")
    payload: dict[str, Any] = {
        "call_id": call_id,
        "tool": tool,
        "params": {
            "name": tool,
            "arguments": {"text": "hello"} if arguments is None else arguments,
        },
    }
    if server is not None:
        payload["server"] = server
    request = trace.events[7].model_copy(
        update={
            "sequence": 9,
            "correlation": None,
            "provenance": provenance,
            "payload": payload,
        }
    )
    response = trace.events[8].model_copy(
        update={
            "sequence": 10,
            "correlation": None,
            "provenance": provenance,
            "payload": {
                "call_id": call_id,
                "result": result or {"content": [{"type": "text", "text": "hello"}]},
                "tool_status": status,
            },
        }
    )
    shifted = tuple(
        event.model_copy(update={"sequence": event.sequence + 2})
        for event in trace.events[9:]
    )
    return _rebuild(trace, (*trace.events[:9], request, response, *shifted))


def _plain_variant(index: int, **update: Any) -> TraceResult:
    trace = _trace()
    event = trace.events[index].model_copy(update=update)
    return _rebuild(trace, (*trace.events[:index], event, *trace.events[index + 1 :]))


def _plain_unmatched() -> TraceResult:
    trace = _trace()
    return _rebuild(trace, (*trace.events[:8], *trace.events[9:]))


def _corpus() -> dict[str, Callable[[], TraceResult]]:
    def tool(**kwargs: Any) -> Callable[[], TraceResult]:
        return lambda: _mrtr_trace(**kwargs)

    def protocol(spec: tuple[str, Any], **kwargs: Any) -> Callable[[], TraceResult]:
        return lambda: _mrtr_protocol_trace(*spec, **kwargs)

    corpus: dict[str, Callable[[], TraceResult]] = {
        "plain_wire_only": _trace,
        "plain_unmatched_request": _plain_unmatched,
        "plain_mcp_error": lambda: _plain_variant(
            8,
            kind=EventKind.MCP_ERROR,
            payload={"method": "tools/call", "error": {"code": -32000, "message": "x"}},
        ),
        "plain_is_error_result": lambda: _plain_variant(
            8,
            payload={
                "method": "tools/call",
                "result": {"content": [], "isError": True},
            },
        ),
        "plain_malformed_result": lambda: _plain_variant(
            8, payload={"method": "tools/call", "result": "oops"}
        ),
        "plain_null_arguments": lambda: _plain_variant(
            7,
            payload={
                "method": "tools/call",
                "params": {"name": "echo", "arguments": None},
            },
        ),
        "reported_only": lambda: _plain_reported(tool="other", call_id="solo"),
        "correlated_equal": _plain_reported,
        "correlated_conflicting": lambda: _plain_reported(
            arguments={"text": "other"},
            result={"content": [{"type": "text", "text": "different"}]},
            server="reported-server",
            status="tool_error",
        ),
        "mrtr_tool": tool(),
        "mrtr_tool_unanswered": tool(unanswered=True),
        "mrtr_tool_ambiguous": tool(ambiguous=True),
        "mrtr_tool_codex_progress_tokens": tool(codex_progress_tokens=True),
        "mrtr_tool_codex_ambiguous": tool(codex_progress_tokens=True, ambiguous=True),
        "mrtr_tool_without_request_state": lambda: _without_mrtr_request_state(
            _mrtr_trace()
        ),
        "mrtr_tool_sequential_operations": lambda: _two_sequential_mrtr_operations(
            _mrtr_trace()
        ),
        "mrtr_tool_concurrent_operations": lambda: _two_concurrent_mrtr_operations(
            _mrtr_trace()
        ),
        "mrtr_tool_three_rounds_reused_state": lambda: _three_mrtr_rounds_reusing_state(
            _mrtr_trace()
        ),
        "mrtr_tool_two_rounds": lambda: _two_rounds(_mrtr_trace(), "tools/call", 6),
        "mrtr_tool_unrelated_call": lambda: _unrelated_call(_mrtr_trace()),
        "mrtr_tool_duplicate_predecessor": lambda: _duplicate_predecessor(
            _mrtr_trace()
        ),
        "mrtr_tool_empty_input_responses": lambda: _retry_with(
            _mrtr_trace(), inputResponses={}
        ),
        "mrtr_tool_changed_meta": lambda: _retry_with(
            _mrtr_trace(), _meta={"changed": True}
        ),
        "mrtr_tool_bool_vs_number": _bool_tool,
        "mrtr_tool_numeric_equivalent_arguments": lambda: _retry_with(
            _mrtr_trace(), arguments={"weight_kg": 2.0}
        ),
        "mrtr_tool_unstable_progress_token_without_codex": lambda: _meta_tokens(
            _mrtr_trace()
        ),
        "mrtr_tool_codex_changed_call_id": lambda: _codex_changed_call_id(),
        "mrtr_tool_pi_reported": lambda: _reported_book_shipment(
            _mrtr_trace(), "pi", "pi-call-1"
        ),
        "mrtr_tool_codex_reported": lambda: _reported_book_shipment(
            _mrtr_trace(codex_progress_tokens=True),
            "codex",
            "m3-call-m3-response-1",
            server="fixture",
            tool="book_shipment",
        ),
        "mrtr_tool_native_terminal_after_input_required": lambda: _native_round_limit(
            _mrtr_trace(codex_progress_tokens=True)
        ),
    }
    for label, spec in (("prompt", _PROMPT), ("resource", _RESOURCE)):
        corpus.update(
            {
                f"mrtr_{label}": protocol(spec),
                f"mrtr_{label}_unanswered": protocol(spec, unanswered=True),
                f"mrtr_{label}_ambiguous": protocol(spec, ambiguous=True),
                f"mrtr_{label}_without_request_state": (
                    lambda s=spec: _without_mrtr_request_state(_mrtr_protocol_trace(*s))
                ),
                f"mrtr_{label}_two_rounds": (
                    lambda s=spec: _two_rounds(_mrtr_protocol_trace(*s), s[0], 34)
                ),
                f"mrtr_{label}_changed_meta": (
                    lambda s=spec: _replace_retry_params(
                        _mrtr_protocol_trace(*s), {**s[1], "_meta": {"changed": True}}
                    )
                ),
                f"mrtr_{label}_changed_task": (
                    lambda s=spec: _replace_retry_params(
                        _mrtr_protocol_trace(*s), {**s[1], "task": {"id": "changed"}}
                    )
                ),
                f"mrtr_{label}_changed_target": (
                    lambda s=spec: _replace_retry_params(
                        _mrtr_protocol_trace(*s),
                        {key: "changed" for key in s[1] if key != "arguments"},
                    )
                ),
                f"mrtr_{label}_interleaved_operation": (
                    lambda s=spec: _interleaved_protocol(
                        _mrtr_protocol_trace(*s),
                        s[0],
                        {key: "other" for key in s[1] if key != "arguments"},
                    )
                ),
            }
        )
    corpus["mrtr_prompt_changed_arguments"] = lambda: _replace_retry_params(
        _mrtr_protocol_trace(*_PROMPT), {**_PROMPT[1], "arguments": {"q": "changed"}}
    )
    corpus["mrtr_prompt_bool_vs_number"] = lambda: _bool_prompt()
    return corpus


def _meta_tokens(trace: TraceResult) -> TraceResult:
    first = _with_params(trace.events[7], _meta={"progressToken": 1})
    retry = _with_params(trace.events[9], _meta={"progressToken": 2})
    return _rebuild(
        trace,
        (*trace.events[:7], first, trace.events[8], retry, *trace.events[10:]),
    )


def _codex_changed_call_id() -> TraceResult:
    trace = _mrtr_trace(codex_progress_tokens=True)
    retry = trace.events[9]
    meta = {**retry.payload["params"]["_meta"], "callId": "different-call"}
    return _rebuild(
        trace,
        (*trace.events[:9], _with_params(retry, _meta=meta), *trace.events[10:]),
    )


def _bool_tool() -> TraceResult:
    trace = _mrtr_trace()
    first = _with_params(trace.events[7], arguments={"requires_approval": True})
    trace = _rebuild(trace, (*trace.events[:7], first, *trace.events[8:]))
    return _retry_with(trace, arguments={"requires_approval": 1})


def _bool_prompt() -> TraceResult:
    method, _ = _PROMPT
    base = {"name": "interactive-prompt", "arguments": {"requires_approval": True}}
    trace = _mrtr_protocol_trace(method, base)
    return _replace_retry_params(
        trace,
        {"name": "interactive-prompt", "arguments": {"requires_approval": 1}},
    )


def _dump(observation: Observation[Any]) -> Any:
    return observation.model_dump(mode="json")


def _json_safe(value: Any) -> Any:
    if value is _UNAVAILABLE:
        return "<unavailable>"
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _attempt(attempt: Any) -> dict[str, Any]:
    return {
        "attempt_index": attempt.attempt_index,
        "jsonrpc_id": _dump(attempt.jsonrpc_id),
        "request_state": _dump(attempt.request_state),
        "continuation_state": _dump(attempt.continuation_state),
        "input_responses": _dump(attempt.input_responses),
        "input_required": attempt.input_required,
        "status": attempt.status.value,
        "sequence_start": attempt.sequence_start,
        "sequence_end": attempt.sequence_end,
    }


def _wire_observed(call: ToolCallEntry) -> bool:
    return call.wire.state.value == "observed"


def _wire_latency(call: ToolCallEntry) -> Any:
    return _dump(call.wire.value.latency_ms) if _wire_observed(call) else None


def _projections(view: Any, call_index: int, evidence: str) -> dict[str, Any]:
    """Matcher outcome of one call under one evidence mode."""
    call = view.tool_calls[call_index]
    seen: list[dict[str, Any]] = []

    def capture(projection: Any) -> bool:
        if projection["entry"] is not call:
            return False
        seen.append({k: _json_safe(v) for k, v in projection.items() if k != "entry"})
        return True

    def passes(**criteria: Any) -> bool:
        try:
            expect(view).to_have_tool_call(
                call.tool.value,
                evidence=evidence,
                predicate=lambda projection: projection["entry"] is call,
                **criteria,
            )
        except AssertionError:
            return False
        return True

    if not passes():
        return {"matched": False}
    expect(view).to_have_tool_call(
        call.tool.value, evidence=evidence, predicate=capture
    )
    projection = seen[0]
    outcome: dict[str, Any] = {"matched": True, "projection": projection}
    result = projection["result"]
    arguments = projection["arguments"]
    outcome["arguments_match"] = (
        passes(arguments=arguments) if arguments != "<unavailable>" else None
    )
    outcome["result_match"] = (
        passes(result=result) if result != "<unavailable>" else None
    )
    outcome["min_latency_match"] = passes(min_latency_ms=0.0)
    outcome["max_latency_match"] = passes(max_latency_ms=1e9)
    outcome["status_match"] = passes(status=projection["status"])
    return outcome


def _snapshot(trace: TraceResult) -> dict[str, Any]:
    view = _normalized(trace).view()
    tool_calls = []
    for index, call in enumerate(view.tool_calls):
        tool_calls.append(
            {
                "call_id": call.call_id,
                "tool": _dump(call.tool),
                "server": _dump(call.server),
                "arguments": _dump(call.arguments),
                "result": _dump(call.result),
                "server_latency_ms": _dump(call.server_latency_ms),
                "jsonrpc_id": _dump(call.jsonrpc_id),
                "correlation": call.correlation.value,
                "conflicts": [c.model_dump(mode="json") for c in call.conflicts],
                "tool_status": call.tool_status.value,
                "status": call.status.value,
                "sequence_start": call.sequence_start,
                "sequence_end": call.sequence_end,
                "reported": _dump(call.reported),
                "wire_observed": _wire_observed(call),
                "wire_latency_ms": _wire_latency(call),
                "attempts": [_attempt(a) for a in call.attempts],
                "matchers": {
                    evidence: _projections(view, index, evidence)
                    for evidence in _EVIDENCE
                },
            }
        )
    protocol = [
        {
            "entry_id": entry.entry_id,
            "method": _dump(entry.method),
            "operation_kind": entry.operation_kind,
            "operation_name": _dump(entry.operation_name),
            "request": _dump(entry.request),
            "response": _dump(entry.response),
            "status": entry.status.value,
            "sequence_start": entry.sequence_start,
            "sequence_end": entry.sequence_end,
            "attempts": [_attempt(a) for a in entry.attempts],
        }
        for entry in view.protocol
        if isinstance(entry, ProtocolEntry) and entry.operation_kind is not None
    ]
    return {
        "tool_calls": tool_calls,
        "protocol": protocol,
        "elicitations": [e.model_dump(mode="json") for e in view.elicitations],
    }


def _canonical(value: Any) -> Any:
    return json.loads(json.dumps(value))


def test_corpus_covers_every_evidence_shape() -> None:
    snapshots = {name: build() for name, build in _corpus().items()}
    views = {name: _normalized(trace).view() for name, trace in snapshots.items()}
    calls = [call for view in views.values() for call in view.tool_calls]
    assert {call.correlation for call in calls} == {
        CorrelationState.WIRE_ONLY,
        CorrelationState.CORRELATED,
        CorrelationState.REPORTED_ONLY,
    }
    assert any(len(call.attempts) > 1 for call in calls)
    assert any(call.conflicts for call in calls)
    assert any(
        len(entry.attempts) > 1
        for view in views.values()
        for entry in view.protocol
        if isinstance(entry, ProtocolEntry)
    )
    assert any(view.elicitations for view in views.values())


@pytest.mark.parametrize("name", sorted(_corpus()))
def test_projection_matches_frozen_expectation(name: str) -> None:
    expected = json.loads(_EXPECTED.read_text())
    assert name in expected
    assert _canonical(_snapshot(_corpus()[name]())) == expected[name]


def test_frozen_expectation_has_no_stale_entries() -> None:
    assert set(json.loads(_EXPECTED.read_text())) == set(_corpus())
