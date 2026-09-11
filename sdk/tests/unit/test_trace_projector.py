"""Finalized direct-MCP projection and public trace retrieval contracts."""

from datetime import datetime, timezone

import pytest

from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.errors import (
    ExecutionNotFound,
    KitClosed,
    RawEvidenceUnavailable,
    TraceNotFinalized,
    TraceUnavailable,
)
from mcp_pal.events import EventFactory, EventSequence
from mcp_pal.execution_trace import ExecutionTraceRecorder, TraceRecorderError
from mcp_pal.observability import (
    ArtifactEntry,
    EvaluationEntry,
    InitializationEntry,
    InteractionEntry,
    LifecycleEntry,
    MessageEntry,
    Observation,
    ObservationReason,
    ObservationState,
    ProcessEntry,
    ProtocolEntry,
    ProviderEntry,
    RawMessageEntry,
    ReasoningEntry,
    ToolCallEntry,
    TraceStatus,
    TransportEntry,
)
from mcp_pal.storage import InMemoryExecutionStore, SQLiteExecutionStore
from mcp_pal.sync_api import MCPTestKit
from mcp_pal.trace.projector import _entry_for_event
from mcp_pal.types import (
    ConnectionId,
    EvaluationStatus,
    EventDirection,
    EventKind,
    EvidenceRef,
    ExecutionOutcome,
    ExecutionResult,
    ExecutionState,
    ExecutionStatus,
    RequestLink,
    TraceResult,
)


def _trace(
    *, terminal: bool = True, outcome: ExecutionOutcome = ExecutionOutcome.COMPLETED
) -> TraceResult:
    execution = "projection-execution"
    factory = EventFactory(
        execution,
        allocator=EventSequence(start=0),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="test.direct",
    )
    connection = "projection-connection"
    events = [
        factory.create(
            EventKind.EXECUTION_CREATED,
            connection_id=connection,
            payload={"trace_id": "trace-projection"},
            lifecycle_phase="startup",
        ),
        factory.create(
            EventKind.TRANSPORT_CONNECTED,
            connection_id=connection,
            payload={"transport": "stdio"},
        ),
        factory.create(
            EventKind.MCP_REQUEST,
            connection_id=connection,
            correlation=RequestLink(
                jsonrpc_id=1,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=1,
            ),
            payload={"method": "initialize", "params": {}},
        ),
        factory.create(
            EventKind.MCP_RESPONSE,
            connection_id=connection,
            correlation=RequestLink(
                jsonrpc_id=1,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=1,
            ),
            payload={
                "method": "initialize",
                "result": {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "fixture", "version": "1"},
                    "instructions": "Use echo",
                    "capabilities": {"tools": {}},
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo input",
                            "inputSchema": {"type": "object"},
                        }
                    ],
                },
            },
        ),
        factory.create(
            EventKind.MCP_INITIALIZED,
            connection_id=connection,
            correlation=RequestLink(
                jsonrpc_id=1,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=1,
            ),
            payload={},
        ),
        factory.create(
            EventKind.MCP_REQUEST,
            connection_id=connection,
            correlation=RequestLink(
                jsonrpc_id=2,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=2,
            ),
            payload={"method": "tools/list", "params": {}},
        ),
        factory.create(
            EventKind.MCP_RESPONSE,
            connection_id=connection,
            correlation=RequestLink(
                jsonrpc_id=2,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=2,
            ),
            payload={"method": "tools/list", "result": {"tools": []}},
        ),
        factory.create(
            EventKind.TOOL_CALL_REQUESTED,
            connection_id=connection,
            correlation=RequestLink(
                jsonrpc_id=3,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=3,
            ),
            payload={
                "method": "tools/call",
                "call_id": "call-3",
                "params": {"name": "echo", "arguments": {"text": "hello"}},
            },
        ),
        factory.create(
            EventKind.TOOL_RESULT_RECEIVED,
            connection_id=connection,
            correlation=RequestLink(
                jsonrpc_id=3,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=3,
            ),
            payload={
                "method": "tools/call",
                "result": {"content": [{"type": "text", "text": "hello"}]},
            },
        ),
        factory.create(
            EventKind.REASONING,
            connection_id=connection,
            reasoning={"visibility": "visible", "explicit": True},
            payload={"content": [{"kind": "text", "text": "planning"}]},
        ),
        factory.create(
            EventKind.AGENT_MESSAGE,
            connection_id=connection,
            payload={"role": "user", "content": [{"kind": "text", "text": "hello"}]},
        ),
        factory.create(
            EventKind.MCP_NOTIFICATION,
            connection_id=connection,
            correlation=RequestLink(
                direction=EventDirection.SERVER_TO_CLIENT,
            ),
            payload={"method": "notifications/progress", "params": {"progress": 1}},
        ),
    ]
    if terminal:
        events.append(
            factory.create(
                EventKind.EXECUTION_FINISHED,
                connection_id=connection,
                payload={
                    "outcome": outcome.value,
                    "completeness": "complete",
                    "limitations": [],
                },
            )
        )
    return TraceResult(
        trace_id="trace-projection",
        execution_id=execution,
        highest_sequence=events[-1].sequence,
        events=tuple(events),
    )


