"""R9: the common finalized TraceView contract across local harnesses."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.execution_trace import ExecutionTraceRecorder
from mcp_pal.harness.acp import AcpHarnessAdapter
from mcp_pal.harness.claude import ClaudeCodeHarnessAdapter
from mcp_pal.harness.contracts import HarnessLaunch, HarnessTurnRequest, HarnessTurnResult
from mcp_pal.harness.observation_sink import HarnessObservationSink
from mcp_pal.harness.opencode import OpenCodeHarnessAdapter
from mcp_pal.observability import DirectTraceInfo, ObservationState, TraceView
from mcp_pal.server_group import HarnessServerConfiguration, ServerGroupSnapshot
from mcp_pal.storage import InMemoryExecutionStore, SQLiteExecutionStore
from mcp_pal.sync_api import MCPTestKit
from mcp_pal.testing import FaultInjector
from mcp_pal.types import (
    ACPAgent,
    AgentExecutionSpec,
    CallToolOperation,
    ClaudeCode,
    DirectExecutionSpec,
    ExecutionOutcome,
    OpenCode,
    PingOperation,
    ServerBinding,
    StdioServer,
    TextContent,
    TransportKind,
    TurnId,
    UserMessage,
)


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
MATRIX_SERVER = FIXTURES / "matrix_stdio_server.py"
HANGING_SERVER = FIXTURES / "hanging_stdio_server.py"


def _server() -> ServerBinding:
    return ServerBinding(
        server=StdioServer(
            name="e2e-mcp",
            command=sys.executable,
            args=(str(MATRIX_SERVER),),
            cwd=str(ROOT.parent),
        ),
        alias="e2e-mcp",
    )


def _assert_common(view: TraceView, runtime: str) -> None:
    """Assertions intentionally limited to facts every harness can expose."""
    assert view.schema_id == "mcp_pal.trace_view"
    assert view.outcome is ExecutionOutcome.COMPLETED
    assert view.runtime.kind == runtime
    assert view.trace_id.root
    assert view.execution_id.root
    assert view.timeline
    assert view.summary.timing.end_offset_ms >= view.summary.timing.start_offset_ms >= 0
    assert view.summary.timing.duration_ms >= 0
    assert [entry.sequence_start for entry in view.timeline] == sorted(
        entry.sequence_start for entry in view.timeline
    )
    if runtime != "direct":
        assert view.summary.turn_count >= 1
    for entry in view.timeline:
        assert entry.sequence_end >= entry.sequence_start
        assert entry.timing.end_offset_ms >= entry.timing.start_offset_ms >= 0
        assert entry.provenance


def _acp_spec(mode: str = "recover") -> AgentExecutionSpec:
    return AgentExecutionSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={
                "schema_version": "mcp-pal.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(FIXTURES / "acp_scenario_agent.py"), mode],
                "env": {},
            },
        ),
        servers=(_server(),),
        message=UserMessage(content=(TextContent(text="r9"),)),
    )


def _rich_acp_spec(path: Path) -> AgentExecutionSpec:
    path.write_text(
        """#!/usr/bin/env python3
