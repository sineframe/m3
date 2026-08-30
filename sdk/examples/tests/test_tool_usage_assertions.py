"""Contract examples for the rich tool-usage matcher API on finalized views."""

from __future__ import annotations

from mcp_pal import (
    CanonicalEvent,
    ConnectionId,
    EventDirection,
    EventId,
    EventKind,
    EventOrigin,
    EventProvenance,
    ExecutionId,
    RequestCorrelation,
    TraceId,
    TraceResult,
    expect,
)

_SHIPPING_RESULT = {
    "content": [
        {"type": "text", "text": '{"amount": 7.0, "currency": "USD"}'},
    ],
    "isError": False,
    "structuredContent": {"amount": 7.0, "currency": "USD"},
}


def _matcher_trace(origin: EventOrigin = EventOrigin.WIRE_OBSERVED) -> TraceResult:
    execution_id = ExecutionId("execution-matcher-example")
    connection_id = ConnectionId("connection-matcher-example")
    events: list[CanonicalEvent] = []
    calls = (
        ("shipping_quote", {"weight_kg": 1, "zone": "local"}, _SHIPPING_RESULT),
        (
            "shipping_quote",
            {"weight_kg": 1.001, "zone": "regional"},
            {
                "content": [
                    {"type": "text", "text": '{"amount": 8.5, "currency": "USD"}'}
                ],
                "isError": False,
                "structuredContent": {"amount": 8.5, "currency": "USD"},
            },
        ),
        (
            "batch_total",
            {"values": [1, 2, 3]},
            {
                "content": [{"type": "text", "text": '{"total": 6.0}'}],
                "isError": False,
                "structuredContent": {"total": 6.0},
            },
        ),
    )
    for index, (tool, arguments, result) in enumerate(calls):
        request_sequence = index * 2 + 1
        events.extend(
            (
                CanonicalEvent(
                    event_id=EventId(f"event-matcher-{index * 2}"),
                    execution_id=execution_id,
                    sequence=index * 2,
                    kind=EventKind.TOOL_CALL_REQUESTED,
                    monotonic_offset_ms=float(index * 10),
                    server_binding="example-mcp",
                    connection_id=connection_id,
                    correlation=RequestCorrelation(
                        jsonrpc_id=request_sequence,
                        direction=EventDirection.CLIENT_TO_SERVER,
                        request_sequence=request_sequence,
                    ),
                    payload={"params": {"name": tool, "arguments": arguments}},
                    provenance=EventProvenance(
                        origin=origin, source="synthetic-matcher-contract"
                    ),
                ),
                CanonicalEvent(
                    event_id=EventId(f"event-matcher-{index * 2 + 1}"),
                    execution_id=execution_id,
                    sequence=index * 2 + 1,
                    kind=EventKind.TOOL_RESULT_RECEIVED,
                    monotonic_offset_ms=float(index * 10 + 5),
                    server_binding="example-mcp",
                    connection_id=connection_id,
                    correlation=RequestCorrelation(
                        jsonrpc_id=request_sequence,
                        direction=EventDirection.SERVER_TO_CLIENT,
                        request_sequence=request_sequence,
                    ),
                    payload={"result": result},
                    provenance=EventProvenance(
                        origin=origin, source="synthetic-matcher-contract"
                    ),
                ),
            )
        )
    created = CanonicalEvent(
        event_id=EventId("event-matcher-created"),
        execution_id=execution_id,
        sequence=0,
        kind=EventKind.EXECUTION_CREATED,
        monotonic_offset_ms=0,
        payload={"trace_id": "trace-matcher-example", "lifecycle": "created"},
        provenance=EventProvenance(
            origin=EventOrigin.DERIVED, source="synthetic-matcher-contract"
        ),
    )
    shifted = tuple(
        event.model_copy(update={"sequence": event.sequence + 1}) for event in events
    )
    finished = CanonicalEvent(
        event_id=EventId("event-matcher-finished"),
        execution_id=execution_id,
        sequence=len(events) + 1,
        kind=EventKind.EXECUTION_FINISHED,
        monotonic_offset_ms=40,
        payload={"outcome": "completed", "completeness": "complete", "limitations": []},
        provenance=EventProvenance(
            origin=EventOrigin.DERIVED, source="synthetic-matcher-contract"
        ),
    )
    return TraceResult(
        trace_id=TraceId("trace-matcher-example"),
        execution_id=execution_id,
        highest_sequence=len(events) + 1,
        events=(created, *shifted, finished),
    )


def test_assert_exact_partial_regex_tolerant_and_unordered_tool_usage() -> None:
    view = _matcher_trace().view()

    # Exact result, explicit server identity, and a single matching call.
    expect(view).to_have_tool_call(
        name="shipping_quote",
        server="example-mcp",
        arguments={"weight_kg": 1, "zone": "local"},
        result={"structured_content": {"amount": 7.0, "currency": "USD"}},
        result_partial=True,
        status="success",
        count=1,
    )
    # Partial argument matching can combine with a regex for string fields.
    expect(view).to_have_tool_call(
        "shipping_quote",
        arguments={"zone": r"reg.*"},
        arguments_partial=True,
        arguments_regex=True,
        server_name="example-mcp",
        min_count=1,
        max_count=1,
    )
    # Numeric tolerance, an argument predicate, and a call-level predicate.
    expect(view).to_have_tool_call(
        "shipping_quote",
        arguments={"weight_kg": 1, "zone": "regional"},
        arguments_tolerance=0.01,
        argument_predicate=lambda arguments: arguments["zone"] == "regional",
        predicate=lambda call: call["status"] == "success",
        min_latency_ms=0,
        max_latency_ms=30_000,
    )
    # Sequence-valued arguments can be matched without caring about order.
    expect(view).to_have_tool_call(
        "batch_total",
        arguments={"values": [3, 2, 1]},
        arguments_unordered=True,
        result={"structured_content": {"total": 6.0}},
        result_partial=True,
        count=1,
    )
    expect(view).to_have_tool_call("shipping_quote", min_count=2, max_count=2)
    expect(view).to_not_have_tool_call("get_order")
    expect(view).to_have_no_tool_call("normalize_customer")
    expect(
        _matcher_trace(EventOrigin.HARNESS_REPORTED).view()
    ).to_have_reported_tool_call("shipping_quote", count=2)
    expect(view).to_have_tool_call("shipping_quote", evidence="any", count=2)