def test_direct_projector_pairs_protocol_and_tools_and_preserves_indexes() -> None:
    view = _trace().view()
    assert isinstance(view.protocol[0], ProtocolEntry)
    assert any(entry.method.value == "tools/list" for entry in view.protocol)
    assert len(view.tool_calls) == 1
    assert isinstance(view.tool_calls[0], ToolCallEntry)
    assert view.tool_calls[0].tool.value == "echo"
    assert view.tool_calls[0].result.value is not None
    assert isinstance(view.messages[0], MessageEntry)
    assert isinstance(view.reasoning[0], ReasoningEntry)
    assert any(isinstance(entry, InitializationEntry) for entry in view.timeline)
    assert view.runtime.kind == "direct"
    assert view.runtime.transport.value.value == "stdio"
    assert view.runtime.initialization.value.server_name.value == "fixture"
    assert view.summary.tool_call_count == 1
    assert view.summary.successful_tool_call_count == 1


def test_json_null_is_observed_and_round_trips_while_invalid_json_is_unavailable() -> (
    None
):
    observed = Observation[object](state=ObservationState.OBSERVED, value=None)
    assert Observation.model_validate_json(observed.model_dump_json()).value is None
    trace = _trace()
    request = trace.events[7].model_copy(
        update={
            "payload": {
                "method": "tools/call",
                "params": {"name": "echo", "arguments": None},
            }
        }
    )
    result = trace.events[8].model_copy(
        update={"payload": {"result": {"structuredContent": {"bad": {1, 2}}}}}
    )
    view = trace.model_copy(
        update={"events": (*trace.events[:7], request, result, *trace.events[9:])}
    ).view()
    assert view.tool_calls[0].arguments.state is ObservationState.OBSERVED
    assert view.tool_calls[0].arguments.value is None
    assert view.tool_calls[0].result.value is not None
    assert (
        view.tool_calls[0].result.value.structured_content.state
        is ObservationState.UNAVAILABLE
    )


def test_tools_call_is_projected_from_generic_mcp_request_response_events() -> None:
    trace = _trace()
    request = trace.events[7].model_copy(update={"kind": EventKind.MCP_REQUEST})
    response = trace.events[8].model_copy(update={"kind": EventKind.MCP_RESPONSE})
    generic = trace.model_copy(
        update={"events": (*trace.events[:7], request, response, *trace.events[9:])}
    )
    calls = generic.view().tool_calls
    assert len(calls) == 1
    assert calls[0].tool.value == "echo"
    assert calls[0].tool_status.value == "success"


def test_projector_keeps_unmatched_response_as_diagnostic_and_is_deterministic() -> (
    None
):
    trace = _trace()
    event = trace.events[-1]
    unmatched = event.model_copy(
        update={
            "sequence": event.sequence,
            "kind": EventKind.MCP_ERROR,
            "payload": {"error": {"code": -32000, "message": "bad"}},
            "correlation": RequestLink(
                jsonrpc_id="unmatched",
                direction=EventDirection.SERVER_TO_CLIENT,
            ),
        }
    )
    # Replace the terminal with the unmatched error and append a new terminal.
    events = (*trace.events[:-1], unmatched)
    factory = EventFactory(
        "projection-execution",
        allocator=EventSequence(start=len(events)),
    )
    terminal = factory.create(
        EventKind.EXECUTION_FINISHED,
        connection_id="projection-connection",
        monotonic_offset_ms=100,
        payload={"outcome": "completed", "completeness": "complete", "limitations": []},
    )
    rebuilt = TraceResult(
        trace_id=trace.trace_id,
        execution_id=trace.execution_id,
        highest_sequence=terminal.sequence,
        events=(*events, terminal),
    )
    first = rebuilt.view()
    second = rebuilt.view()
    assert first == second
    assert any(
        item.kind == "diagnostic" and getattr(item, "code", "") == "unmatched_response"
        for item in first.timeline
    )


def test_finalized_only_and_malformed_terminal_errors() -> None:
    with pytest.raises(TraceNotFinalized):
        _trace(terminal=False).view()
    trace = _trace()
    bad_terminal = trace.events[-1].model_copy(
        update={"payload": {"outcome": "unknown"}}
    )
    with pytest.raises(TraceUnavailable):
        trace.model_copy(update={"events": (*trace.events[:-1], bad_terminal)}).view()


def test_store_retrieval_returns_trace_and_view_after_finalization() -> None:
    store = InMemoryExecutionStore()
    trace = _trace()
    store.create(
        snapshot=ExecutionState(execution_id=trace.execution_id),
    )
    store.append_events(trace.events)
    assert store.get_trace(trace.execution_id) == trace
    assert store.get_trace_view(trace.execution_id) == trace.view()
    assert store.get_trace("unknown") is None


def test_raw_evidence_is_a_typed_timeline_entry_without_displacing_protocol() -> None:
    trace = _trace()
    reference = EvidenceRef(
        evidence_id="re:" + "a" * 64,
        sha256="b" * 64,
        size_bytes=12,
        media_type="application/json",
        storage_key="raw/12",
    )
    event = trace.events[8].model_copy(update={"raw_evidence_ref": reference})
    rebuilt = trace.model_copy(
        update={"events": (*trace.events[:8], event, *trace.events[9:])}
    )
    view = rebuilt.view()
    assert any(isinstance(item, RawMessageEntry) for item in view.timeline)
    assert any(isinstance(item, ToolCallEntry) for item in view.timeline)