import json, sys
def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)
for line in sys.stdin:
    request = json.loads(line); method = request.get('method'); ident = request.get('id')
    if method == 'initialize':
        send({'jsonrpc':'2.0','id':ident,'result':{'protocolVersion':1}})
    elif method == 'session/new':
        send({'jsonrpc':'2.0','id':ident,'result':{'sessionId':'rich-r9','modes':{'currentModeId':'fast','availableModes':[{'id':'fast','name':'Fast'}]},'configOptions':[{'type':'select','id':'quality','name':'Quality','currentValue':'normal','options':[{'value':'high','name':'High'}]}]}})
    elif method == 'session/set_mode':
        send({'jsonrpc':'2.0','id':ident,'result':{'modeId':'fast'}})
    elif method == 'session/set_config_option':
        send({'jsonrpc':'2.0','id':ident,'result':{'configOptions':[{'id':'quality','currentValue':'high'}]}})
    elif method == 'session/prompt':
        for update in (
            {'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'answer'}},
            {'sessionUpdate':'agent_thought_chunk','content':{'type':'text','text':'thinking'}},
            {'sessionUpdate':'plan','entries':[{'content':'step','status':'pending'}],'status':'updated'},
            {'sessionUpdate':'current_mode_update','currentModeId':'fast'},
            {'sessionUpdate':'config_option_update','configOptions':[{'id':'quality','currentValue':'high'}]},
        ):
            send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'rich-r9','update':update}})
        send({'jsonrpc':'2.0','id':ident,'result':{'stopReason':'end_turn'}})
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o100)
    return AgentExecutionSpec(
        harness=ACPAgent(
            model="fixture",
            manifest={
                "command": str(path),
                "protocol": "acp",
                "protocol_version": 1,
            },
            agent_mode_id="fast",
            session_config={"quality": "high"},
        ),
        servers=(_server(),),
        message=UserMessage(content=(TextContent(text="rich"),)),
    )


def _claude_spec() -> AgentExecutionSpec:
    executable = str(FIXTURES / "claude_observability_fixture.py")
    return AgentExecutionSpec(
        harness=ClaudeCode(model="fixture", executable=executable),
        servers=(_server(),),
        message=UserMessage(content=(TextContent(text="r9"),)),
    )


def _opencode_spec() -> AgentExecutionSpec:
    executable = str(FIXTURES / "opencode_serve_fixture.py")
    return AgentExecutionSpec(
        harness=OpenCode(model="fixture", executable=executable),
        servers=(_server(),),
        message=UserMessage(content=(TextContent(text="r9"),)),
    )


async def _native_trace(
    spec: AgentExecutionSpec,
    adapter: Any,
    *,
    message: str = "r9",
    timeout_seconds: float | None = None,
    store: Any = None,
    execution_id: str = "r9-native",
) -> tuple[TraceView, HarnessTurnResult]:
    configurations: tuple[HarnessServerConfiguration, ...] = ()
    capture = None
    if isinstance(adapter, AcpHarnessAdapter):
        configurations = (
            HarnessServerConfiguration(
                key="e2e-mcp",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="e2e-mcp",
                command=sys.executable,
                args=(str(MATRIX_SERVER),),
            ),
        )
        capture = SimpleNamespace(
            enforces_portable_policy=lambda values: tuple(values) == ("e2e-mcp",)
        )
    launch = HarnessLaunch(
        spec, ServerGroupSnapshot(), configurations, spec.tool_policy, capture=capture
    )
    session = await adapter.open(launch)
    try:
        turn = await session.send(
            HarnessTurnRequest.from_message(message, timeout_seconds=timeout_seconds)
        )
    finally:
        await session.close()
    assert turn.turn_evidence is not None
    if store is None:
        from mcp_pal.storage import InMemoryExecutionStore

        store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, execution_id)
    sink = HarnessObservationSink(
        recorder, turn_id=TurnId(f"turn-{turn.turn_evidence.sequence}")
    )
    for observation in turn.turn_evidence.observations:
        sink.emit(observation)
    outcome = ExecutionOutcome(turn.status)
    limitations = tuple(
        dict.fromkeys(
            (*turn.trace_limitations, *(turn.turn_evidence.limitations or ()))
            + sink.limitations
        )
    )
    return recorder.finalize(outcome, limitations=limitations).view(), turn


async def _native_view(
    spec: AgentExecutionSpec,
    adapter: Any,
    **kwargs: Any,
) -> TraceView:
    view, _turn = await _native_trace(spec, adapter, **kwargs)
    return view


