"""Synthetic wire-level coverage for modern multi-round protocol traces."""

import pytest
from test_trace_projector import _trace

from m3.observability import (
    ElicitationEntry,
    ObservationState,
    ProtocolEntry,
    ToolCallEntry,
    TraceStatus,
)
from m3.trace.projector import _json_equal
from m3.types import (
    EventDirection,
    EventId,
    EventKind,
    EventOrigin,
    EventSource,
    RequestLink,
)


def _mrtr_trace(*, ambiguous: bool = False, unanswered: bool = False):
    base = _trace()
    first_request = base.events[7]
    first_response = base.events[8]
    terminal = base.events[-1]

    def event(template, *, event_id: str, sequence: int, kind, correlation, payload):
        return template.model_copy(
            update={
                "event_id": EventId(event_id),
                "sequence": sequence,
                "kind": kind,
                "correlation": correlation,
                "payload": payload,
            }
        )

    request_params = {"name": "book_shipment", "arguments": {"weight_kg": 2}}
    first = event(
        first_request,
        event_id="mrtr-request-1",
        sequence=7,
        kind=EventKind.TOOL_CALL_REQUESTED,
        correlation=RequestLink(
            jsonrpc_id=3,
            direction=EventDirection.CLIENT_TO_SERVER,
            request_sequence=3,
        ),
        payload={"method": "tools/call", "params": request_params},
    ).model_copy(update={"server_binding": "fixture"})
    input_required = event(
        first_response,
        event_id="mrtr-result-1",
        sequence=8,
        kind=EventKind.TOOL_RESULT_RECEIVED,
        correlation=RequestLink(
            jsonrpc_id=3,
            direction=EventDirection.SERVER_TO_CLIENT,
            request_sequence=3,
        ),
        payload={
            "method": "tools/call",
            "result": {
                "resultType": "input_required",
                "inputRequests": {
                    "shipping_address": {
                        "method": "elicitation/create",
                        "params": {
                            "mode": "form",
                            "message": "Enter the address",
                            "requestedSchema": {"type": "object"},
                        },
                    }
                },
                "requestState": "opaque-state",
            },
        },
    ).model_copy(update={"server_binding": "fixture"})
    second = event(
        first_request,
        event_id="mrtr-request-2",
        sequence=11,
        kind=EventKind.TOOL_CALL_REQUESTED,
        correlation=RequestLink(
            jsonrpc_id=5,
            direction=EventDirection.CLIENT_TO_SERVER,
            request_sequence=5,
        ),
        payload={
            "method": "tools/call",
            "params": {
                **request_params,
                "requestState": "opaque-state",
                "inputResponses": {
                    "shipping_address": {
                        "action": "accept",
                        "content": {"city": "Pune"},
                    }
                },
            },
        },
    ).model_copy(update={"server_binding": "fixture"})
    second_response = event(
        first_response,
        event_id="mrtr-result-2",
        sequence=12,
        kind=EventKind.TOOL_RESULT_RECEIVED,
        correlation=RequestLink(
            jsonrpc_id=5,
            direction=EventDirection.SERVER_TO_CLIENT,
            request_sequence=5,
        ),
        payload={
            "method": "tools/call",
            "result": {
                "content": [{"type": "text", "text": "booked"}],
                "structuredContent": {"status": "booked"},
            },
        },
    ).model_copy(update={"server_binding": "fixture"})
    values = [*base.events[:7], first, input_required]
    if not unanswered:
        values.extend((second, second_response))
    if ambiguous:
        values.append(
            second.model_copy(
                update={
                    "event_id": EventId("mrtr-request-ambiguous"),
                    "sequence": 13,
                    "correlation": RequestLink(
                        jsonrpc_id=6,
                        direction=EventDirection.CLIENT_TO_SERVER,
                        request_sequence=6,
                    ),
                }
            )
        )
    values.append(terminal.model_copy(update={"sequence": len(values)}))
    return base.model_copy(
        update={"events": tuple(values), "highest_sequence": len(values) - 1}
    )


def test_mrtr_attempts_are_one_logical_tool_call() -> None:
    view = _mrtr_trace().view()

    assert len(view.tool_calls) == 1
    assert view.summary.tool_call_count == 1
    call = view.tool_calls[0]
    assert isinstance(call, ToolCallEntry)
    assert call.tool.value == "book_shipment"
    assert call.tool_status.value == "success"
    assert call.result.value is not None
    assert call.result.value.structured_content.value == {"status": "booked"}
    assert len(call.attempts) == 2
    assert call.attempts[0].input_required is True
    assert call.attempts[0].request_state.state is ObservationState.NOT_EMITTED
    assert call.attempts[0].continuation_state.value == "opaque-state"
    assert call.attempts[0].raw_result.value["requestState"] == "opaque-state"
    assert call.attempts[1].request_state.value == "opaque-state"
    assert call.attempts[1].input_responses.value == {
        "shipping_address": {
            "action": "accept",
            "content": {"city": "Pune"},
        }
    }
    assert type(view).model_validate(view.model_dump(mode="json")) == view