def test_paired_response_raw_evidence_remains_chronological_after_intervening_event() -> (
    None
):
    trace = _trace()
    reference = EvidenceRef(
        evidence_id="re:" + "c" * 64,
        sha256="d" * 64,
        size_bytes=4,
        media_type="application/json",
        storage_key="raw/response",
    )
    response = trace.events[8].model_copy(update={"raw_evidence_ref": reference})
    intervening = trace.events[9].model_copy(
        update={
            "kind": EventKind.DIAGNOSTIC,
            "payload": {"code": "between", "message": "between"},
        }
    )
    events = (*trace.events[:8], intervening, response, *trace.events[9:])
    events = tuple(
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(events)
    )
    view = trace.model_copy(
        update={"events": events, "highest_sequence": events[-1].sequence}
    ).view()
    starts = [entry.sequence_start for entry in view.timeline]
    assert starts == sorted(starts)
    response_raw = [
        entry
        for entry in view.timeline
        if isinstance(entry, RawMessageEntry) and entry.evidence_ref == reference
    ]
    assert len(response_raw) == 1


def test_sqlite_store_reopens_and_projects_the_same_finalized_view(tmp_path) -> None:
    database = tmp_path / "projection.sqlite"
    trace = _trace()
    first = SQLiteExecutionStore(database)
    first.create(snapshot=ExecutionState(execution_id=trace.execution_id))
    first.append_events(trace.events)
    expected = trace.view()
    reopened = SQLiteExecutionStore(database)
    assert reopened.get_trace(trace.execution_id) == trace
    assert reopened.get_trace_view(trace.execution_id) == expected


def test_sync_kit_exposes_store_trace_and_raw_evidence_apis() -> None:
    store = InMemoryExecutionStore()
    trace = _trace()
    store.create(snapshot=ExecutionState(execution_id=trace.execution_id))
    store.append_events(trace.events)
    with MCPTestKit(store=store) as kit:
        assert kit.get_trace(trace.execution_id) == trace
        assert kit.get_trace_view(trace.execution_id) == trace.view()
        with pytest.raises(RawEvidenceUnavailable):
            kit.read_raw_evidence(EvidenceRef(evidence_id="re:" + "0" * 64))


@pytest.mark.asyncio
async def test_async_kit_exposes_store_trace_api() -> None:
    store = InMemoryExecutionStore()
    trace = _trace()
    store.create(snapshot=ExecutionState(execution_id=trace.execution_id))
    store.append_events(trace.events)
    kit = AsyncMCPTestKit(store=store, embedded_worker=False)
    try:
        assert await kit.get_trace(trace.execution_id) == trace
        assert await kit.get_trace_view(trace.execution_id) == trace.view()
    finally:
        await kit.aclose()


def test_sync_kit_lookup_requires_a_store_and_respects_closed_state() -> None:
    kit = MCPTestKit()
    with kit, pytest.raises(TraceUnavailable):
        kit.get_trace("missing")
    with pytest.raises(KitClosed):
        kit.get_trace("missing")


def test_sync_kit_lookup_raises_execution_not_found_for_unknown_store_id() -> None:
    kit = MCPTestKit(store=InMemoryExecutionStore())
    with kit, pytest.raises(ExecutionNotFound):
        kit.get_trace("missing")


@pytest.mark.asyncio
async def test_async_kit_lookup_requires_a_store_and_respects_closed_state() -> None:
    kit = AsyncMCPTestKit(embedded_worker=False)
    with pytest.raises(TraceUnavailable):
        await kit.get_trace("missing")
    await kit.aclose()
    with pytest.raises(KitClosed):
        await kit.get_trace("missing")


@pytest.mark.asyncio
async def test_async_kit_lookup_and_raw_evidence_raise_typed_unknown_errors() -> None:
    kit = AsyncMCPTestKit(store=InMemoryExecutionStore(), embedded_worker=False)
    reference = EvidenceRef(evidence_id="re:" + "1" * 64)
    try:
        with pytest.raises(ExecutionNotFound):
            await kit.get_trace("missing")
        with pytest.raises(RawEvidenceUnavailable):
            await kit.read_raw_evidence(reference)
    finally:
        await kit.aclose()


def test_execution_result_exposes_the_finalized_typed_view() -> None:
    trace = _trace(outcome=ExecutionOutcome.FAILED)
    snapshot = ExecutionState(execution_id=trace.execution_id).transition(
        ExecutionStatus.FINISHED, ExecutionOutcome.FAILED
    )
    result = ExecutionResult(snapshot=snapshot, trace=trace)
    assert result.trace_view.outcome is ExecutionOutcome.FAILED


@pytest.mark.parametrize("outcome", list(ExecutionOutcome))
def test_all_terminal_outcomes_are_preserved_and_cleanup_is_independent(
    outcome: ExecutionOutcome,
) -> None:
    view = _trace(outcome=outcome).view()
    assert view.outcome is outcome
    assert view.summary.cleanup_status.value == "completed"


def test_cleanup_failure_is_the_only_terminal_cleanup_failure_signal() -> None:
    trace = _trace().model_copy(
        update={"completeness": "partial", "limitations": ("cleanup_failed",)}
    )
    terminal = trace.events[-1].model_copy(
        update={
            "payload": {
                "outcome": "completed",
                "completeness": "partial",
                "limitations": ["cleanup_failed"],
            }
        }
    )
    view = trace.model_copy(update={"events": (*trace.events[:-1], terminal)}).view()
    assert view.outcome is ExecutionOutcome.COMPLETED
    assert view.summary.cleanup_status.value == "failed"