@pytest.mark.asyncio
async def test_r9_common_finalized_view_covers_all_local_harnesses() -> None:
    direct = DirectExecutionSpec(
        servers=(_server(),),
        operation=CallToolOperation(
            server="e2e-mcp", name="echo", arguments={"text": "r9"}
        ),
    )
    async with AsyncMCPTestKit(env={}, cwd=ROOT.parent) as kit:
        direct_result = await kit.run(direct)
        assert direct_result.trace_view is not None
        _assert_common(direct_result.trace_view, "direct")
        assert isinstance(direct_result.trace_view.runtime, DirectTraceInfo)
        assert direct_result.trace_view.runtime.initialization.state is ObservationState.OBSERVED
        assert direct_result.trace_view.tool_calls
        assert direct_result.trace_view.tool_calls[0].wire.state is ObservationState.OBSERVED

        for spec, runtime, adapter in (
            (_opencode_spec(), "opencode", OpenCodeHarnessAdapter(executable=str(FIXTURES / "opencode_serve_fixture.py"))),
            (_claude_spec(), "claude_code", ClaudeCodeHarnessAdapter(executable=str(FIXTURES / "claude_observability_fixture.py"))),
            (_acp_spec(), "acp", AcpHarnessAdapter()),
        ):
            _assert_common(await _native_view(spec, adapter), runtime)


@pytest.mark.asyncio
async def test_r9_native_source_specific_surfaces_remain_truthful() -> None:
    claude_view = await _native_view(
        _claude_spec(),
        ClaudeCodeHarnessAdapter(executable=str(FIXTURES / "claude_observability_fixture.py")),
    )
    assert claude_view.runtime.kind == "claude_code"
    assert claude_view.reasoning
    assert claude_view.runtime.encrypted_reasoning.state is ObservationState.OBSERVED
    assert claude_view.runtime.usage.state is ObservationState.OBSERVED
    assert claude_view.runtime.stop_reason.state is ObservationState.OBSERVED
    assert claude_view.runtime.usage.value.cache_read_tokens.state is ObservationState.OBSERVED  # type: ignore[union-attr]

    opencode_view = await _native_view(
        _opencode_spec(),
        OpenCodeHarnessAdapter(executable=str(FIXTURES / "opencode_serve_fixture.py")),
    )
    assert opencode_view.runtime.kind == "opencode"
    assert opencode_view.runtime.provider_id.state is ObservationState.OBSERVED
    assert opencode_view.runtime.model_id.state is ObservationState.OBSERVED
    assert opencode_view.runtime.http_lifecycle.state is ObservationState.OBSERVED
    assert opencode_view.runtime.usage.state is ObservationState.OBSERVED
    assert opencode_view.runtime.usage.value.cost.state is ObservationState.OBSERVED  # type: ignore[union-attr]

    acp_view = await _native_view(_acp_spec(), AcpHarnessAdapter())
    assert acp_view.runtime.kind == "acp"
    assert acp_view.runtime.usage.state is ObservationState.UNSUPPORTED
    assert acp_view.runtime.session_id.state is ObservationState.OBSERVED


@pytest.mark.asyncio
async def test_r9_acp_rich_runtime_plan_state_and_modes_are_publicly_typed(
    tmp_path: Path,
) -> None:
    view = await _native_view(_rich_acp_spec(tmp_path / "rich-acp.py"), AcpHarnessAdapter())
    assert view.runtime.kind == "acp"
    assert view.runtime.available_modes.state is ObservationState.OBSERVED
    modes = cast(Any, view.runtime.available_modes.value)
    assert [
        {key: dict(item).get(key) for key in ("id", "name")} for item in modes
    ] == [{"id": "fast", "name": "Fast"}]
    assert view.runtime.current_mode.state is ObservationState.OBSERVED
    assert view.runtime.current_mode.value == "fast"
    assert view.runtime.config_options.state is ObservationState.OBSERVED
    assert view.runtime.selected_config.state is ObservationState.OBSERVED
    assert view.runtime.plan_state_available.state is ObservationState.OBSERVED
    assert view.messages and view.reasoning
    providers = [entry for entry in view.timeline if entry.kind == "provider"]
    assert any(getattr(entry, "category", None) == "plan" for entry in providers)
    assert any(getattr(entry, "category", None) == "state" for entry in providers)