def test_reported_pi_result_keeps_wire_mrtr_attempts_as_one_logical_call() -> None:
    trace = _mrtr_trace()
    wire_retry = trace.events[9]
    wire_result = trace.events[10]
    reported_source = EventSource(origin=EventOrigin.HARNESS_REPORTED, source="pi")
    reported_request = wire_retry.model_copy(
        update={
            "event_id": EventId("mrtr-reported-request"),
            "sequence": 13,
            "correlation": None,
            "provenance": reported_source,
            "payload": {
                "method": "tools/call",
                "call_id": "pi-call-1",
                "params": {
                    "name": "book_shipment",
                    "arguments": {"weight_kg": 2},
                },
            },
        }
    )
    reported_result = wire_result.model_copy(
        update={
            "event_id": EventId("mrtr-reported-result"),
            "sequence": 14,
            "correlation": None,
            "provenance": reported_source,
            "payload": {
                "method": "tools/call",
                "call_id": "pi-call-1",
                "result": {
                    "content": [{"type": "text", "text": "booked"}],
                    "structuredContent": {"status": "booked"},
                },
                "tool_status": "success",
            },
        }
    )
    terminal = trace.events[-1].model_copy(update={"sequence": 15})
    view = trace.model_copy(
        update={
            "events": (
                *trace.events[:-1],
                reported_request,
                reported_result,
                terminal,
            ),
            "highest_sequence": 15,
        }
    ).view()

    calls = [call for call in view.tool_calls if call.tool.value == "book_shipment"]
    assert len(calls) == 1
    assert len(calls[0].attempts) == 2
    assert calls[0].result.value is not None
    assert calls[0].result.value.structured_content.value == {"status": "booked"}
    assert len(view.elicitations) == 1


def test_mrtr_elicitation_is_keyed_by_current_round_input_response() -> None:
    view = _mrtr_trace().view()

    assert len(view.elicitations) == 1
    entry = view.elicitations[0]
    assert isinstance(entry, ElicitationEntry)
    assert entry.request_key == "shipping_address"
    assert entry.mode == "form"
    assert entry.action == "accept"
    assert entry.server == "fixture"
    assert entry.operation_name == "book_shipment"
    assert entry.logical_operation_id == view.tool_calls[0].call_id
    assert entry.round_index == 1


@pytest.mark.parametrize(
    ("operation", "method", "params", "operation_kind", "operation_name"),
    (
        ("tool", None, {}, "tool", "book_shipment"),
        (
            "prompt",
            "prompts/get",
            {"name": "interactive-prompt"},
            "prompt",
            "interactive-prompt",
        ),
        (
            "resource",
            "resources/read",
            {"uri": "memory://interactive"},
            "resource",
            "memory://interactive",
        ),
    ),
)
def test_unanswered_mrtr_attempt_projects_pending_elicitation(
    operation: str,
    method: str | None,
    params: dict[str, object],
    operation_kind: str,
    operation_name: str,
) -> None:
    view = _mrtr_operation_trace(operation, method, params, unanswered=True).view()

    if operation_kind == "tool":
        call = view.tool_calls[0]
        logical_operation_id = call.call_id
        request_key = "shipping_address"
    else:
        call = next(
            entry
            for entry in view.protocol
            if isinstance(entry, ProtocolEntry)
            and entry.operation_kind == operation_kind
        )
        logical_operation_id = call.entry_id
        request_key = "approval"

    assert len(view.elicitations) == 1
    entry = view.elicitations[0]
    assert entry.entry_id == f"elicitation:{logical_operation_id}:1:{request_key}"
    assert entry.operation_kind == operation_kind
    assert entry.operation_name == operation_name
    assert entry.logical_operation_id == logical_operation_id
    assert entry.round_index == 1
    assert entry.request_key == request_key
    assert entry.status is TraceStatus.INCOMPLETE
    assert entry.action is None
    assert entry.input_responses.state is ObservationState.NOT_EMITTED
    assert entry.request_state.value in {"opaque-state", "opaque-protocol-state"}
    assert entry.sequence_start == entry.sequence_end == call.attempts[0].sequence_end


def test_mrtr_elicitation_distinguishes_observed_empty_responses() -> None:
    trace = _mrtr_trace()
    retry = trace.events[9]
    params = dict(retry.payload["params"])
    params["inputResponses"] = {}
    retry = retry.model_copy(update={"payload": {**retry.payload, "params": params}})
    trace = trace.model_copy(
        update={"events": (*trace.events[:9], retry, *trace.events[10:])}
    )

    entry = trace.view().elicitations[0]
    assert entry.action is None
    assert entry.input_responses.state is ObservationState.OBSERVED
    assert entry.input_responses.value == {}


def test_form_elicitation_with_omitted_mode_is_projected() -> None:
    trace = _mrtr_trace()
    required = trace.events[8]
    result = required.payload["result"].copy()
    requests = result["inputRequests"].copy()
    request = requests["shipping_address"].copy()
    params = request["params"].copy()
    params.pop("mode")
    requests["shipping_address"] = {**request, "params": params}
    result["inputRequests"] = requests
    required = required.model_copy(
        update={"payload": {**required.payload, "result": result}}
    )
    view = trace.model_copy(
        update={"events": (*trace.events[:8], required, *trace.events[9:])}
    ).view()

    assert len(view.elicitations) == 1
    assert view.elicitations[0].mode == "form"