@pytest.mark.parametrize("visibility", ["unavailable", "encrypted", "provider_hidden"])
def test_reasoning_availability_states_are_never_synthesized(visibility: str) -> None:
    trace = _trace()
    event = trace.events[9].model_copy(
        update={
            "reasoning": {"visibility": visibility, "explicit": False},
            "payload": {},
        }
    )
    view = trace.model_copy(
        update={"events": (*trace.events[:9], event, *trace.events[10:])}
    ).view()
    reasoning = view.reasoning[0].content
    assert reasoning.state.value == visibility
    assert reasoning.value is None


def test_visible_malformed_reasoning_is_unavailable_and_unknown_role_is_diagnostic() -> (
    None
):
    trace = _trace()
    reasoning = trace.events[9].model_copy(
        update={"payload": {"content": "not-a-block-list"}}
    )
    message = trace.events[10].model_copy(
        update={"payload": {"role": "alien", "content": []}}
    )
    rebuilt = trace.model_copy(
        update={"events": (*trace.events[:9], reasoning, message, *trace.events[11:])}
    )
    view = rebuilt.view()
    assert view.reasoning[0].content.state.value == "unavailable"
    assert any(
        getattr(item, "code", "") == "malformed_message_role" for item in view.timeline
    )


def test_provider_identity_and_pid_boolean_are_not_guessed() -> None:
    trace = _trace()
    provider = trace.events[1].model_copy(
        update={"kind": EventKind.PROVIDER_EVENT, "payload": {"data": {}}}
    )
    process = trace.events[2].model_copy(
        update={"kind": EventKind.PROCESS_STARTED, "payload": {"pid": True}}
    )
    rebuilt = trace.model_copy(
        update={"events": (trace.events[0], provider, process, *trace.events[3:])}
    )
    view = rebuilt.view()
    assert any(
        getattr(item, "code", "") == "malformed_provider_event"
        for item in view.timeline
    )
    process_entry = next(
        item for item in view.processes if item.sequence_start == process.sequence
    )
    assert process_entry.pid.value is None


def test_present_malformed_scalars_are_unavailable_and_provider_data_presence_is_preserved() -> (
    None
):
    trace = _trace()
    process = trace.events[1].model_copy(
        update={
            "kind": EventKind.PROCESS_STARTED,
            "payload": {"pid": True, "executable": 3, "stderr": None},
        }
    )
    provider_null = trace.events[2].model_copy(
        update={
            "kind": EventKind.PROVIDER_EVENT,
            "payload": {"provider": "p", "category": "c", "data": None},
        }
    )
    provider_missing = trace.events[3].model_copy(
        update={
            "kind": EventKind.PROVIDER_EVENT,
            "payload": {"provider": "p", "category": "c"},
        }
    )
    rebuilt = trace.model_copy(
        update={
            "events": (
                trace.events[0],
                process,
                provider_null,
                provider_missing,
                *trace.events[4:],
            )
        }
    )
    view = rebuilt.view()
    process_entry = next(
        item for item in view.processes if item.sequence_start == process.sequence
    )
    assert process_entry.pid.state is ObservationState.UNAVAILABLE
    assert process_entry.executable.state is ObservationState.UNAVAILABLE
    assert process_entry.stderr.state is ObservationState.UNAVAILABLE
    providers = [item for item in view.timeline if isinstance(item, ProviderEntry)]
    assert providers[0].data.state is ObservationState.OBSERVED
    assert providers[0].data.value is None
    assert providers[1].data.state is ObservationState.NOT_EMITTED


def test_reversed_and_incompatible_pairs_are_not_correlated() -> None:
    trace = _trace()
    request = trace.events[7]
    response = trace.events[8]
    reversed_events = [
        *list(trace.events[:7]),
        response,
        request,
        *list(trace.events[9:]),
    ]
    reversed_events = [
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(reversed_events)
    ]
    reversed_trace = trace.model_copy(
        update={
            "events": tuple(reversed_events),
            "highest_sequence": len(reversed_events) - 1,
        }
    )
    reversed_view = reversed_trace.view()
    assert reversed_view.tool_calls[0].tool_status.value == "incomplete"
    incompatible_request = request.model_copy(
        update={"kind": EventKind.SAMPLING_REQUEST}
    )
    incompatible = trace.model_copy(
        update={
            "events": (
                *trace.events[:7],
                incompatible_request,
                response,
                *trace.events[9:],
            )
        }
    )
    assert incompatible.view().reasoning == reversed_view.reasoning
    assert not any(
        call.tool_status.value == "success" for call in incompatible.view().tool_calls
    )


def test_tool_errors_protocol_errors_and_typed_fallback_call_ids() -> None:
    trace = _trace()
    failed_result = trace.events[8].model_copy(
        update={
            "payload": {
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "failed"}],
                }
            }
        }
    )
    failed = (
        trace.model_copy(
            update={"events": (*trace.events[:8], failed_result, *trace.events[9:])}
        )
        .view()
        .tool_calls[0]
    )
    assert failed.tool_status.value == "tool_error"
    assert failed.status.value == "tool_error"
    protocol_result = failed_result.model_copy(
        update={
            "kind": EventKind.MCP_ERROR,
            "payload": {"error": {"code": -32001, "message": "denied"}},
        }
    )
    protocol = (
        trace.model_copy(
            update={"events": (*trace.events[:8], protocol_result, *trace.events[9:])}
        )
        .view()
        .tool_calls[0]
    )
    assert protocol.tool_status.value == "protocol_error"
    assert (
        protocol.result.value is not None
        and protocol.result.value.error.value is not None
    )
    assert protocol.result.value.error.value.code.value == "protocol_error"
    assert protocol.call_id == "call-3"