@pytest.mark.asyncio
async def test_r9_tool_source_parity_keeps_wire_reported_and_builtin_identity() -> None:
    direct = DirectExecutionSpec(
        servers=(_server(),),
        operation=CallToolOperation(
            server="e2e-mcp", name="echo", arguments={"text": "wire"}
        ),
    )
    async with AsyncMCPTestKit(env={}, cwd=ROOT.parent) as kit:
        direct_result = await kit.run(direct)
    assert direct_result.trace_view is not None
    direct_call = direct_result.trace_view.tool_calls[0]
    assert direct_call.wire.state is ObservationState.OBSERVED
    assert direct_call.reported.state is ObservationState.NOT_EMITTED

    builtin_spec = _claude_spec().model_copy(
        update={"harness": ClaudeCode(
            model="fixture",
            executable=str(FIXTURES / "claude_partial_observability_fixture.py"),
        )}
    )
    builtin = await _native_view(
        builtin_spec,
        ClaudeCodeHarnessAdapter(
            executable=str(FIXTURES / "claude_partial_observability_fixture.py")
        ),
        message="builtin",
    )
    assert builtin.tool_calls
    assert builtin.tool_calls[0].server.state is ObservationState.NOT_EMITTED
    assert builtin.tool_calls[0].tool.value == "Read_File"


@pytest.mark.asyncio
async def test_r9_reused_provider_call_ids_remain_distinct_across_turns() -> None:
    spec = _acp_spec()
    launch = HarnessLaunch(
        spec,
        ServerGroupSnapshot(),
        (
            HarnessServerConfiguration(
                key="e2e-mcp",
                transport=TransportKind.STDIO,
                required=True,
                available=True,
                connection_id="e2e-mcp",
                command=sys.executable,
                args=(str(MATRIX_SERVER),),
            ),
        ),
        spec.tool_policy,
        capture=SimpleNamespace(
            enforces_portable_policy=lambda values: tuple(values) == ("e2e-mcp",)
        ),
    )
    adapter = AcpHarnessAdapter()
    session = await adapter.open(launch)
    recorder = ExecutionTraceRecorder(InMemoryExecutionStore(), "r9-reused")
    try:
        for sequence in (1, 2, 3):
            turn = await session.send(HarnessTurnRequest.from_message(f"turn-{sequence}"))
            assert turn.turn_evidence is not None
            sink = HarnessObservationSink(recorder, turn_id=TurnId(f"turn-{sequence}"))
            for observation in turn.turn_evidence.observations:
                sink.emit(observation)
    finally:
        await session.close()
    view = recorder.finalize(ExecutionOutcome.COMPLETED).view()
    calls = [call for call in view.tool_calls if call.provider_call_id.value == "scenario-call"]
    assert len(calls) == 3
    assert all(call.turn_id is not None for call in calls)
    assert {call.turn_id.root for call in calls if call.turn_id is not None} == {
        "turn-1",
        "turn-2",
        "turn-3",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("harness", "mode", "message", "timeout_seconds", "expected"),
    [
        ("opencode", "socket-close", "failure", 2.0, ExecutionOutcome.FAILED),
        ("opencode", "blocking-message", "timeout", 0.05, ExecutionOutcome.TIMED_OUT),
        ("claude", None, "sleep", 0.05, ExecutionOutcome.TIMED_OUT),
        ("acp", "loss", "loss", 2.0, ExecutionOutcome.FAILED),
    ],
    ids=("opencode-failed", "opencode-timeout", "claude-timeout", "acp-failed"),
)
async def test_r9_native_terminal_turns_finalize_and_reopen(
    tmp_path: Path,
    harness: str,
    mode: str | None,
    message: str,
    timeout_seconds: float,
    expected: ExecutionOutcome,
) -> None:
    if harness == "opencode":
        spec = _opencode_spec()
        adapter: Any = OpenCodeHarnessAdapter(
            executable=str(FIXTURES / "opencode_serve_fixture.py"),
            environment={"MCP_PAL_OPENCODE_MODE": mode or "normal"},
        )
    elif harness == "claude":
        spec = _claude_spec().model_copy(
            update={"harness": ClaudeCode(model="fixture", executable=str(FIXTURES / "claude_stream_fixture.py"))}
        )
        adapter = ClaudeCodeHarnessAdapter(executable=str(FIXTURES / "claude_stream_fixture.py"))
    else:
        spec = _acp_spec(mode or "recover")
        adapter = AcpHarnessAdapter()
    store = SQLiteExecutionStore(tmp_path / f"{harness}-{expected.value}.sqlite")
    closed = False
    try:
        view, turn = await _native_trace(
            spec,
            adapter,
            message=message,
            timeout_seconds=timeout_seconds,
            store=store,
            execution_id=f"r9-{harness}-{expected.value}",
        )
        assert turn.status == expected.value
        assert view.outcome is expected
        assert view.completeness in {"complete", "partial"}
        assert view.timeline
        execution_id = view.execution_id
        store.close()
        reopened = SQLiteExecutionStore(tmp_path / f"{harness}-{expected.value}.sqlite")
        try:
            restored = reopened.get_trace_view(execution_id)
            assert restored == view
            assert restored.outcome is expected
            for raw in restored.raw_messages:
                if raw.evidence_ref is not None:
                    assert reopened.read_raw_evidence(raw.evidence_ref).redacted is True
        finally:
            reopened.close()
            closed = True
    finally:
        if not closed:
            store.close()