def test_ambiguous_mrtr_continuation_is_not_merged() -> None:
    view = _mrtr_trace(ambiguous=True).view()

    assert len(view.tool_calls) == 3
    assert all(len(call.attempts) == 1 for call in view.tool_calls)


def _mrtr_operation_trace(
    operation: str,
    method: str | None,
    params: dict[str, object],
    *,
    unanswered: bool = False,
):
    if operation == "tool":
        return _mrtr_trace(unanswered=unanswered)
    assert method is not None
    return _mrtr_protocol_trace(method, params, unanswered=unanswered)


def _mrtr_operation_calls(view, operation: str, method: str | None):
    if operation == "tool":
        return [call for call in view.tool_calls if call.tool.value == "book_shipment"]
    assert method is not None
    return [
        call
        for call in view.protocol
        if call.method.state is ObservationState.OBSERVED
        and call.method.value == method
    ]


def _without_mrtr_request_state(trace):
    required = trace.events[8]
    result = required.payload["result"].copy()
    result.pop("requestState", None)
    required = required.model_copy(
        update={"payload": {**required.payload, "result": result}}
    )
    retry = trace.events[9]
    params = retry.payload["params"].copy()
    params.pop("requestState", None)
    retry = retry.model_copy(update={"payload": {**retry.payload, "params": params}})
    return trace.model_copy(
        update={"events": (*trace.events[:8], required, retry, *trace.events[10:])}
    )


def _two_sequential_mrtr_operations(trace):
    first_request, first_result, first_retry, first_completion = trace.events[7:11]
    second_request_id = 51
    second_retry_id = 52

    def clone(event, event_id: str, jsonrpc_id: int):
        direction = event.correlation.direction
        return event.model_copy(
            update={
                "event_id": EventId(event_id),
                "correlation": RequestLink(
                    jsonrpc_id=jsonrpc_id,
                    direction=direction,
                    request_sequence=jsonrpc_id,
                ),
            }
        )

    second = (
        clone(first_request, "mrtr-second-request", second_request_id),
        clone(first_result, "mrtr-second-input-required", second_request_id),
        clone(first_retry, "mrtr-second-retry", second_retry_id),
        clone(first_completion, "mrtr-second-completion", second_retry_id),
    )
    values = [
        *trace.events[:7],
        first_request,
        first_result,
        first_retry,
        first_completion,
        *second,
        trace.events[-1],
    ]
    values = [
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(values)
    ]
    return trace.model_copy(
        update={"events": tuple(values), "highest_sequence": len(values) - 1}
    )


def _two_concurrent_mrtr_operations(trace):
    first_request, first_result, first_retry, first_completion = trace.events[7:11]
    second_request_id = 61
    second_retry_id = 62

    def clone(event, event_id: str, jsonrpc_id: int):
        return event.model_copy(
            update={
                "event_id": EventId(event_id),
                "correlation": RequestLink(
                    jsonrpc_id=jsonrpc_id,
                    direction=event.correlation.direction,
                    request_sequence=jsonrpc_id,
                ),
            }
        )

    second_request = clone(first_request, "mrtr-concurrent-request", second_request_id)
    second_result = clone(
        first_result, "mrtr-concurrent-input-required", second_request_id
    )
    second_retry = clone(first_retry, "mrtr-concurrent-retry", second_retry_id)
    second_completion = clone(
        first_completion, "mrtr-concurrent-completion", second_retry_id
    )
    values = [
        *trace.events[:7],
        first_request,
        first_result,
        second_request,
        second_result,
        first_retry,
        first_completion,
        second_retry,
        second_completion,
        trace.events[-1],
    ]
    values = [
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(values)
    ]
    return trace.model_copy(
        update={"events": tuple(values), "highest_sequence": len(values) - 1}
    )


def _three_mrtr_rounds_reusing_state(trace):
    _, first_result, retry, second_result = trace.events[7:11]
    request_state = first_result.payload["result"]["requestState"]
    second_input_required = second_result.model_copy(
        update={
            "payload": {
                **(
                    {"method": second_result.payload["method"]}
                    if "method" in second_result.payload
                    else {}
                ),
                "result": {
                    "resultType": "input_required",
                    "inputRequests": {
                        "next_input": {
                            "method": "elicitation/create",
                            "params": {
                                "mode": "form",
                                "message": "Confirm the next step",
                                "requestedSchema": {"type": "object"},
                            },
                        }
                    },
                    "requestState": request_state,
                },
            }
        }
    )
    retry_params = retry.payload["params"].copy()
    retry_params["inputResponses"] = {"next_input": {"action": "accept"}}
    third_retry = retry.model_copy(
        update={
            "event_id": EventId("mrtr-third-retry"),
            "correlation": RequestLink(
                jsonrpc_id=71,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=71,
            ),
            "payload": {**retry.payload, "params": retry_params},
        }
    )
    completion = second_result.model_copy(
        update={
            "event_id": EventId("mrtr-third-completion"),
            "correlation": RequestLink(
                jsonrpc_id=71,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=71,
            ),
            "payload": {
                **(
                    {"method": second_result.payload["method"]}
                    if "method" in second_result.payload
                    else {}
                ),
                "result": {"content": [{"type": "text", "text": "done"}]},
            },
        }
    )
    values = [
        *trace.events[:10],
        second_input_required,
        third_retry,
        completion,
        trace.events[-1],
    ]
    values = [
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(values)
    ]
    return trace.model_copy(
        update={"events": tuple(values), "highest_sequence": len(values) - 1}
    )


