"""Compact public-schema fixture for consumers such as the UI.

This deliberately exercises the typed fields that a renderer should use.  It
does not inspect canonical event payloads, which keeps the compatibility
contract independent of any one harness adapter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pytest
from mcp_pal.observability import (
    ACPTraceInfo,
    ClaudeCodeTraceInfo,
    CorrelationState,
    DirectTraceInfo,
    EvidenceConflict,
    HttpExchangeMetadata,
    InitializationEntry,
    InitializationValue,
    InteractionEntry,
    LifecycleEntry,
    MessageEntry,
    Observation,
    ObservationReason,
    ObservationState,
    OpenCodeTraceInfo,
    ProcessEntry,
    ProtocolEntry,
    ProtocolKind,
    ProviderEntry,
    RawEvidenceSource,
    RawMessageEntry,
    ReasoningEntry,
    ReportedToolCall,
    SafeHttpHeader,
    ToolCallEntry,
    ToolCallStatus,
    ToolResult,
    TraceEntry,
    TraceSummary,
    TraceTiming,
    TraceView,
    UsageEntry,
    UsageValue,
    WireToolCall,
)
from mcp_pal.types import (
    ErrorCode,
    ErrorInfo,
    EventDirection,
    EventOrigin,
    EventProvenance,
    ExecutionId,
    ExecutionOutcome,
    RawEvidenceRef,
    TextContent,
    TraceId,
    TransportKind,
)


def _observed(value: Any) -> Observation[Any]:
    return Observation(state=ObservationState.OBSERVED, value=value)


def _timing(start: int, end: int) -> TraceTiming:
    return TraceTiming(
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        start_offset_ms=start,
        end_offset_ms=end,
        duration_ms=end - start,
    )


def _entry(kind: str, sequence: int) -> Any:
    return {
        "entry_id": f"{kind}-{sequence}",
        "execution_id": "ui-execution",
        "session_id": "ui-session",
        "turn_id": "ui-turn",
        "sequence_start": sequence,
        "sequence_end": sequence,
        "timing": _timing(sequence, sequence + 1),
        "provenance": (
            EventProvenance(origin=EventOrigin.NORMALIZED, source="ui-fixture"),
        ),
    }


def _timeline() -> tuple[TraceEntry, ...]:
    started = LifecycleEntry(**_entry("lifecycle", 0), phase="started")
    message = MessageEntry(
        **_entry("message", 1),
        message_id=_observed("message-1"),
        content=(TextContent(text="answer"),),
    )
    reasoning = ReasoningEntry(
        **_entry("reasoning", 2),
        block_id=_observed("thought-1"),
        content=_observed((TextContent(text="visible thought"),)),
    )
    encrypted_reasoning = ReasoningEntry(
        **_entry("reasoning-encrypted", 3),
        block_id=_observed("thought-2"),
        content=Observation(
            state=ObservationState.ENCRYPTED,
            reason=ObservationReason.PROVIDER_ENCRYPTED,
        ),
    )
    hidden_reasoning = ReasoningEntry(
        **_entry("reasoning-hidden", 4),
        block_id=_observed("thought-3"),
        content=Observation(
            state=ObservationState.PROVIDER_HIDDEN,
            reason=ObservationReason.PROVIDER_HIDDEN,
        ),
    )
    result = ToolResult(
        content=(TextContent(text="ok"),),
        structured_content=_observed({"currency": "USD"}),
        is_error=False,
    )
    wire = WireToolCall(
        jsonrpc_id=_observed(7),
        server=_observed("example-mcp"),
        tool=_observed("shipping_quote"),
        arguments=_observed({"zone": "local"}),
        result=_observed(result),
        latency_ms=_observed(3.0),
    )
    reported = ReportedToolCall(
        provider_call_id=_observed("provider-call-1"),
        server=_observed("example-mcp"),
        tool=_observed("shipping_quote"),
        arguments=_observed({"zone": "local"}),
        result=_observed({"currency": "USD"}),
        status=_observed("success"),
    )
    conflict = EvidenceConflict(
        field="arguments",
        reported=_observed({"zone": "remote"}),
        wire=_observed({"zone": "local"}),
    )
    tool = ToolCallEntry(
        **_entry("tool_call", 5),
        call_id="call-1",
        provider_call_id=_observed("provider-call-1"),
        server=_observed("example-mcp"),
        tool=_observed("shipping_quote"),
        arguments=_observed({"zone": "local"}),
        result=_observed(result),
        tool_status=ToolCallStatus.SUCCESS,
        correlation=CorrelationState.CORRELATED,
        jsonrpc_id=_observed(7),
        server_latency_ms=_observed(3.0),
        reported=_observed(reported),
        wire=_observed(wire),
        conflicts=(conflict,),
    )
    failed_result = ToolResult(
        is_error=True,
        error=_observed(
            ErrorInfo(code=ErrorCode.PROTOCOL_ERROR, message="tool failed")
        ),
    )
    failed_tool = ToolCallEntry(
        **_entry("tool_call-failed", 6),
        call_id="call-2",
        server=_observed("example-mcp"),
        tool=_observed("always_fails"),
        arguments=_observed({}),
        result=_observed(failed_result),
        tool_status=ToolCallStatus.TOOL_ERROR,
        correlation=CorrelationState.CORRELATED,
    )
    protocol = ProtocolEntry(
        **_entry("protocol", 7),
        protocol=ProtocolKind.MCP,
        direction=EventDirection.SERVER_TO_CLIENT,
        method=_observed("tools/call"),
        http=_observed(
            HttpExchangeMetadata(
                method="POST",
                status_code=200,
                headers=(
                    SafeHttpHeader(name="content-type", value="application/json"),
                ),
            )
        ),
    )
    initialization = InitializationEntry(
        **_entry("initialization", 8),
        protocol_version=_observed("2025-11-25"),
        server_name=_observed("example-mcp"),
        capabilities=_observed({"tools": {}}),
    )
    usage = UsageEntry(
        **_entry("usage", 9),
        input_tokens=_observed(10),
        output_tokens=_observed(5),
        cache_read_tokens=_observed(2),
        cost=_observed(0.01),
        currency=_observed("USD"),
    )
    interaction = InteractionEntry(
        **_entry("interaction", 10),
        interaction_kind="filesystem.read.request",
        request=_observed({"path": "safe.txt"}),
        response=_observed({"status": "allowed"}),
    )
    terminal = InteractionEntry(
        **_entry("interaction-terminal", 11),
        interaction_kind="terminal.output.response",
        request=_observed({"terminal_id": "term-1"}),
        response=_observed({"output": "safe"}),
    )
    process = ProcessEntry(
        **_entry("process", 12),
        executable=_observed("fixture-agent"),
        pid=_observed(42),
        stderr=_observed(""),
    )
    exited = ProcessEntry(
        **_entry("process-exit", 13),
        exit_code=_observed(0),
    )
    raw = RawMessageEntry(
        **_entry("raw_message", 14),
        source=RawEvidenceSource.ACP,
        direction=EventDirection.HARNESS_TO_SDK,
        media_type="application/json",
        preview=Observation(
            state=ObservationState.REDACTED,
            reason=ObservationReason.REDACTED_BY_POLICY,
            value="[REDACTED]",
        ),
        evidence_ref=RawEvidenceRef(evidence_id="raw-1", size_bytes=12),
        size_bytes=12,
    )
    provider = ProviderEntry(
        **_entry("provider", 15),
        provider="claude-code",
        category="result",
        data=_observed({"stop_reason": "end_turn"}),
    )
    plan = ProviderEntry(
        **_entry("provider-plan", 16),
        provider="acp",
        category="plan",
        data=_observed({"entries": [{"content": "ship", "status": "in_progress"}]}),
    )
    state = ProviderEntry(
        **_entry("provider-state", 17),
        provider="acp",
        category="state",
        data=_observed({"status": "working"}),
    )
    capture = ProviderEntry(
        **_entry("provider-capture", 18),
        provider="capture",
        category="limits",
        data=Observation(
            state=ObservationState.TRUNCATED,
            reason=ObservationReason.EVIDENCE_TRUNCATED,
            value={"prefix": "safe"},
        ),
    )
    malformed = ProviderEntry(
        **_entry("provider-malformed", 19),
        provider="capture",
        category="malformed",
        data=Observation(
            state=ObservationState.UNAVAILABLE,
            reason=ObservationReason.MALFORMED_SOURCE,
        ),
    )
    raw_mcp = RawMessageEntry(
        **_entry("raw-mcp", 20),
        source=RawEvidenceSource.MCP,
        media_type="application/json",
        preview=_observed({"method": "tools/call"}),
        size_bytes=8,
    )
    raw_provider = RawMessageEntry(
        **_entry("raw-provider", 21),
        source=RawEvidenceSource.CLAUDE_CODE,
        media_type="application/json",
        preview=_observed({"type": "result"}),
        size_bytes=8,
    )
    finished = LifecycleEntry(**_entry("lifecycle-finished", 22), phase="exited")
    return (
        started,
        message,
        reasoning,
        encrypted_reasoning,
        hidden_reasoning,
        tool,
        failed_tool,
        protocol,
        initialization,
        usage,
        interaction,
        terminal,
        process,
        exited,
        raw,
        provider,
        plan,
        state,
        capture,
        malformed,
        raw_mcp,
        raw_provider,
        finished,
    )


def _runtime(
    kind: str,
) -> DirectTraceInfo | OpenCodeTraceInfo | ClaudeCodeTraceInfo | ACPTraceInfo:
    usage = UsageValue(
        input_tokens=_observed(10),
        output_tokens=_observed(5),
        cache_read_tokens=_observed(2),
        cost=_observed(0.01),
        currency=_observed("USD"),
    )
    usage_observation = _observed(usage)
    if kind == "direct":
        return DirectTraceInfo(
            transport=_observed(TransportKind.STDIO),
            protocol=_observed("mcp"),
            initialization=_observed(
                InitializationValue(protocol_version=_observed("2025-11-25"))
            ),
        )
    if kind == "opencode":
        return OpenCodeTraceInfo(
            session_id=_observed("opencode-session"),
            provider_id=_observed("provider"),
            model_id=_observed("model"),
            finish_reason=_observed("stop"),
            http_lifecycle=_observed({"status": 200}),
            usage=usage_observation,
        )
    if kind == "claude_code":
        return ClaudeCodeTraceInfo(
            session_id=_observed("claude-session"),
            model_id=_observed("claude-model"),
            result_subtype=_observed("success"),
            stop_reason=_observed("end_turn"),
            service_tier=_observed("standard"),
            api_duration_ms=_observed(12.0),
            encrypted_reasoning=_observed(True),
            usage=usage_observation,
        )
    return ACPTraceInfo(
        session_id=_observed("acp-session"),
        protocol_version=_observed("1"),
        agent_identity=_observed({"name": "fixture-agent"}),
        available_modes=_observed([{"id": "fast"}]),
        current_mode=_observed("fast"),
        config_options=_observed([{"id": "quality"}]),
        selected_config=_observed({"quality": "high"}),
        plan_state_available=_observed(True),
        usage=Observation(
            state=ObservationState.UNSUPPORTED,
            reason=ObservationReason.HARNESS_UNSUPPORTED,
        ),
    )


@pytest.mark.parametrize("runtime_kind", ["direct", "opencode", "claude_code", "acp"])
@pytest.mark.parametrize("outcome", list(ExecutionOutcome))
def test_public_trace_view_is_a_stable_ui_compatibility_surface(
    runtime_kind: str, outcome: ExecutionOutcome
) -> None:
    limitations = () if outcome is ExecutionOutcome.COMPLETED else ("terminal outcome",)
    view = TraceView(
        trace_id=TraceId("ui-trace"),
        execution_id=ExecutionId("ui-execution"),
        outcome=outcome,
        completeness="complete" if not limitations else "partial",
        limitations=limitations,
        runtime=_runtime(runtime_kind),
        summary=TraceSummary(
            timing=TraceTiming(start_offset_ms=0, end_offset_ms=23, duration_ms=23),
            usage=_observed(
                UsageValue(
                    input_tokens=_observed(10),
                    output_tokens=_observed(5),
                    cache_read_tokens=_observed(2),
                    cost=_observed(0.01),
                    currency=_observed("USD"),
                )
            ),
            turn_count=1,
            message_count=1,
            reasoning_count=3,
            tool_call_count=2,
            successful_tool_call_count=1,
            failed_tool_call_count=1,
        ),
        timeline=_timeline(),
    )
    restored = TraceView.model_validate(view.model_dump(mode="json"))
    assert restored == view
    assert restored.runtime.kind == runtime_kind
    assert restored.trace_id.root == "ui-trace"
    assert restored.execution_id.root == "ui-execution"
    assert restored.outcome is outcome
    assert restored.completeness == (
        "complete" if outcome is ExecutionOutcome.COMPLETED else "partial"
    )
    assert restored.limitations == (
        () if outcome is ExecutionOutcome.COMPLETED else ("terminal outcome",)
    )
    assert all(
        entry.execution_id.root == "ui-execution"
        and entry.session_id is not None
        and entry.session_id.root == "ui-session"
        and entry.turn_id is not None
        and entry.turn_id.root == "ui-turn"
        for entry in restored.timeline
    )
    assert restored.messages and restored.reasoning and restored.tool_calls
    assert restored.messages[0].message_id.value == "message-1"
    assert isinstance(restored.messages[0].content[0], TextContent)
    assert restored.messages[0].content[0].text == "answer"
    assert restored.reasoning[0].content.state is ObservationState.OBSERVED
    assert restored.reasoning[1].content.state is ObservationState.ENCRYPTED
    assert restored.reasoning[2].content.state is ObservationState.PROVIDER_HIDDEN
    assert restored.tool_calls[0].wire.state is ObservationState.OBSERVED
    assert restored.tool_calls[0].reported.state is ObservationState.OBSERVED
    assert restored.tool_calls[0].conflicts[0].field == "arguments"
    assert restored.tool_calls[0].wire.value is not None
    assert restored.tool_calls[0].wire.value.arguments.value == {"zone": "local"}
    assert restored.tool_calls[0].result.value is not None
    assert restored.tool_calls[0].result.value.structured_content.value == {
        "currency": "USD"
    }
    assert restored.tool_calls[1].tool_status is ToolCallStatus.TOOL_ERROR
    assert restored.tool_calls[1].result.value is not None
    assert restored.tool_calls[1].result.value.is_error is True
    assert restored.protocol[0].http.state is ObservationState.OBSERVED
    assert restored.protocol[0].method.value == "tools/call"
    initialization = next(
        entry for entry in restored.timeline if isinstance(entry, InitializationEntry)
    )
    assert initialization.protocol_version.value == "2025-11-25"
    assert initialization.server_name.value == "example-mcp"
    usage_entry = next(
        entry for entry in restored.timeline if isinstance(entry, UsageEntry)
    )
    assert usage_entry.input_tokens.value == 10
    assert usage_entry.output_tokens.value == 5
    assert usage_entry.cost.value == 0.01
    assert usage_entry.currency.value == "USD"
    assert restored.summary.timing.duration_ms == 23
    assert restored.tool_calls[0].server_latency_ms.value == 3.0
    assert restored.raw_messages[0].preview.state is ObservationState.REDACTED
    assert restored.raw_messages[0].preview.value == "[REDACTED]"
    assert restored.raw_messages[1].source is RawEvidenceSource.MCP
    assert restored.raw_messages[2].source is RawEvidenceSource.CLAUDE_CODE
    assert restored.processes[0].pid.value == 42
    assert restored.processes[0].exit_code.state is ObservationState.NOT_EMITTED
    assert restored.processes[1].exit_code.value == 0
    assert restored.interactions[0].interaction_kind == "filesystem.read.request"
    assert restored.interactions[1].interaction_kind == "terminal.output.response"
    assert {
        entry.category
        for entry in restored.timeline
        if isinstance(entry, ProviderEntry)
    } >= {
        "result",
        "plan",
        "state",
    }
    assert (
        next(
            entry.data
            for entry in restored.timeline
            if isinstance(entry, ProviderEntry) and entry.category == "limits"
        ).state
        is ObservationState.TRUNCATED
    )
    assert (
        next(
            entry.data
            for entry in restored.timeline
            if isinstance(entry, ProviderEntry) and entry.category == "malformed"
        ).reason
        is ObservationReason.MALFORMED_SOURCE
    )
    assert [
        entry.phase for entry in restored.timeline if isinstance(entry, LifecycleEntry)
    ] == [
        "started",
        "exited",
    ]
    runtime = restored.runtime
    if isinstance(runtime, DirectTraceInfo):
        assert runtime_kind == "direct"
        assert runtime.transport.value is TransportKind.STDIO
        assert runtime.initialization.value is not None
        assert runtime.initialization.value.protocol_version.value == "2025-11-25"
    elif isinstance(runtime, OpenCodeTraceInfo):
        assert runtime_kind == "opencode"
        assert runtime.provider_id.value == "provider"
        assert runtime.model_id.value == "model"
        assert runtime.http_lifecycle.value == {"status": 200}
        assert runtime.usage.value is not None
        assert runtime.usage.value.cost.value == 0.01
    elif isinstance(runtime, ClaudeCodeTraceInfo):
        assert runtime_kind == "claude_code"
        assert runtime.model_id.value == "claude-model"
        assert runtime.stop_reason.value == "end_turn"
        assert runtime.encrypted_reasoning.value is True
        assert runtime.usage.value is not None
        assert runtime.usage.value.cache_read_tokens.value == 2
        assert runtime.api_duration_ms.value == 12.0
    else:
        assert isinstance(runtime, ACPTraceInfo)
        assert runtime_kind == "acp"
        assert runtime.available_modes.value is not None
        modes = cast(Any, runtime.available_modes.value)
        assert modes[0]["id"] == "fast"
        assert runtime.current_mode.value == "fast"
        assert runtime.config_options.value is not None
        configs = cast(Any, runtime.config_options.value)
        assert configs[0]["id"] == "quality"
        assert runtime.selected_config.value is not None
        selected = cast(Any, runtime.selected_config.value)
        assert selected["quality"] == "high"
        assert runtime.plan_state_available.value is True
        assert runtime.usage.state is ObservationState.UNSUPPORTED