@pytest.mark.parametrize(
    "payload",
    [
        {"result": {"content": [{"type": "text", "text": "ok"}], "isError": "false"}},
        {"result": {"content": [{"type": "text", "text": "ok"}], "is_error": "false"}},
        {},
        {"result": []},
    ],
)
def test_malformed_tool_results_never_become_success(payload) -> None:
    trace = _trace()
    response = trace.events[8].model_copy(
        update={"kind": EventKind.MCP_RESPONSE, "payload": payload}
    )
    view = trace.model_copy(
        update={"events": (*trace.events[:8], response, *trace.events[9:])}
    ).view()
    call = view.tool_calls[0]
    assert call.tool_status.value == "incomplete"
    assert call.status is TraceStatus.INCOMPLETE


def test_valid_artifact_and_evaluation_events_are_public_entries() -> None:
    trace = _trace()
    artifact = trace.events[1].model_copy(
        update={
            "kind": EventKind.ARTIFACT_RECORDED,
            "payload": {
                "artifact": {
                    "artifact_id": "artifact-1",
                    "execution_id": trace.execution_id.root,
                    "name": "trace.json",
                    "size_bytes": 1,
                    "sha256": "0" * 64,
                }
            },
        }
    )
    artifact_view = trace.model_copy(
        update={"events": (trace.events[0], artifact, *trace.events[2:])}
    ).view()
    assert any(isinstance(item, ArtifactEntry) for item in artifact_view.timeline)

    evaluation = trace.events[1].model_copy(
        update={
            "kind": EventKind.EVALUATION_RECORDED,
            "payload": {
                "evaluation": {
                    "evaluation_id": "evaluation-1",
                    "name": "quality",
                    "status": EvaluationStatus.PASSED.value,
                }
            },
        }
    )
    evaluation_view = trace.model_copy(
        update={"events": (trace.events[0], evaluation, *trace.events[2:])}
    ).view()
    assert any(isinstance(item, EvaluationEntry) for item in evaluation_view.timeline)


@pytest.mark.parametrize(
    ("tool_status", "trace_status"),
    [
        ("success", TraceStatus.COMPLETED),
        ("tool_error", TraceStatus.TOOL_ERROR),
        ("protocol_error", TraceStatus.PROTOCOL_ERROR),
        ("transport_error", TraceStatus.TRANSPORT_ERROR),
        ("timed_out", TraceStatus.TIMED_OUT),
        ("cancelled", TraceStatus.CANCELLED),
        ("incomplete", TraceStatus.INCOMPLETE),
    ],
)
def test_projector_preserves_every_tool_terminal_status(
    tool_status: str, trace_status: TraceStatus
) -> None:
    trace = _trace()
    result = trace.events[8].model_copy(
        update={"payload": {"status": tool_status, "result": {"content": []}}}
    )
    call = (
        trace.model_copy(
            update={"events": (*trace.events[:8], result, *trace.events[9:])}
        )
        .view()
        .tool_calls[0]
    )
    assert call.tool_status.value == tool_status
    assert call.status is trace_status


def test_recorder_reopen_initializes_empty_snapshot_and_rejects_bad_identity(
    tmp_path,
) -> None:
    for store in (
        InMemoryExecutionStore(),
        SQLiteExecutionStore(tmp_path / "reopen.sqlite"),
    ):
        execution = "reopen-execution"
        store.create(snapshot=ExecutionState(execution_id=execution))
        recorder = ExecutionTraceRecorder(store, execution, trace_id="trace-reopen")
        assert recorder.trace_id.root == "trace-reopen"
        with pytest.raises(TraceRecorderError):
            ExecutionTraceRecorder(store, execution, trace_id="different")


def test_projector_preserves_malformed_initialization_as_unavailable() -> None:
    trace = _trace()
    response = trace.events[3].model_copy(
        update={
            "payload": {
                "method": "initialize",
                "result": {
                    "protocolVersion": 7,
                    "serverInfo": "not-an-object",
                    "tools": {"not": "a-list"},
                },
            }
        }
    )
    view = trace.model_copy(
        update={"events": (*trace.events[:3], response, *trace.events[4:])}
    ).view()
    initialization = view.runtime.initialization.value
    assert initialization.protocol_version.state.value == "unavailable"
    assert initialization.server_name.state.value == "unavailable"
    assert initialization.tools.state.value == "unavailable"


@pytest.mark.parametrize(
    "payload",
    [
        {"method": "initialize"},
        {"method": "initialize", "result": None},
        {"method": "initialize", "result": []},
    ],
)
def test_unique_malformed_initialization_response_is_unavailable(payload) -> None:
    trace = _trace()
    response = trace.events[3].model_copy(update={"payload": payload})
    view = trace.model_copy(
        update={"events": (*trace.events[:3], response, *trace.events[4:])}
    ).view()
    initialization = view.runtime.initialization.value
    assert initialization.protocol_version.state is ObservationState.UNAVAILABLE
    entry = next(
        item for item in view.timeline if isinstance(item, InitializationEntry)
    )
    assert entry.tools.state is ObservationState.UNAVAILABLE