@pytest.mark.parametrize(
    ("operation", "method", "params"),
    (
        ("tool", None, {}),
        (
            "protocol",
            "prompts/get",
            {"name": "interactive-prompt", "arguments": {"q": "x"}},
        ),
        ("protocol", "resources/read", {"uri": "memory://interactive"}),
    ),
)
def test_mrtr_retry_without_request_state_is_coalesced(
    operation: str, method: str | None, params: dict[str, object]
) -> None:
    trace = _without_mrtr_request_state(
        _mrtr_operation_trace(operation, method, params)
    )

    view = trace.view()
    calls = _mrtr_operation_calls(view, operation, method)
    assert len(calls) == 1
    assert len(calls[0].attempts) == 2
    assert calls[0].attempts[0].continuation_state.state is ObservationState.NOT_EMITTED
    assert calls[0].attempts[1].request_state.state is ObservationState.NOT_EMITTED
    assert len(view.elicitations) == 1


@pytest.mark.parametrize(
    ("operation", "method", "params"),
    (
        ("tool", None, {}),
        (
            "protocol",
            "prompts/get",
            {"name": "interactive-prompt", "arguments": {"q": "x"}},
        ),
        ("protocol", "resources/read", {"uri": "memory://interactive"}),
    ),
)
@pytest.mark.parametrize("omit_state", (False, True))
def test_sequential_mrtr_calls_can_reuse_request_state(
    operation: str,
    method: str | None,
    params: dict[str, object],
    omit_state: bool,
) -> None:
    trace = _mrtr_operation_trace(operation, method, params)
    if omit_state:
        trace = _without_mrtr_request_state(trace)
    trace = _two_sequential_mrtr_operations(trace)

    view = trace.view()
    calls = _mrtr_operation_calls(view, operation, method)
    assert len(calls) == 2
    assert [len(call.attempts) for call in calls] == [2, 2]
    assert len(view.elicitations) == 2


@pytest.mark.parametrize(
    ("operation", "method", "params"),
    (
        ("tool", None, {}),
        (
            "protocol",
            "prompts/get",
            {"name": "interactive-prompt", "arguments": {"q": "x"}},
        ),
        ("protocol", "resources/read", {"uri": "memory://interactive"}),
    ),
)
@pytest.mark.parametrize("omit_state", (False, True))
def test_concurrent_mrtr_calls_with_reused_state_remain_unmerged(
    operation: str,
    method: str | None,
    params: dict[str, object],
    omit_state: bool,
) -> None:
    trace = _mrtr_operation_trace(operation, method, params)
    if omit_state:
        trace = _without_mrtr_request_state(trace)
    trace = _two_concurrent_mrtr_operations(trace)

    view = trace.view()
    calls = _mrtr_operation_calls(view, operation, method)
    assert len(calls) == 4
    assert all(len(call.attempts) == 1 for call in calls)
    assert len(view.elicitations) == 2
    assert len({entry.logical_operation_id for entry in view.elicitations}) == 2
    assert all(entry.status is TraceStatus.INCOMPLETE for entry in view.elicitations)
    assert all(entry.action is None for entry in view.elicitations)
    assert all(
        entry.input_responses.state is ObservationState.NOT_EMITTED
        for entry in view.elicitations
    )


@pytest.mark.parametrize(
    ("operation", "method", "params"),
    (
        ("tool", None, {}),
        (
            "protocol",
            "prompts/get",
            {"name": "interactive-prompt", "arguments": {"q": "x"}},
        ),
        ("protocol", "resources/read", {"uri": "memory://interactive"}),
    ),
)
def test_multiple_mrtr_rounds_can_reuse_the_same_request_state(
    operation: str, method: str | None, params: dict[str, object]
) -> None:
    trace = _three_mrtr_rounds_reusing_state(
        _mrtr_operation_trace(operation, method, params)
    )

    view = trace.view()
    calls = _mrtr_operation_calls(view, operation, method)
    assert len(calls) == 1
    assert len(calls[0].attempts) == 3
    assert [entry.round_index for entry in view.elicitations] == [1, 2]


