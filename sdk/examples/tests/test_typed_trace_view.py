"""Copyable examples for finalized, typed TraceView inspection."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from m3 import expect
from m3.async_api import AsyncMCPTestKit
from m3.execution_trace import ExecutionTraceRecorder
from m3.harness import (
    HarnessObservationSink,
    RawEvidenceInput,
    RawFrameObservation,
)
from m3.observability import (
    ACPTrace,
    Observation,
    ObservationReason,
    ObservationState,
)
from m3.storage import InMemoryExecutionStore, SQLiteExecutionStore
from m3.sync_api import MCPTestKit
from m3.types import (
    CallTool,
    DirectSpec,
    ExecutionOutcome,
    ExecutionResult,
    ServerBinding,
    StdioServer,
)


def _quote_spec(example_server: StdioServer) -> DirectSpec:
    return DirectSpec(
        servers=(ServerBinding(server=example_server, alias="example-mcp"),),
        operation=CallTool(
            server="example-mcp",
            name="shipping_quote",
            arguments={"weight_kg": 2, "zone": "local"},
        ),
    )


def test_finalized_view_exposes_typed_runtime_and_tool(
    example_server: StdioServer,
) -> None:
    with MCPTestKit(env={}) as kit:
        with kit.direct(example_server) as client:
            client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})
        trace = client.final_trace

    assert trace is not None
    view = trace.view()
    assert view.outcome is ExecutionOutcome.COMPLETED
    assert view.completeness in {"complete", "partial"}
    assert view.runtime.kind == "direct"
    assert view.runtime.transport.state in {
        ObservationState.OBSERVED,
        ObservationState.NOT_EMITTED,
    }
    assert view.runtime.initialization.state is ObservationState.OBSERVED
    assert view.tool_calls[0].tool.value == "shipping_quote"
    assert view.tool_calls[0].wire.state is ObservationState.OBSERVED
    assert view.summary.timing.duration_ms >= 0


def test_matchers_and_indexes_target_the_finalized_view(
    example_server: StdioServer,
) -> None:
    with MCPTestKit(env={}) as kit:
        result = kit.run(_quote_spec(example_server))
    assert result.trace_view is not None
    view = result.trace_view

    expect(view).to_have_tool_call(
        "shipping_quote",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
    expect(view).to_have_no_tool_call("missing_tool")
    assert view.tool_calls == view.for_server("example-mcp").tool_calls
    assert not view.for_turn("unknown-turn").timeline
    assert view.between(0, view.summary.timing.duration_ms).timeline
    assert view.messages == tuple(
        item for item in view.timeline if item.kind == "message"
    )


def test_observation_availability_states_are_explicit_data_contract_examples() -> None:
    """These frozen values document the contract, not provider behavior."""
    observed = Observation[str](state=ObservationState.OBSERVED, value="visible")
    not_emitted = Observation[str](
        state=ObservationState.NOT_EMITTED,
        reason=ObservationReason.PROVIDER_DID_NOT_EMIT,
    )
    unsupported = ACPTrace().usage
    encrypted_reasoning = Observation[str](
        state=ObservationState.ENCRYPTED,
        reason=ObservationReason.PROVIDER_ENCRYPTED,
    )
    redacted = Observation[str](
        state=ObservationState.REDACTED,
        reason=ObservationReason.REDACTED_BY_POLICY,
        value="[REDACTED]",
    )
    truncated = Observation[str](
        state=ObservationState.TRUNCATED,
        reason=ObservationReason.EVIDENCE_TRUNCATED,
        value="safe prefix",
    )
    malformed = Observation[str](
        state=ObservationState.UNAVAILABLE,
        reason=ObservationReason.MALFORMED_SOURCE,
    )

    assert observed.value == "visible"
    assert not_emitted.state is ObservationState.NOT_EMITTED
    assert not_emitted.reason is ObservationReason.PROVIDER_DID_NOT_EMIT
    assert unsupported.state is ObservationState.UNSUPPORTED
    assert unsupported.reason is ObservationReason.HARNESS_UNSUPPORTED
    assert encrypted_reasoning.state is ObservationState.ENCRYPTED
    assert redacted.value == "[REDACTED]"
    assert truncated.value == "safe prefix"
    assert malformed.state is ObservationState.UNAVAILABLE
    assert malformed.reason is ObservationReason.MALFORMED_SOURCE
    for value in (
        observed,
        not_emitted,
        unsupported,
        encrypted_reasoning,
        redacted,
        truncated,
        malformed,
    ):
        assert type(value).model_validate(value.model_dump(mode="json")) == value


def test_sqlite_is_an_opt_in_reopenable_trace_store(
    example_server: StdioServer, tmp_path: Path
) -> None:
    database = tmp_path / "example-traces.sqlite"
    store = SQLiteExecutionStore(database)
    with MCPTestKit(store=store, env={}) as kit:
        result = kit.run(_quote_spec(example_server))
        assert result.trace_view is not None
        first = result.trace_view
        execution_id = result.snapshot.execution_id
    store.close()

    reopened = SQLiteExecutionStore(database)
    try:
        second = reopened.get_trace_view(execution_id)
        assert second == first
        assert second.outcome is ExecutionOutcome.COMPLETED
    finally:
        reopened.close()


def test_public_raw_evidence_api_reads_a_bounded_captured_frame() -> None:
    """Raw capture contract example; the frame is a deterministic fixture."""
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "raw-example")
    sink = HarnessObservationSink(recorder)
    sink.emit(
        RawFrameObservation(
            observation_id="raw-frame",
            harness_kind="example-fixture",
            turn_sequence=0,
            wall_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            monotonic_offset_ms=1,
            payload={"safe": "value"},
            raw_evidence=RawEvidenceInput(
                content='{"safe":"value"}', media_type="application/json"
            ),
        )
    )
    view = recorder.finalize(ExecutionOutcome.COMPLETED).view()
    raw_entries = [raw for raw in view.raw_messages if raw.evidence_ref is not None]
    assert len(raw_entries) == 1
    reference = raw_entries[0].evidence_ref
    assert reference is not None
    evidence = store.read_raw_evidence(reference)
    assert evidence.redacted is True
    assert evidence.content == '{"safe":"value"}'
    assert evidence.media_type == "application/json"
    assert (
        evidence.returned_size_bytes == evidence.size_bytes == len(b'{"safe":"value"}')
    )


async def _async_quote(example_server: StdioServer) -> ExecutionResult:
    async with AsyncMCPTestKit(env={}) as kit:
        return await kit.run(_quote_spec(example_server))


def test_async_and_sync_typed_views_have_matching_semantics(
    example_server: StdioServer,
) -> None:
    with MCPTestKit(env={}) as kit:
        sync_result = kit.run(_quote_spec(example_server))
    async_result = asyncio.run(_async_quote(example_server))
    assert sync_result.trace_view is not None
    assert async_result.trace_view is not None
    assert (
        sync_result.trace_view.runtime.kind
        == async_result.trace_view.runtime.kind
        == "direct"
    )
    assert (
        sync_result.trace_view.outcome
        is async_result.trace_view.outcome
        is ExecutionOutcome.COMPLETED
    )
    assert (
        sync_result.trace_view.tool_calls[0].tool
        == async_result.trace_view.tool_calls[0].tool
    )