@pytest.mark.parametrize(
    "update",
    [
        {"jsonrpc_id": 99},
        {"direction": EventDirection.CLIENT_TO_SERVER},
    ],
)
def test_initialization_lookup_validates_initialized_id_and_direction(update) -> None:
    trace = _trace()
    initialized = trace.events[4].model_copy(
        update={"correlation": trace.events[4].correlation.model_copy(update=update)}
    )
    view = trace.model_copy(
        update={"events": (*trace.events[:4], initialized, *trace.events[5:])}
    ).view()
    assert (
        view.runtime.initialization.value.server_name.state
        is ObservationState.NOT_EMITTED
    )


def test_initialization_lists_preserve_null_as_malformed_and_support_official_aliases() -> (
    None
):
    trace = _trace()
    response = trace.events[3].model_copy(
        update={
            "payload": {
                "result": {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "fixture", "version": "1"},
                    "tools": None,
                    "resourceTemplates": [],
                }
            }
        }
    )
    view = trace.model_copy(
        update={"events": (*trace.events[:3], response, *trace.events[4:])}
    ).view()
    initialization = view.runtime.initialization.value
    assert initialization.tools.state is ObservationState.UNAVAILABLE
    assert initialization.resource_templates.state is ObservationState.OBSERVED


def test_malformed_present_transport_and_valid_empty_strings_are_distinguished() -> (
    None
):
    trace = _trace()
    transport = trace.events[1].model_copy(update={"payload": {"transport": 7}})
    transport_view = trace.model_copy(
        update={"events": (trace.events[0], transport, *trace.events[2:])}
    ).view()
    assert transport_view.runtime.transport.state is ObservationState.UNAVAILABLE

    response = trace.events[3].model_copy(
        update={
            "payload": {
                "result": {
                    "instructions": "",
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            }
        }
    )
    string_view = trace.model_copy(
        update={"events": (*trace.events[:3], response, *trace.events[4:])}
    ).view()
    initialization = string_view.runtime.initialization.value
    assert initialization.instructions.state is ObservationState.OBSERVED
    assert initialization.instructions.value == ""

    process = trace.events[1].model_copy(
        update={"kind": EventKind.PROCESS_EXITED, "payload": {"stderr": ""}}
    )
    process_view = trace.model_copy(
        update={"events": (trace.events[0], process, *trace.events[2:])}
    ).view()
    process_entry = next(
        item for item in process_view.processes if item.sequence_start == 1
    )
    assert process_entry.stderr.state is ObservationState.OBSERVED


@pytest.mark.parametrize(
    ("kind", "payload", "phase"),
    [
        (EventKind.TRANSPORT_CONNECTED, {"transport": "stdio"}, "connected"),
        (
            EventKind.TRANSPORT_CONNECTED,
            {"configured_transport": "stdio", "instrumented_transport": "sse"},
            "connected",
        ),
        (EventKind.TRANSPORT_DISCONNECTED, {"transport": "stdio"}, "disconnected"),
    ],
)
def test_transport_events_project_to_typed_entries(
    kind: EventKind, payload: dict[str, object], phase: str
) -> None:
    trace = _trace()
    event = trace.events[1].model_copy(update={"kind": kind, "payload": payload})
    view = trace.model_copy(
        update={"events": (trace.events[0], event, *trace.events[2:])}
    ).view()
    entry = next(
        item for item in view.transports if item.sequence_start == event.sequence
    )
    assert entry.phase == phase
    assert entry.configured.state is ObservationState.OBSERVED
    assert entry.instrumented.state is ObservationState.OBSERVED


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"configured_transport": None},
        {"instrumented_transport": 7},
        {"configured_transport": "vendor_private"},
    ],
)
def test_transport_missing_or_malformed_values_are_explicit(
    payload: dict[str, object],
) -> None:
    trace = _trace()
    event = trace.events[1].model_copy(
        update={"kind": EventKind.TRANSPORT_DISCONNECTED, "payload": payload}
    )
    view = trace.model_copy(
        update={"events": (trace.events[0], event, *trace.events[2:])}
    ).view()
    entry = next(
        item for item in view.transports if item.sequence_start == event.sequence
    )
    if not payload:
        assert entry.configured.state is ObservationState.NOT_EMITTED
        assert entry.instrumented.state is ObservationState.NOT_EMITTED
    else:
        observation = (
            entry.configured
            if "configured_transport" in payload
            else entry.instrumented
        )
        assert observation.state is ObservationState.UNAVAILABLE
        assert observation.reason is ObservationReason.MALFORMED_SOURCE


def test_standalone_interaction_explicit_nulls_are_observed() -> None:
    trace = _trace()
    event = trace.events[1].model_copy(
        update={
            "kind": EventKind.PERMISSION_REQUEST,
            "payload": {"request": None, "response": None},
        }
    )
    view = trace.model_copy(
        update={"events": (trace.events[0], event, *trace.events[2:])}
    ).view()
    interaction = next(item for item in view.interactions if item.sequence_start == 1)
    assert interaction.request.state is ObservationState.OBSERVED
    assert interaction.request.value is None
    assert interaction.response.state is ObservationState.OBSERVED
    assert interaction.response.value is None