def test_unrelated_tool_call_is_not_absorbed_by_mrtr_chain() -> None:
    trace = _mrtr_trace()
    first = trace.events[7]
    unrelated = first.model_copy(
        update={
            "event_id": EventId("mrtr-unrelated-request"),
            "sequence": 9,
            "correlation": RequestLink(
                jsonrpc_id=99,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=99,
            ),
            "payload": {
                "method": "tools/call",
                "params": {"name": "other_tool", "arguments": {}},
            },
        }
    )
    unrelated_result = trace.events[8].model_copy(
        update={
            "event_id": EventId("mrtr-unrelated-result"),
            "sequence": 10,
            "correlation": RequestLink(
                jsonrpc_id=99,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=99,
            ),
            "payload": {"result": {"content": [{"type": "text", "text": "other"}]}},
        }
    )
    values = list(trace.events)
    values[9:9] = [unrelated, unrelated_result]
    values = [
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(values)
    ]
    rebuilt = trace.model_copy(
        update={"events": tuple(values), "highest_sequence": len(values) - 1}
    )

    calls = rebuilt.view().tool_calls
    assert len(calls) == 2
    assert sorted(len(call.attempts) for call in calls) == [1, 2]


def test_changed_operation_metadata_is_not_absorbed_by_mrtr_chain() -> None:
    trace = _mrtr_trace()
    second = trace.events[9].model_copy(
        update={
            "payload": {
                **trace.events[9].payload,
                "params": {
                    **trace.events[9].payload["params"],
                    "_meta": {"changed": True},
                },
            }
        }
    )
    values = (*trace.events[:9], second, *trace.events[10:])
    rebuilt = trace.model_copy(update={"events": values})

    calls = rebuilt.view().tool_calls
    assert len(calls) == 2
    assert all(len(call.attempts) == 1 for call in calls)


def test_tool_mrtr_correlation_does_not_coerce_bool_to_number() -> None:
    trace = _mrtr_trace()
    first = trace.events[7].model_copy(
        update={
            "payload": {
                **trace.events[7].payload,
                "params": {
                    "name": "book_shipment",
                    "arguments": {"requires_approval": True},
                },
            }
        }
    )
    retry = trace.events[9].model_copy(
        update={
            "payload": {
                **trace.events[9].payload,
                "params": {
                    **trace.events[9].payload["params"],
                    "arguments": {"requires_approval": 1},
                },
            }
        }
    )
    rebuilt = trace.model_copy(
        update={
            "events": (
                *trace.events[:7],
                first,
                trace.events[8],
                retry,
                *trace.events[10:],
            )
        }
    )

    calls = rebuilt.view().tool_calls
    assert len(calls) == 2
    assert all(len(call.attempts) == 1 for call in calls)


@pytest.mark.parametrize(
    ("method", "params", "changed_params"),
    (
        (
            "prompts/get",
            {"name": "interactive-prompt", "arguments": {"requires_approval": True}},
            {"name": "interactive-prompt", "arguments": {"requires_approval": 1}},
        ),
        (
            "resources/read",
            {"uri": "memory://interactive", "_meta": {"requires_approval": True}},
            {"uri": "memory://interactive", "_meta": {"requires_approval": 1}},
        ),
    ),
)
def test_prompt_and_resource_mrtr_correlation_does_not_coerce_bool_to_number(
    method: str,
    params: dict[str, object],
    changed_params: dict[str, object],
) -> None:
    trace = _mrtr_protocol_trace(method, params)
    retry = trace.events[9].model_copy(
        update={
            "payload": {
                **trace.events[9].payload,
                "params": {
                    **changed_params,
                    "requestState": "opaque-protocol-state",
                    "inputResponses": {
                        "approval": {"action": "accept", "content": {"approved": True}}
                    },
                },
            }
        }
    )
    rebuilt = trace.model_copy(
        update={"events": (*trace.events[:9], retry, *trace.events[10:])}
    )

    calls = [
        entry
        for entry in rebuilt.view().protocol
        if entry.method.state is ObservationState.OBSERVED
        and entry.method.value == method
    ]
    assert len(calls) == 2
    assert all(len(call.attempts) == 1 for call in calls)


@pytest.mark.parametrize(
    ("left", "right", "equal"),
    (
        (1, 1.0, True),
        (-0.0, 0, True),
        (True, 1, False),
        (False, 0, False),
        ({"a": [1, {"b": 2}]}, {"a": [1.0, {"b": 2}]}, True),
        ({"a": 1, "b": 2}, {"b": 2.0, "a": 1.0}, True),
        ([1, 2], [2, 1], False),
        (float("nan"), float("nan"), False),
        (float("inf"), float("inf"), False),
    ),
)
def test_json_equal_uses_canonical_json_semantics(
    left: object, right: object, equal: bool
) -> None:
    assert _json_equal(left, right) is equal


def test_two_possible_predecessors_leave_retry_unmerged() -> None:
    trace = _mrtr_trace()
    duplicate_request = trace.events[7].model_copy(
        update={"event_id": EventId("mrtr-duplicate-request")}
    )
    duplicate_result = trace.events[8].model_copy(
        update={"event_id": EventId("mrtr-duplicate-result")}
    )
    values = [*trace.events[:9], duplicate_request, duplicate_result, *trace.events[9:]]
    values = [
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(values)
    ]
    rebuilt = trace.model_copy(
        update={"events": tuple(values), "highest_sequence": len(values) - 1}
    )

    calls = rebuilt.view().tool_calls
    assert len(calls) == 3
    assert all(len(call.attempts) == 1 for call in calls)


