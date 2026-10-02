"""Tool values are stored once in the trace; MRTR state is read from events."""

import json
from typing import Any

import pytest
from test_mrtr_trace_projection import _mrtr_protocol_trace, _mrtr_trace
from test_trace_dedup_equivalence import (
    _PROMPT,
    _normalized,
    _plain_reported,
    _plain_unmatched,
    _plain_variant,
    _rebuild,
    _two_rounds,
    _with_params,
    _with_payload,
)

from m3.matchers import expect
from m3.observability import (
    CorrelationState,
    Observation,
    ObservationState,
    ProtocolEntry,
    ReportedToolCall,
    ToolCallEntry,
    TraceView,
)
from m3.types import (
    Event,
    EventKind,
    EventOrigin,
    EventSource,
    TraceResult,
)

_ARGUMENT = "argument-marker-0b7e91"
_RESULT = "result-marker-5d1c42"
_MESSAGE = "message-marker-93a6f0"
_HARNESS_ARGUMENT = "harness-argument-marker-2e8f13"
_HARNESS_RESULT = "harness-result-marker-7a40cd"
_MCP_RESULT = {"content": [{"kind": "text", "text": _RESULT}]}


def _count(view: TraceView, marker: str) -> int:
    return json.dumps(view.model_dump(mode="json")).count(marker)


def _with_marker(event: Event, old: str, new: str) -> Event:
    payload = json.loads(json.dumps(event.payload).replace(old, new))
    return event.model_copy(update={"payload": payload})


def _marked_plain_trace() -> TraceResult:
    trace = _plain_variant(
        7,
        payload={
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": _ARGUMENT}},
        },
    )
    response = trace.events[8].model_copy(
        update={
            "payload": {
                "method": "tools/call",
                "result": {"content": [{"type": "text", "text": _RESULT}]},
            }
        }
    )
    return _rebuild(trace, (*trace.events[:8], response, *trace.events[9:]))


def _correlated(
    *,
    wire_argument: str = _ARGUMENT,
    reported_argument: str | dict[str, Any] | None = None,
    reported_result: Any = None,
    wire_result: str = _RESULT,
) -> TraceResult:
    """Correlate a harness report with a wire echo call carrying marker values."""
    reported_arguments = (
        {"text": wire_argument} if reported_argument is None else reported_argument
    )
    trace = _plain_reported(
        arguments=reported_arguments,
        result={"content": []},
    )
    events = list(trace.events)
    request, response = events[7], events[8]
    events[7] = request.model_copy(
        update={
            "payload": {
                **request.model_dump(mode="json")["payload"],
                "params": {"name": "echo", "arguments": {"text": wire_argument}},
            }
        }
    )
    events[8] = response.model_copy(
        update={
            "payload": {
                "method": "tools/call",
                "result": {"content": [{"type": "text", "text": wire_result}]},
            }
        }
    )
    harness_response = events[10]
    events[10] = harness_response.model_copy(
        update={
            "payload": {
                **harness_response.payload,
                "result": (
                    {"content": [{"type": "text", "text": wire_result}]}
                    if reported_result is None
                    else reported_result
                ),
            }
        }
    )
    return _rebuild(trace, tuple(events))


def _marked_tool_chain() -> TraceResult:
    trace = _mrtr_trace()
    arguments = {"note": _ARGUMENT}
    first, required, retry, completion = trace.events[7:11]
    return _rebuild(
        trace,
        (
            *trace.events[:7],
            _with_params(first, arguments=arguments),
            _with_marker(required, "Enter the address", _MESSAGE),
            _with_params(retry, arguments=arguments),
            _with_payload(
                completion,
                result={"content": [{"type": "text", "text": _RESULT}]},
            ),
            *trace.events[11:],
        ),
    )


def _marked_prompt_chain() -> TraceResult:
    trace = _mrtr_protocol_trace(
        _PROMPT[0], {"name": "interactive-prompt", "arguments": {"q": _ARGUMENT}}
    )
    required, retry, completion = trace.events[8:11]
    return _rebuild(
        trace,
        (
            *trace.events[:8],
            _with_marker(required, "Approve access", _MESSAGE),
            retry,
            _with_payload(
                completion,
                result={"content": [{"type": "text", "text": _RESULT}]},
            ),
            *trace.events[11:],
        ),
    )