async def _run_async_direct(spec: DirectExecutionSpec) -> TraceView:
    async with AsyncMCPTestKit(env={}, cwd=ROOT.parent) as kit:
        result = await kit.run(spec)
    assert result.trace_view is not None
    return result.trace_view


def test_r9_direct_sync_async_public_views_have_matching_semantics() -> None:
    spec = DirectExecutionSpec(
        servers=(_server(),),
        operation=CallToolOperation(
            server="e2e-mcp", name="echo", arguments={"text": "sync-async"}
        ),
    )
    with MCPTestKit(env={}, cwd=ROOT.parent) as kit:
        sync_result = kit.run(spec)
    assert sync_result.trace_view is not None
    async_view = asyncio.run(_run_async_direct(spec))
    assert sync_result.trace_view.runtime.kind == async_view.runtime.kind == "direct"
    assert sync_result.trace_view.outcome is async_view.outcome is ExecutionOutcome.COMPLETED
    assert sync_result.trace_view.summary.tool_call_count == async_view.summary.tool_call_count == 1
    assert sync_result.trace_view.tool_calls[0].tool == async_view.tool_calls[0].tool


@pytest.mark.parametrize(
    "case", ["completed", "failed", "timed_out"], ids=lambda value: f"direct-{value}"
)
def test_r9_direct_terminal_outcomes_persist_and_reopen(
    tmp_path: Path, case: str
) -> None:
    if case == "completed":
        spec = DirectExecutionSpec(
            servers=(_server(),),
            operation=CallToolOperation(
                server="e2e-mcp", name="echo", arguments={"text": "terminal"}
            ),
        )
    elif case == "failed":
        spec = DirectExecutionSpec(
            servers=(
                ServerBinding(
                    server=StdioServer(name="missing", command="r9-no-such-command"),
                    alias="missing",
                ),
            ),
            operation=PingOperation(server="missing"),
        )
    else:
        spec = DirectExecutionSpec(
            servers=(
                ServerBinding(
                    server=FaultInjector().delay("tools/call", 0.5).stdio_server(),
                    alias="slow",
                ),
            ),
            operation=CallToolOperation(server="slow", name="echo", arguments={}),
            timeout_seconds=0.05,
        )
    database = tmp_path / f"direct-{case}.sqlite"
    store = SQLiteExecutionStore(database)
    with MCPTestKit(store=store, env={}, cwd=ROOT.parent) as kit:
        result = kit.run(spec)
    assert result.trace_view is not None
    expected = {
        "completed": ExecutionOutcome.COMPLETED,
        "failed": ExecutionOutcome.FAILED,
        "timed_out": ExecutionOutcome.TIMED_OUT,
    }[case]
    assert result.snapshot.outcome is expected
    assert result.trace_view.outcome is expected
    assert result.trace_view.timeline
    execution_id = result.snapshot.execution_id
    store.close()
    reopened = SQLiteExecutionStore(database)
    try:
        restored = reopened.get_trace_view(execution_id)
        assert restored == result.trace_view
        assert restored.outcome is expected
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_r9_direct_cancelled_trace_persists_and_reopens(tmp_path: Path) -> None:
    marker = tmp_path / "hanging.pid"
    server = StdioServer(
        name="hanging",
        command=sys.executable,
        args=(str(HANGING_SERVER),),
        cwd=str(ROOT.parent),
        environment={"MCP_PAL_E2E_PID_FILE": str(marker)},
    )
    spec = DirectExecutionSpec(
        servers=(ServerBinding(server=server, alias="hanging"),),
        operation=PingOperation(server="hanging"),
        timeout_seconds=30,
    )
    database = tmp_path / "direct-cancelled.sqlite"
    store = SQLiteExecutionStore(database)
    kit = AsyncMCPTestKit(store=store, env={}, cwd=ROOT.parent)
    try:
        handle = kit.submit(spec)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        await handle.cancel()
        result = await handle.result(timeout=5)
    finally:
        await kit.aclose()
    assert result.trace_view is not None
    assert result.snapshot.outcome is ExecutionOutcome.CANCELLED
    assert result.trace_view.outcome is ExecutionOutcome.CANCELLED
    assert result.trace_view.completeness == "partial"
    execution_id = result.snapshot.execution_id
    store.close()
    reopened = SQLiteExecutionStore(database)
    try:
        restored = reopened.get_trace_view(execution_id)
        assert restored == result.trace_view
        assert restored.outcome is ExecutionOutcome.CANCELLED
        assert restored.limitations
    finally:
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", ["opencode", "claude", "acp"])
async def test_r9_sqlite_reopen_preserves_each_native_finalized_view(
    tmp_path: Path, harness: str
) -> None:
    database = tmp_path / "r9.sqlite"
    store = SQLiteExecutionStore(database, blob_root=tmp_path / "blobs")
    spec, adapter = {
        "opencode": (
            _opencode_spec(),
            OpenCodeHarnessAdapter(executable=str(FIXTURES / "opencode_serve_fixture.py")),
        ),
        "claude": (
            _claude_spec(),
            ClaudeCodeHarnessAdapter(executable=str(FIXTURES / "claude_observability_fixture.py")),
        ),
        "acp": (_acp_spec(), AcpHarnessAdapter()),
    }[harness]
    first = await _native_view(
        spec,
        adapter,
        store=store,
        execution_id=f"r9-sqlite-{harness}",
    )
    execution_id = first.execution_id
    assert first.raw_messages
    store.close()

    reopened = SQLiteExecutionStore(database, blob_root=tmp_path / "blobs")
    try:
        second = reopened.get_trace_view(execution_id)
        assert second == first
        assert second.outcome is ExecutionOutcome.COMPLETED
        if second.raw_messages:
            raw = second.raw_messages[0]
            assert raw.preview.state in {
                ObservationState.OBSERVED,
                ObservationState.REDACTED,
                ObservationState.TRUNCATED,
            }
            if raw.evidence_ref is not None:
                assert reopened.read_raw_evidence(raw.evidence_ref).redacted is True
    finally:
        reopened.close()