def test_two_mrtr_rounds_keep_attempts_and_responses_scoped() -> None:
    trace = _mrtr_trace()
    second_request = trace.events[9]
    second_result = trace.events[10]
    terminal = trace.events[11]
    second_input_required = second_result.model_copy(
        update={
            "event_id": EventId("mrtr-result-2-input"),
            "payload": {
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
                }
            },
        }
    )
    third_request = second_request.model_copy(
        update={
            "event_id": EventId("mrtr-request-3"),
            "correlation": RequestLink(
                jsonrpc_id=6,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=6,
            ),
            "payload": {
                "method": "tools/call",
                "params": {
                    "name": "book_shipment",
                    "arguments": {"weight_kg": 2},
                    "requestState": "opaque-state-2",
                    "inputResponses": {"payment": {"action": "accept"}},
                },
            },
        }
    )
    third_result = second_result.model_copy(
        update={
            "event_id": EventId("mrtr-result-3"),
            "correlation": RequestLink(
                jsonrpc_id=6,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=6,
            ),
            "payload": {
                "result": {
                    "content": [{"type": "text", "text": "booked"}],
                    "structuredContent": {"status": "booked"},
                }
            },
        }
    )
    values = [
        *trace.events[:10],
        second_input_required,
        third_request,
        third_result,
        terminal.model_copy(update={"sequence": 13}),
    ]
    values = [
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(values)
    ]
    rebuilt = trace.model_copy(
        update={"events": tuple(values), "highest_sequence": len(values) - 1}
    )

    view = rebuilt.view()
    assert len(view.tool_calls) == 1
    call = view.tool_calls[0]
    assert len(call.attempts) == 3
    assert [attempt.jsonrpc_id.value for attempt in call.attempts] == [3, 5, 6]
    assert call.attempts[1].input_responses.value == {
        "shipping_address": {
            "action": "accept",
            "content": {"city": "Pune"},
        }
    }
    assert call.attempts[2].input_responses.value == {"payment": {"action": "accept"}}
    assert [(item.round_index, item.request_key) for item in view.elicitations] == [
        (1, "shipping_address"),
        (2, "payment"),
    ]
    assert view.elicitations[0].input_responses.value == {
        "shipping_address": {
            "action": "accept",
            "content": {"city": "Pune"},
        }
    }
    assert view.elicitations[1].input_responses.value == {
        "payment": {"action": "accept"}
    }


def _mrtr_protocol_trace(
    method: str,
    params: dict[str, object],
    *,
    ambiguous: bool = False,
    unanswered: bool = False,
):
    base = _trace()
    first_request = base.events[7]
    first_response = base.events[8]
    terminal = base.events[-1]

    def event(template, *, event_id: str, sequence: int, kind, correlation, payload):
        return template.model_copy(
            update={
                "event_id": EventId(event_id),
                "sequence": sequence,
                "kind": kind,
                "correlation": correlation,
                "payload": payload,
                "server_binding": "fixture",
            }
        )

    first = event(
        first_request,
        event_id=f"{method}-request-1",
        sequence=7,
        kind=EventKind.MCP_REQUEST,
        correlation=RequestLink(
            jsonrpc_id=31,
            direction=EventDirection.CLIENT_TO_SERVER,
            request_sequence=31,
        ),
        payload={"method": method, "params": params},
    )
    input_required = event(
        first_response,
        event_id=f"{method}-result-1",
        sequence=8,
        kind=EventKind.MCP_RESPONSE,
        correlation=RequestLink(
            jsonrpc_id=31,
            direction=EventDirection.SERVER_TO_CLIENT,
            request_sequence=31,
        ),
        payload={
            "method": method,
            "result": {
                "resultType": "input_required",
                "inputRequests": {
                    "approval": {
                        "method": "elicitation/create",
                        "params": {
                            "mode": "form",
                            "message": "Approve access",
                            "requestedSchema": {"type": "object"},
                        },
                    }
                },
                "requestState": "opaque-protocol-state",
            },
        },
    )
    retry = event(
        first_request,
        event_id=f"{method}-request-2",
        sequence=11,
        kind=EventKind.MCP_REQUEST,
        correlation=RequestLink(
            jsonrpc_id=32,
            direction=EventDirection.CLIENT_TO_SERVER,
            request_sequence=32,
        ),
        payload={
            "method": method,
            "params": {
                **params,
                "requestState": "opaque-protocol-state",
                "inputResponses": {
                    "approval": {
                        "action": "accept",
                        "content": {"approved": True},
                    }
                },
            },
        },
    )
    completed = event(
        first_response,
        event_id=f"{method}-result-2",
        sequence=12,
        kind=EventKind.MCP_RESPONSE,
        correlation=RequestLink(
            jsonrpc_id=32,
            direction=EventDirection.SERVER_TO_CLIENT,
            request_sequence=32,
        ),
        payload={
            "method": method,
            "result": {"content": [{"type": "text", "text": "done"}]},
        },
    )
    values = [*base.events[:7], first, input_required]
    if not unanswered:
        values.extend((retry, completed))
    if ambiguous:
        values.append(
            retry.model_copy(
                update={
                    "event_id": EventId(f"{method}-request-ambiguous"),
                    "sequence": 13,
                    "correlation": RequestLink(
                        jsonrpc_id=33,
                        direction=EventDirection.CLIENT_TO_SERVER,
                        request_sequence=33,
                    ),
                }
            )
        )
    values.append(terminal.model_copy(update={"sequence": len(values)}))
    return base.model_copy(
        update={"events": tuple(values), "highest_sequence": len(values) - 1}
    )