def test_runtime_initialization_does_not_cross_correlate_connections() -> None:
    trace = _trace()
    wrong_connection = trace.events[3].model_copy(
        update={"connection_id": ConnectionId("other-connection")}
    )
    view = trace.model_copy(
        update={"events": (*trace.events[:3], wrong_connection, *trace.events[4:])}
    ).view()
    initialization = view.runtime.initialization.value
    assert initialization.server_name.state is ObservationState.NOT_EMITTED
    assert initialization.tools.state is ObservationState.NOT_EMITTED


def test_projector_uses_unique_typed_id_fallback_but_not_ambiguous_candidates() -> None:
    trace = _trace()
    request = trace.events[7].model_copy(
        update={
            "correlation": RequestLink(
                jsonrpc_id=99,
                direction=EventDirection.CLIENT_TO_SERVER,
                request_sequence=None,
            )
        }
    )
    response = trace.events[8].model_copy(
        update={
            "correlation": RequestLink(
                jsonrpc_id=99,
                direction=EventDirection.SERVER_TO_CLIENT,
                request_sequence=999,
            )
        }
    )
    fallback = trace.model_copy(
        update={"events": (*trace.events[:7], request, response, *trace.events[9:])}
    ).view()
    assert fallback.tool_calls[0].tool_status.value == "success"

    second_request = request.model_copy(update={"sequence": request.sequence + 1})
    ambiguous_events = (
        *trace.events[:7],
        request,
        second_request,
        response,
        *trace.events[9:],
    )
    ambiguous_events = tuple(
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(ambiguous_events)
    )
    ambiguous = trace.model_copy(
        update={
            "events": ambiguous_events,
            "highest_sequence": ambiguous_events[-1].sequence,
        }
    ).view()
    assert all(call.tool_status.value == "incomplete" for call in ambiguous.tool_calls)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda trace: trace.events[0].model_copy(
            update={"payload": {"trace_id": "other"}}
        ),
        lambda trace: trace.events[1].model_copy(
            update={"kind": EventKind.EXECUTION_CREATED}
        ),
        lambda trace: trace.events[1].model_copy(
            update={
                "kind": EventKind.EXECUTION_FINISHED,
                "payload": {
                    "outcome": "completed",
                    "completeness": "complete",
                    "limitations": [],
                },
            }
        ),
    ],
    ids=["identity-conflict", "duplicate-created", "non-last-terminal"],
)
def test_projector_rejects_invalid_execution_boundaries(mutation) -> None:
    trace = _trace()
    changed = mutation(trace)
    events = trace.events
    if changed.sequence == events[0].sequence:
        events = (changed, *events[1:])
    else:
        events = (events[0], changed, *events[2:])
    with pytest.raises(TraceUnavailable):
        trace.model_copy(update={"events": events}).view()


def test_projector_rejects_terminal_metadata_disagreement() -> None:
    trace = _trace()
    terminal = trace.events[-1].model_copy(
        update={
            "payload": {
                "outcome": "completed",
                "completeness": "partial",
                "limitations": ["capture_incomplete"],
            }
        }
    )
    with pytest.raises(TraceUnavailable):
        trace.model_copy(update={"events": (*trace.events[:-1], terminal)}).view()


def test_unmatched_tool_request_is_wire_only_and_incomplete() -> None:
    trace = _trace()
    events = tuple(
        event.model_copy(update={"sequence": index})
        for index, event in enumerate(trace.events[:8] + trace.events[9:])
    )
    view = trace.model_copy(
        update={"events": events, "highest_sequence": events[-1].sequence}
    ).view()
    call = view.tool_calls[0]
    assert call.correlation.value == "wire_only"
    assert call.status.value == "incomplete"
    assert call.tool_status.value == "incomplete"


def test_typed_jsonrpc_ids_and_fallback_call_ids_remain_distinct() -> None:
    trace = _trace()
    request_payload = {
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {}},
    }
    int_request = trace.events[7].model_copy(update={"payload": request_payload})
    string_request = int_request.model_copy(
        update={
            "correlation": RequestLink(
                jsonrpc_id="3", direction="client_to_server", request_sequence=3
            ),
            "payload": request_payload,
        }
    )
    string_response = trace.events[8].model_copy(
        update={
            "correlation": RequestLink(
                jsonrpc_id="3", direction="server_to_client", request_sequence=3
            )
        }
    )
    view = trace.model_copy(
        update={
            "events": (
                *trace.events[:7],
                string_request,
                string_response,
                *trace.events[9:],
            )
        }
    ).view()
    assert view.tool_calls[0].call_id == "str:3"
    assert view.tool_calls[0].jsonrpc_id.value == "3"