@pytest.mark.parametrize(
    ("build", "markers"),
    (
        (_marked_plain_trace, (_ARGUMENT, _RESULT)),
        (_marked_tool_chain, (_ARGUMENT, _RESULT, _MESSAGE)),
        (_marked_prompt_chain, (_ARGUMENT, _RESULT, _MESSAGE)),
    ),
)
def test_each_tool_value_appears_once_in_the_serialized_trace(
    build: Any, markers: tuple[str, ...]
) -> None:
    view = _normalized(build()).view()

    assert view.tool_calls or view.protocol
    for marker in markers:
        assert _count(view, marker) == 1, marker


def _timeline_dumps(view: TraceView, kind: str) -> list[dict[str, Any]]:
    return [
        item
        for item in view.model_dump(mode="json")["timeline"]
        if item["kind"] == kind and item.get("attempts")
    ]


def test_trace_entries_no_longer_carry_duplicate_wire_or_attempt_payloads() -> None:
    (call,) = _timeline_dumps(_normalized(_marked_tool_chain()).view(), "tool_call")
    (protocol,) = _timeline_dumps(
        _normalized(_marked_prompt_chain()).view(), "protocol"
    )

    assert "wire" not in call
    for attempt in (*call["attempts"], *protocol["attempts"]):
        assert not {"operation_params", "result", "raw_result"} & set(attempt)
    assert all("latency_ms" in attempt for attempt in call["attempts"])


def _codex_evidence(trace: TraceResult) -> TraceResult:
    codex = trace.events[-1].model_copy(
        update={
            "kind": EventKind.PROVIDER_EVENT,
            "provenance": EventSource(
                origin=EventOrigin.HARNESS_REPORTED, source="codex"
            ),
            "payload": {
                "harness_kind": "codex",
                "provider": "codex",
                "category": "finish_reason",
                "data": "completed",
            },
        }
    )
    return _rebuild(trace, (*trace.events[:-1], codex, trace.events[-1].model_copy()))


def _two_round_chain(operation: str, progress_tokens: bool) -> TraceResult:
    if operation == "tool":
        trace = _mrtr_trace(codex_progress_tokens=progress_tokens)
        return _two_rounds(trace, "tools/call", 6)
    method, params = _PROMPT
    if progress_tokens:
        params = {**params, "_meta": {"progressToken": 1}}
    trace = _mrtr_protocol_trace(method, params)
    if progress_tokens:
        retry = _with_params(trace.events[9], _meta={"progressToken": 2})
        trace = _rebuild(trace, (*trace.events[:9], retry, *trace.events[10:]))
        trace = _codex_evidence(trace)
    return _two_rounds(trace, method, 34)


@pytest.mark.parametrize("progress_tokens", (False, True))
@pytest.mark.parametrize("operation", ("tool", "prompt"))
def test_two_round_chains_coalesce_from_event_evidence(
    operation: str, progress_tokens: bool
) -> None:
    trace = _normalized(_two_round_chain(operation, progress_tokens))
    view = trace.view()

    if operation == "tool":
        (call,) = view.tool_calls
        assert isinstance(call, ToolCallEntry)
        keys = ["shipping_address", "payment"]
        ids = [3, 5, 6]
    else:
        (call,) = (e for e in view.protocol if e.operation_kind is not None)
        assert isinstance(call, ProtocolEntry)
        keys = ["approval", "payment"]
        ids = [31, 32, 34]
    assert [a.input_required for a in call.attempts] == [True, True, False]
    assert [a.jsonrpc_id.value for a in call.attempts] == ids
    assert [(e.round_index, e.request_key) for e in view.elicitations] == [
        (1, keys[0]),
        (2, keys[1]),
    ]
    assert [e.action for e in view.elicitations] == ["accept", "accept"]
    assert view.elicitations[1].input_responses.value == {
        "payment": {"action": "accept"}
    }
    assert [e.mode for e in view.elicitations] == ["form", "url"]


def test_prompt_progress_tokens_split_a_chain_only_without_codex_evidence() -> None:
    method, params = _PROMPT
    trace = _mrtr_protocol_trace(method, {**params, "_meta": {"progressToken": 1}})
    retry = _with_params(trace.events[9], _meta={"progressToken": 2})
    trace = _rebuild(trace, (*trace.events[:9], retry, *trace.events[10:]))

    split = [e for e in _normalized(trace).view().protocol if e.operation_kind]
    assert [len(call.attempts) for call in split] == [1, 1]
    merged = [
        e
        for e in _normalized(_codex_evidence(trace)).view().protocol
        if e.operation_kind
    ]
    assert [len(call.attempts) for call in merged] == [2]