@pytest.mark.parametrize(
    ("method", "params", "operation_name"),
    (
        (
            "prompts/get",
            {"name": "interactive-prompt", "arguments": {"q": "x"}},
            "interactive-prompt",
        ),
        ("resources/read", {"uri": "memory://interactive"}, "memory://interactive"),
    ),
)
def test_prompt_and_resource_mrtr_attempts_are_one_logical_protocol_call(
    method: str, params: dict[str, object], operation_name: str
) -> None:
    view = _mrtr_protocol_trace(method, params).view()

    calls = [
        entry
        for entry in view.protocol
        if entry.method.state is ObservationState.OBSERVED
        and entry.method.value == method
    ]
    assert len(calls) == 1
    call = calls[0]
    assert isinstance(call, ProtocolEntry)
    assert call.status.value == "completed"
    assert call.response.value["content"][0]["text"] == "done"
    assert call.operation_kind == ("prompt" if method == "prompts/get" else "resource")
    assert len(call.attempts) == 2
    assert [attempt.jsonrpc_id.value for attempt in call.attempts] == [31, 32]
    assert call.attempts[0].input_required is True
    assert call.attempts[0].status is TraceStatus.INCOMPLETE
    assert call.attempts[0].continuation_state.value == "opaque-protocol-state"
    assert call.attempts[0].raw_result.value["requestState"] == "opaque-protocol-state"
    assert call.attempts[1].request_state.value == "opaque-protocol-state"
    assert call.attempts[1].status is TraceStatus.COMPLETED
    assert call.attempts[1].result.value["content"][0]["text"] == "done"
    assert call.attempts[0].sequence_start < call.attempts[1].sequence_start
    assert (
        call.attempts[1].timing.end_offset_ms >= call.attempts[1].timing.start_offset_ms
    )
    assert call.attempts[1].input_responses.value == {
        "approval": {"action": "accept", "content": {"approved": True}}
    }
    assert call.operation_name.value == operation_name
    assert len(view.elicitations) == 1
    elicitation = view.elicitations[0]
    assert elicitation.operation_kind == call.operation_kind
    assert elicitation.operation_name == operation_name
    assert elicitation.logical_operation_id == call.entry_id
    assert elicitation.request_key == "approval"
    assert elicitation.round_index == 1
    assert elicitation.action == "accept"
    assert elicitation.input_responses.value["approval"]["action"] == "accept"
    assert type(view).model_validate(view.model_dump(mode="json")) == view


@pytest.mark.parametrize(
    ("method", "params"),
    (
        ("prompts/get", {"name": "interactive-prompt"}),
        ("resources/read", {"uri": "memory://interactive"}),
    ),
)
def test_ambiguous_prompt_and_resource_mrtr_continuation_is_not_merged(
    method: str, params: dict[str, object]
) -> None:
    view = _mrtr_protocol_trace(method, params, ambiguous=True).view()

    calls = [
        entry
        for entry in view.protocol
        if entry.method.state is ObservationState.OBSERVED
        and entry.method.value == method
    ]
    assert len(calls) == 3
    assert all(len(call.attempts) == 1 for call in calls)


@pytest.mark.parametrize(
    ("method", "params", "changed_params"),
    (
        (
            "prompts/get",
            {"name": "interactive-prompt", "arguments": {"q": "x"}},
            {"name": "interactive-prompt", "arguments": {"q": "changed"}},
        ),
        (
            "prompts/get",
            {"name": "interactive-prompt", "arguments": {"q": "x"}},
            {
                "name": "interactive-prompt",
                "arguments": {"q": "x"},
                "_meta": {"changed": True},
            },
        ),
        (
            "prompts/get",
            {"name": "interactive-prompt", "arguments": {"q": "x"}},
            {
                "name": "interactive-prompt",
                "arguments": {"q": "x"},
                "task": {"id": "changed"},
            },
        ),
        (
            "resources/read",
            {"uri": "memory://interactive"},
            {"uri": "memory://changed"},
        ),
        (
            "resources/read",
            {"uri": "memory://interactive"},
            {"uri": "memory://interactive", "_meta": {"changed": True}},
        ),
        (
            "resources/read",
            {"uri": "memory://interactive"},
            {"uri": "memory://interactive", "task": {"id": "changed"}},
        ),
    ),
)
def test_changed_prompt_or_resource_operation_params_are_not_absorbed(
    method: str,
    params: dict[str, object],
    changed_params: dict[str, object],
) -> None:
    trace = _mrtr_protocol_trace(method, params)
    retry = trace.events[9].model_copy(
        update={
            "payload": {
                **trace.events[9].payload,
                "params": {
                    **changed_params,
                    "requestState": "opaque-protocol-state",
                    "inputResponses": {
                        "approval": {"action": "accept", "content": {"approved": True}}
                    },
                },
            }
        }
    )
    rebuilt = trace.model_copy(
        update={"events": (*trace.events[:9], retry, *trace.events[10:])}
    )

    calls = [
        entry
        for entry in rebuilt.view().protocol
        if entry.method.state is ObservationState.OBSERVED
        and entry.method.value == method
    ]
    assert len(calls) == 2
    assert all(len(call.attempts) == 1 for call in calls)