@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        (EventKind.PROCESS_STARTED, {"executable": "fixture", "pid": 7}, ProcessEntry),
        (EventKind.PROCESS_EXITED, {"exit_code": 0, "signal": None}, ProcessEntry),
        (EventKind.TRANSPORT_DISCONNECTED, {}, TransportEntry),
        (
            EventKind.PERMISSION_REQUEST,
            {"request": {"permission": "read"}},
            InteractionEntry,
        ),
        (EventKind.SAMPLING_REQUEST, {"request": {"prompt": "x"}}, InteractionEntry),
        (EventKind.ELICITATION_REQUEST, {"request": {"form": {}}}, InteractionEntry),
        (EventKind.WORKSPACE_CHANGED, {"path": "x"}, object),
        (EventKind.DIAGNOSTIC, {"code": "E", "message": "bad"}, object),
        (
            EventKind.PROVIDER_EVENT,
            {"provider": "p", "category": "c", "data": {}},
            ProviderEntry,
        ),
        (EventKind.CLEANUP_STARTED, {"state": "running"}, LifecycleEntry),
    ],
)
def test_stable_event_kinds_have_typed_projection(kind, payload, expected) -> None:
    trace = _trace()
    event = trace.events[1].model_copy(update={"kind": kind, "payload": payload})
    view = trace.model_copy(
        update={"events": (trace.events[0], event, *trace.events[2:])}
    ).view()
    if expected is object:
        assert any(item.sequence_start == event.sequence for item in view.timeline)
    else:
        assert any(isinstance(item, expected) for item in view.timeline)


def test_every_event_kind_has_an_explicit_projection_contract() -> None:
    lifecycle = {
        EventKind.EXECUTION_CREATED,
        EventKind.EXECUTION_STATE_CHANGED,
        EventKind.EXECUTION_FINISHED,
        EventKind.SESSION_CREATED,
        EventKind.SESSION_STATE_CHANGED,
        EventKind.TURN_CREATED,
        EventKind.TURN_STATE_CHANGED,
        EventKind.CLEANUP_STARTED,
        EventKind.CLEANUP_FINISHED,
    }
    protocol = {
        EventKind.MCP_REQUEST,
        EventKind.MCP_RESPONSE,
        EventKind.MCP_ERROR,
        EventKind.MCP_NOTIFICATION,
        EventKind.MCP_PROGRESS,
        EventKind.MCP_CANCELLATION_REQUESTED,
        EventKind.MCP_CANCELLATION_COMPLETED,
    }
    transport = {
        EventKind.TRANSPORT_CONNECTED,
        EventKind.TRANSPORT_DISCONNECTED,
    }
    messages = {EventKind.AGENT_MESSAGE, EventKind.ASSISTANT_CONTENT}
    interactions = {
        EventKind.PERMISSION_REQUEST,
        EventKind.PERMISSION_RESPONSE,
        EventKind.SAMPLING_REQUEST,
        EventKind.SAMPLING_RESPONSE,
        EventKind.ELICITATION_REQUEST,
        EventKind.ELICITATION_RESPONSE,
        EventKind.FILESYSTEM_READ_REQUEST,
        EventKind.FILESYSTEM_READ_RESPONSE,
        EventKind.FILESYSTEM_WRITE_REQUEST,
        EventKind.FILESYSTEM_WRITE_RESPONSE,
        EventKind.TERMINAL_CREATE_REQUEST,
        EventKind.TERMINAL_CREATE_RESPONSE,
        EventKind.TERMINAL_OUTPUT_REQUEST,
        EventKind.TERMINAL_OUTPUT_RESPONSE,
        EventKind.TERMINAL_WAIT_REQUEST,
        EventKind.TERMINAL_WAIT_RESPONSE,
        EventKind.TERMINAL_RELEASE_REQUEST,
        EventKind.TERMINAL_RELEASE_RESPONSE,
        EventKind.TERMINAL_KILL_REQUEST,
        EventKind.TERMINAL_KILL_RESPONSE,
    }
    expected = {
        **{kind: "lifecycle" for kind in lifecycle},
        **{kind: "protocol" for kind in protocol},
        **{kind: "transport" for kind in transport},
        **{kind: "message" for kind in messages},
        EventKind.PROCESS_STARTED: "process",
        EventKind.PROCESS_EXITED: "process",
        EventKind.MCP_INITIALIZED: "initialization",
        EventKind.REASONING: "reasoning",
        **{kind: "interaction" for kind in interactions},
        EventKind.WORKSPACE_CHANGED: "workspace",
        EventKind.ARTIFACT_RECORDED: "diagnostic",
        EventKind.EVALUATION_RECORDED: "diagnostic",
        EventKind.DIAGNOSTIC: "diagnostic",
        EventKind.PROVIDER_EVENT: "provider",
        EventKind.TOOL_CALL_REQUESTED: "tool_call",
        EventKind.TOOL_RESULT_RECEIVED: "diagnostic",
    }
    assert set(expected) == set(EventKind)
    trace = _trace()
    payloads = {
        EventKind.AGENT_MESSAGE: {"content": [], "role": "user"},
        EventKind.ASSISTANT_CONTENT: {"content": [], "role": "assistant"},
        EventKind.TOOL_CALL_REQUESTED: {"params": {"name": "echo", "arguments": {}}},
        EventKind.PROVIDER_EVENT: {
            "provider": "fixture",
            "category": "event",
            "data": {},
        },
        EventKind.PROCESS_STARTED: {"pid": 1, "executable": "fixture"},
        EventKind.PROCESS_EXITED: {"pid": 1, "exit_code": 0},
        EventKind.REASONING: {"content": []},
        EventKind.WORKSPACE_CHANGED: {"path": "fixture"},
    }
    for kind, expected_kind in expected.items():
        source = trace.events[1].model_copy(
            update={
                "kind": kind,
                "payload": payloads.get(kind, {}),
                "reasoning": {"visibility": "visible", "explicit": True}
                if kind is EventKind.REASONING
                else None,
            }
        )
        assert _entry_for_event(source).kind == expected_kind, kind