def _offset(trace: TraceResult, sequence: int) -> float:
    return next(
        event.monotonic_offset_ms
        for event in trace.events
        if event.sequence == sequence
    )


def test_single_attempt_latency_is_the_request_response_span() -> None:
    trace = _normalized(_marked_plain_trace())
    (call,) = trace.view().tool_calls
    attempt = call.attempts[-1]

    expected = _offset(trace, attempt.sequence_end) - _offset(
        trace, attempt.sequence_start
    )
    assert expected > 0
    assert attempt.latency_ms.value == expected
    assert call.server_latency_ms.value == expected


def test_merged_attempts_each_keep_their_own_latency() -> None:
    trace = _normalized(_two_rounds(_mrtr_trace(), "tools/call", 6))
    (call,) = trace.view().tool_calls

    spans = [
        _offset(trace, a.sequence_end) - _offset(trace, a.sequence_start)
        for a in call.attempts
    ]
    assert [a.latency_ms.value for a in call.attempts] == spans
    assert call.server_latency_ms.value == _offset(
        trace, call.attempts[-1].sequence_end
    ) - _offset(trace, call.attempts[0].sequence_start)
    assert call.server_latency_ms.value > spans[-1]


def test_attempt_without_a_response_has_no_latency() -> None:
    (call,) = _normalized(_plain_unmatched()).view().tool_calls

    assert call.attempts[-1].latency_ms.state is ObservationState.NOT_EMITTED


def test_wire_result_is_unavailable_while_the_last_attempt_needs_input() -> None:
    view = _normalized(_mrtr_trace(unanswered=True)).view()
    (call,) = view.tool_calls
    assert call.attempts[-1].input_required

    expect(view).to_have_tool_call(
        "book_shipment", evidence="wire", status="incomplete"
    )
    expect(view).to_have_tool_call("book_shipment", evidence="wire", min_latency_ms=0)
    with pytest.raises(AssertionError):
        expect(view).to_have_tool_call(
            "book_shipment", evidence="wire", result={"content": []}
        )
    # The typed entry result is unchanged and still matches resolved evidence.
    expect(view).to_have_tool_call(
        "book_shipment", evidence="any", result={"content": []}
    )


def test_wire_result_matches_once_the_chain_completes() -> None:
    view = _normalized(_mrtr_trace()).view()

    expect(view).to_have_tool_call(
        "book_shipment",
        evidence="wire",
        result={
            "content": [{"type": "text", "text": "booked"}],
            "structuredContent": {"status": "booked"},
        },
    )


def test_wire_evidence_requires_wire_or_correlated_calls() -> None:
    view = _normalized(_plain_reported(tool="other", call_id="solo")).view()
    by_correlation = {call.correlation: call for call in view.tool_calls}
    assert set(by_correlation) == {
        CorrelationState.WIRE_ONLY,
        CorrelationState.REPORTED_ONLY,
    }

    expect(view).to_have_tool_call("echo", evidence="wire", count=1)
    expect(view).to_have_tool_call("other", evidence="reported", count=1)
    with pytest.raises(AssertionError):
        expect(view).to_have_tool_call("other", evidence="wire")


def _call(trace: TraceResult) -> tuple[TraceView, ToolCallEntry]:
    view = _normalized(trace).view()
    (call,) = (c for c in view.tool_calls if c.reported.state.value == "observed")
    return view, call


def _expect_reported_matches(view: TraceView, call: ToolCallEntry) -> None:
    """The reported-evidence matcher sees the full harness values."""
    arguments = call.reported_field("arguments").value
    result = call.reported_field("result").value
    expect(view).to_have_tool_call(
        "echo", evidence="reported", arguments=arguments, count=1
    )
    expect(view).to_have_tool_call("echo", evidence="reported", result=result, count=1)


def test_equal_correlated_values_are_stored_once_and_reconstructed() -> None:
    view, call = _call(_correlated(reported_result=_MCP_RESULT))

    assert call.correlation is CorrelationState.CORRELATED
    assert call.conflicts == ()
    assert call.reported.value is not None
    assert call.reported.value.same_as_call == ("arguments", "result")
    assert call.reported.value.arguments.state is ObservationState.NOT_EMITTED
    assert call.reported.value.result.state is ObservationState.NOT_EMITTED
    assert _count(view, _ARGUMENT) == 1
    assert _count(view, _RESULT) == 1
    assert call.reported_field("arguments").value == {"text": _ARGUMENT}
    assert call.reported_field("result").model_dump(mode="json")["value"] == _MCP_RESULT
    _expect_reported_matches(view, call)