@pytest.mark.parametrize(
    ("method", "params", "other_params"),
    (
        (
            "prompts/get",
            {"name": "interactive-prompt", "arguments": {"q": "x"}},
            {"name": "other-prompt", "arguments": {"q": "x"}},
        ),
        (
            "resources/read",
            {"uri": "memory://interactive"},
            {"uri": "memory://other"},
        ),
    ),
)
def test_interleaved_same_method_operation_does_not_break_mrtr_correlation(
    method: str,
    params: dict[str, object],
    other_params: dict[str, object],
) -> None:
    trace = _mrtr_protocol_trace(method, params)
    other_request = trace.events[7].model_copy(
        update={
            "event_id": EventId(f"{method}-other-request"),
            "sequence": 9,
            "correlation": RequestLink(
                jsonrpc_id=41,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=41,
            ),
            "payload": {"method": method, "params": other_params},
        }
    )
    other_result = trace.events[8].model_copy(
        update={
            "event_id": EventId(f"{method}-other-result"),
            "sequence": 10,
            "correlation": RequestLink(
                jsonrpc_id=41,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=41,
            ),
            "kind": EventKind.MCP_RESPONSE,
            "payload": {"method": method, "result": {"content": []}},
        }
    )
    values = [*trace.events[:9], other_request, other_result, *trace.events[9:]]
    values = [
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(values)
    ]
    rebuilt = trace.model_copy(
        update={"events": tuple(values), "highest_sequence": len(values) - 1}
    )

    calls = [
        entry
        for entry in rebuilt.view().protocol
        if entry.method.state is ObservationState.OBSERVED
        and entry.method.value == method
    ]
    assert len(calls) == 2
    assert sorted(len(call.attempts) for call in calls) == [1, 2]


@pytest.mark.parametrize(
    ("method", "params"),
    (
        ("prompts/get", {"name": "interactive-prompt"}),
        ("resources/read", {"uri": "memory://interactive"}),
    ),
)
def test_prompt_and_resource_mrtr_attempts_keep_round_responses_scoped(
    method: str, params: dict[str, object]
) -> None:
    trace = _mrtr_protocol_trace(method, params)
    second_input_required = trace.events[10].model_copy(
        update={
            "event_id": EventId(f"{method}-result-2-input"),
            "payload": {
                "method": method,
                "result": {
                    "resultType": "input_required",
                    "inputRequests": {
                        "confirmation": {
                            "method": "elicitation/create",
                            "params": {
                                "mode": "url",
                                "message": "Confirm access",
                                "url": "https://example.test/confirm",
                            },
                        }
                    },
                    "requestState": "opaque-protocol-state-2",
                },
            },
        }
    )
    third_request = trace.events[9].model_copy(
        update={
            "event_id": EventId(f"{method}-request-3"),
            "correlation": RequestLink(
                jsonrpc_id=34,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=34,
            ),
            "payload": {
                "method": method,
                "params": {
                    **params,
                    "requestState": "opaque-protocol-state-2",
                    "inputResponses": {"confirmation": {"action": "accept"}},
                },
            },
        }
    )
    third_result = trace.events[10].model_copy(
        update={
            "event_id": EventId(f"{method}-result-3"),
            "correlation": RequestLink(
                jsonrpc_id=34,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=34,
            ),
            "payload": {
                "method": method,
                "result": {"content": [{"type": "text", "text": "done"}]},
            },
        }
    )
    terminal = trace.events[11]
    values = [
        *trace.events[:10],
        second_input_required,
        third_request,
        third_result,
        terminal.model_copy(update={"sequence": 13}),
    ]
    values = [
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(values)
    ]
    view = trace.model_copy(
        update={"events": tuple(values), "highest_sequence": len(values) - 1}
    ).view()

    calls = [
        entry
        for entry in view.protocol
        if entry.method.state is ObservationState.OBSERVED
        and entry.method.value == method
    ]
    assert len(calls) == 1
    call = calls[0]
    assert [attempt.jsonrpc_id.value for attempt in call.attempts] == [31, 32, 34]
    assert call.attempts[1].input_responses.value == {
        "approval": {"action": "accept", "content": {"approved": True}}
    }
    assert call.attempts[2].input_responses.value == {
        "confirmation": {"action": "accept"}
    }
    assert [(item.round_index, item.request_key) for item in view.elicitations] == [
        (1, "approval"),
        (2, "confirmation"),
    ]