def test_correlated_result_in_a_different_shape_is_kept() -> None:
    view, call = _call(_correlated())

    assert call.conflicts == ()
    assert call.reported.value is not None
    # The harness spelled the content block ``type``; the entry says ``kind``.
    assert call.reported.value.same_as_call == ("arguments",)
    assert call.reported.value.result.state is ObservationState.OBSERVED
    assert _count(view, _ARGUMENT) == 1
    assert _count(view, _RESULT) == 2
    _expect_reported_matches(view, call)


def test_conflicting_correlated_values_are_each_stored_once() -> None:
    view, call = _call(
        _correlated(
            reported_argument={"text": _HARNESS_ARGUMENT},
            reported_result={"content": [{"type": "text", "text": _HARNESS_RESULT}]},
        )
    )

    assert call.conflicts == ("arguments", "result")
    assert call.reported.value is not None
    assert call.reported.value.same_as_call == ()
    assert _count(view, _ARGUMENT) == 1
    assert _count(view, _HARNESS_ARGUMENT) == 1
    assert _count(view, _RESULT) == 1
    assert _count(view, _HARNESS_RESULT) == 1
    assert call.arguments.value == {"text": _ARGUMENT}
    assert call.reported.value.arguments.value == {"text": _HARNESS_ARGUMENT}
    _expect_reported_matches(view, call)


def test_conflicting_arguments_keep_an_equal_result_elided() -> None:
    view, call = _call(
        _correlated(
            reported_argument={"text": _HARNESS_ARGUMENT}, reported_result=_MCP_RESULT
        )
    )

    assert call.conflicts == ("arguments",)
    assert call.reported.value is not None
    assert call.reported.value.same_as_call == ("result",)
    assert _count(view, _ARGUMENT) == 1
    assert _count(view, _HARNESS_ARGUMENT) == 1
    assert _count(view, _RESULT) == 1
    _expect_reported_matches(view, call)


def test_string_harness_result_is_not_reconstructible_and_is_kept() -> None:
    view, call = _call(_correlated(reported_result=_HARNESS_RESULT))

    assert call.conflicts == ("result",)
    assert call.reported.value is not None
    assert call.reported.value.same_as_call == ("arguments",)
    assert call.reported_field("result").value == _HARNESS_RESULT
    assert _count(view, _HARNESS_RESULT) == 1
    assert _count(view, _RESULT) == 1
    _expect_reported_matches(view, call)


def test_number_spellings_are_not_elided() -> None:
    _view, call = _call(
        _correlated(
            wire_argument=_ARGUMENT, reported_argument={"text": _ARGUMENT, "n": 1.0}
        )
    )
    assert call.reported.value is not None
    assert call.reported.value.same_as_call == ()
    assert call.reported_field("arguments").value == {"text": _ARGUMENT, "n": 1.0}


def test_reported_only_call_stores_equal_values_once() -> None:
    trace = _plain_reported(
        tool="other",
        call_id="solo",
        arguments={"text": _ARGUMENT},
        result={"content": [{"kind": "text", "text": _RESULT}]},
    )
    view = _normalized(trace).view()
    (call,) = (
        c for c in view.tool_calls if c.correlation is CorrelationState.REPORTED_ONLY
    )

    assert call.reported.value is not None
    assert call.reported.value.same_as_call == ("arguments", "result")
    assert _count(view, _ARGUMENT) == 1
    assert _count(view, _RESULT) == 1
    expect(view).to_have_tool_call(
        "other",
        evidence="reported",
        arguments={"text": _ARGUMENT},
        result={"content": [{"kind": "text", "text": _RESULT}]},
    )


def test_same_as_call_must_name_unstored_fields_once() -> None:
    with pytest.raises(ValueError, match="must not be stored"):
        ReportedToolCall(
            arguments=Observation(state=ObservationState.OBSERVED, value={"a": 1}),
            same_as_call=("arguments",),
        )
    with pytest.raises(ValueError, match="sorted and unique"):
        ReportedToolCall(same_as_call=("arguments", "arguments"))
    with pytest.raises(ValueError, match="sorted and unique"):
        ReportedToolCall(same_as_call=("result", "arguments"))
