"""Native provenance must never conceal real MCP traffic or a completed answer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from m3._types.specs import AgentSpec
from m3.agent_session import AdapterTurn, AsyncAgentSession
from m3.execution_trace import ExecutionTraceRecorder
from m3.harness.codex import CodexHarnessAdapter
from m3.harness.contracts import HarnessLaunch
from m3.harness.observations import (
    MessageChunkObservation,
    ToolCallObservedObservation,
    TurnEvidence,
)
from m3.policy import ToolDescriptor, ToolPolicyEvidence
from m3.server_group import HarnessServerConfig, ServerGroupSnapshot, ServerRecord
from m3.storage import InMemoryExecutionStore, SQLiteExecutionStore
from m3.types import (
    Codex,
    EventKind,
    FullToolPolicy,
    RestrictiveToolPolicy,
    ServerBinding,
    StdioServer,
    TextContent,
    TransportKind,
    TurnOutcome,
    TurnResponse,
)


def _launch(server: str = "slow") -> HarnessLaunch:
    spec = AgentSpec(
        harness=Codex(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name=server, command="fixture")),),
        tool_policy=FullToolPolicy(acknowledge_risk=True),
    )
    config = HarnessServerConfig(
        key=server,
        transport=TransportKind.STDIO,
        required=True,
        available=True,
        connection_id="fixture",
        command="fixture",
    )
    snapshot = ServerGroupSnapshot(
        records=(
            ServerRecord(
                key=server,
                server=spec.servers[0].server,
                required=True,
                available=True,
                connection_id="fixture",
                tools=(),
            ),
        )
    )
    return HarnessLaunch(spec, snapshot, (config,), spec.tool_policy)


def _decode(
    tool: str, server: str | None, bound: str = "slow"
) -> tuple[dict[str, Any], ToolCallObservedObservation]:
    adapter = CodexHarnessAdapter(executable="fixture")
    adapter._launch = _launch(bound)
    observations: list[Any] = []
    _, _, calls = adapter.consume_frame(
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "mcpToolCall",
                    "id": "call-1",
                    "server": server,
                    "tool": tool,
                    "arguments": {},
                    "status": "completed",
                }
            },
        },
        1,
        datetime.now(timezone.utc),
        0.0,
        observations,
    )
    return dict(calls[0]), next(
        item for item in observations if isinstance(item, ToolCallObservedObservation)
    )


@pytest.mark.parametrize("tool", ["list_mcp_resources", "list_mcp_resource_templates"])
@pytest.mark.asyncio
async def test_codex_native_introspection_has_explicit_protocol_provenance(
    tool: str,
) -> None:
    call, observation = _decode(tool, "codex")
    assert call["provenance"] == "harness_builtin"
    assert observation.provenance == "harness_builtin"
    # Preserve the provider's reported namespace for trace compatibility.
    assert call["server"] == observation.server == "codex"


@pytest.mark.parametrize(
    "tool,server,bound",
    [
        ("list_mcp_resources", "other", "slow"),
        ("lookup", "codex", "slow"),
        ("list_mcp_resources", "codex", "codex"),
    ],
)
@pytest.mark.asyncio
async def test_codex_names_cannot_override_mcp_origin(
    tool: str, server: str, bound: str
) -> None:
    call, observation = _decode(tool, server, bound)
    assert call["provenance"] == observation.provenance == "mcp"


@pytest.mark.asyncio
async def test_malformed_codex_mcp_identity_is_unknown_not_native() -> None:
    call, observation = _decode("list_mcp_resources", None)
    assert call["provenance"] == observation.provenance == "unknown"


class _Manager:
    capture = None

    def __init__(self, launch: HarnessLaunch) -> None:
        self.launch = launch

    async def start(self) -> ServerGroupSnapshot:
        return self.launch.servers

    def snapshot(self) -> ServerGroupSnapshot:
        return self.launch.servers

    def configurations(self) -> tuple[HarnessServerConfig, ...]:
        return self.launch.configurations

    async def close(self) -> None:
        pass


class _Adapter:
    name = "codex"
    last_policy_evidence = ToolPolicyEvidence(
        requested="full", enforced="portable", observed="preflight"
    )

    def __init__(self, call: dict[str, Any], typed: bool = False) -> None:
        self.call, self.typed = call, typed

    async def preflight(self, launch: HarnessLaunch) -> object:
        from m3.types import Readiness

        return Readiness(ready=True)

    async def start(self, spec: AgentSpec) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send(self, message: object, **kwargs: Any) -> AdapterTurn:
        evidence = None
        if self.typed:
            evidence = TurnEvidence(
                sequence=1,
                status="completed",
                limitations=("partial_trace",),
                observations=(
                    MessageChunkObservation(
                        observation_id="answer",
                        harness_kind="codex",
                        turn_sequence=1,
                        wall_time=datetime.now(timezone.utc),
                        monotonic_offset_ms=0,
                        text="4",
                        complete=True,
                    ),
                ),
            )
        return AdapterTurn(
            response=TurnResponse(content=(TextContent(text="4"),)),
            tool_calls=(self.call,),
            evidence={"answer_observed": True},
            turn_evidence=evidence,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["list_mcp_resources", "list_mcp_resource_templates"])
async def test_native_introspection_keeps_completed_answer(tool: str) -> None:
    launch = _launch()
    call, _ = _decode(tool, "codex")
    async with AsyncAgentSession(
        launch.spec, _Adapter(call), server_manager=_Manager(launch)
    ) as session:
        result = await session.send("what is 2+2?")
    assert result.snapshot.outcome is TurnOutcome.COMPLETED
    assert result.response is not None and result.response.content[0].text == "4"


@pytest.mark.parametrize(
    "captured",
    [
        (ToolDescriptor(server="slow", name="list_mcp_resources"),),
        (
            ToolDescriptor(server="slow", name="list_mcp_resources"),
            ToolDescriptor(server="other", name="list_mcp_resources"),
        ),
    ],
)
@pytest.mark.asyncio
async def test_capture_overrides_native_classification_without_hiding_ambiguity(
    captured: tuple[ToolDescriptor, ...],
) -> None:
    launch = _launch()
    spec = launch.spec.model_copy(
        update={"tool_policy": RestrictiveToolPolicy(allowed_tools=("slow:allowed",))}
    )
    call, _ = _decode("list_mcp_resources", "codex")
    adapter = _Adapter(call)
    session = AsyncAgentSession(spec, adapter, server_manager=_Manager(launch))
    session._tool_policy_evidence = ToolPolicyEvidence(
        requested="restrictive", enforced="portable", observed="preflight"
    )
    violations = session._evaluate_reported_tool_calls(
        AdapterTurn(tool_calls=(call,)), captured_tool_calls=captured
    )
    assert violations
    assert violations[0]["reason"] == (
        "tool denied by restrictive policy" if len(captured) == 1 else "ambiguous_tool"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
@pytest.mark.parametrize("typed", [False, True])
async def test_unauthorized_mcp_retains_answer_evidence_and_persisted_violation(
    tmp_path: Path, store_kind: str, typed: bool
) -> None:
    launch = _launch()
    call, _ = _decode("list_mcp_resources", "other")
    store = (
        InMemoryExecutionStore()
        if store_kind == "memory"
        else SQLiteExecutionStore(tmp_path / "provenance.sqlite")
    )
    recorder = ExecutionTraceRecorder(store, "provenance-execution")
    try:
        async with AsyncAgentSession(
            launch.spec,
            _Adapter(call, typed),
            server_manager=_Manager(launch),
            trace_recorder=recorder,
        ) as session:
            result = await session.send("what is 2+2?")
        assert result.snapshot.outcome is TurnOutcome.FAILED
        assert result.response is not None and result.response.content[0].text == "4"
        assert result.evidence["answer_observed"] is True
        assert result.error is not None
        assert (
            result.error.details["policy_violations"][0]["reason"]
            == "server_unavailable"
        )
        persisted = store.get_report(recorder.execution_id)
        assert persisted is not None
        turn = persisted.turns[0]
        assert turn.response == result.response
        assert turn.error == result.error
        assert any(
            event.kind is EventKind.ASSISTANT_CONTENT for event in persisted.events
        )
        assert any(
            event.payload.get("policy_violation") is True for event in persisted.events
        )
        if typed:
            assert any(
                event.payload.get("observation_id") == "answer"
                for event in persisted.events
            )
            assert "partial_trace" in session.result.trace.limitations
        if store_kind == "sqlite":
            reopened = SQLiteExecutionStore(tmp_path / "provenance.sqlite")
            try:
                reloaded = reopened.get_report(recorder.execution_id)
                assert (
                    reloaded is not None
                    and reloaded.turns[0].response == result.response
                )
                assert reloaded.turns[0].error == result.error
            finally:
                reopened.close()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_bound_server_overrides_even_explicit_native_claim() -> None:
    launch = _launch("codex")
    spec = launch.spec.model_copy(
        update={"tool_policy": RestrictiveToolPolicy(allowed_tools=("codex:allowed",))}
    )
    call, _ = _decode("list_mcp_resources", "codex", "codex")
    call["provenance"] = "harness_builtin"
    session = AsyncAgentSession(spec, _Adapter(call), server_manager=_Manager(launch))
    session._tool_policy_evidence = ToolPolicyEvidence(
        requested="restrictive", enforced="portable", observed="preflight"
    )
    violations = session._evaluate_reported_tool_calls(AdapterTurn(tool_calls=(call,)))
    assert len(violations) == 1
    assert violations[0]["server"] == "codex"
    assert violations[0]["reason"] == "tool denied by restrictive policy"


@pytest.mark.parametrize(
    "provenance", ["unknown", "mcp", "harness_builtin", ["harness_builtin"]]
)
def test_malformed_native_claim_never_bypasses_policy(provenance: object) -> None:
    launch = _launch()
    call = {"server": None, "tool": "", "provenance": provenance}
    session = AsyncAgentSession(
        launch.spec, _Adapter(call), server_manager=_Manager(launch)
    )
    session._tool_policy_evidence = _Adapter.last_policy_evidence
    violations = session._evaluate_reported_tool_calls(AdapterTurn(tool_calls=(call,)))
    assert len(violations) == 1
    assert violations[0]["reason"] == "tool_identity_invalid"


def test_unknown_serverless_call_is_enforced() -> None:
    launch = _launch()
    call = {"server": None, "tool": "list_mcp_resources", "provenance": "unknown"}
    session = AsyncAgentSession(
        launch.spec, _Adapter(call), server_manager=_Manager(launch)
    )
    session._tool_policy_evidence = _Adapter.last_policy_evidence
    assert session._evaluate_reported_tool_calls(AdapterTurn(tool_calls=(call,)))


@pytest.mark.asyncio
async def test_repeated_capture_identity_is_not_ambiguous() -> None:
    launch = _launch()
    call, _ = _decode("list_mcp_resources", "codex")
    session = AsyncAgentSession(
        launch.spec, _Adapter(call), server_manager=_Manager(launch)
    )
    session._tool_policy_evidence = _Adapter.last_policy_evidence
    captured = (ToolDescriptor(server="slow", name="list_mcp_resources"),) * 2
    assert (
        session._evaluate_reported_tool_calls(
            AdapterTurn(tool_calls=(call,)), captured_tool_calls=captured
        )
        == ()
    )


@pytest.mark.asyncio
async def test_decoded_provenance_is_retained_in_persisted_trace() -> None:
    from m3.harness.observation_sink import HarnessObservationSink

    call, observation = _decode("list_mcp_resources", "codex")
    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(store, "native-provenance")
    sink = HarnessObservationSink(recorder)
    sink.emit(observation)
    events = store.events(recorder.execution_id)
    event = next(
        event for event in events if event.kind is EventKind.TOOL_CALL_REQUESTED
    )
    assert event.payload["provenance"] == call["provenance"] == "harness_builtin"
    assert event.payload["server"] == "codex"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reported", ["native_different_name", "unknown_different_name", "omitted"]
)
async def test_all_captured_identities_enforced_independently_of_reported_names(
    reported: str,
) -> None:
    launch = _launch()
    spec = launch.spec.model_copy(
        update={"tool_policy": RestrictiveToolPolicy(allowed_tools=("slow:allowed",))}
    )
    native, _ = _decode("list_mcp_resources", "codex")
    calls = (
        (native,)
        if reported == "native_different_name"
        else ({"server": "slow", "tool": "allowed", "provenance": "unknown"},)
        if reported == "unknown_different_name"
        else ()
    )
    session = AsyncAgentSession(spec, _Adapter(native), server_manager=_Manager(launch))
    session._tool_policy_evidence = ToolPolicyEvidence(
        requested="restrictive", enforced="portable", observed="preflight"
    )
    violations = session._evaluate_reported_tool_calls(
        AdapterTurn(tool_calls=calls),
        captured_tool_calls=(ToolDescriptor(server="slow", name="blocked"),),
    )
    assert len(violations) == 1
    assert violations[0]["server"] == "slow"
    assert violations[0]["tool"] == "blocked"
    assert violations[0]["reason"] == "tool denied by restrictive policy"


@pytest.mark.asyncio
async def test_unrelated_allowed_captured_mcp_does_not_reclassify_native_introspection() -> (
    None
):
    launch = _launch()
    spec = launch.spec.model_copy(
        update={"tool_policy": RestrictiveToolPolicy(allowed_tools=("slow:allowed",))}
    )
    native, _ = _decode("list_mcp_resources", "codex")
    session = AsyncAgentSession(spec, _Adapter(native), server_manager=_Manager(launch))
    session._tool_policy_evidence = ToolPolicyEvidence(
        requested="restrictive", enforced="portable", observed="preflight"
    )
    assert (
        session._evaluate_reported_tool_calls(
            AdapterTurn(tool_calls=(native,)),
            captured_tool_calls=(ToolDescriptor(server="slow", name="allowed"),),
        )
        == ()
    )


def test_actual_mcp_cannot_escape_binding_check_without_a_server_manager() -> None:
    launch = _launch()
    call = {"server": "other", "tool": "list_mcp_resources", "provenance": "mcp"}
    session = AsyncAgentSession(launch.spec, _Adapter(call))
    session._tool_policy_evidence = _Adapter.last_policy_evidence
    violations = session._evaluate_reported_tool_calls(AdapterTurn(tool_calls=(call,)))
    assert len(violations) == 1 and violations[0]["reason"] == "server_unavailable"


@pytest.mark.asyncio
async def test_authorized_mcp_collision_preserves_completed_answer() -> None:
    launch = _launch("codex")
    call, _ = _decode("list_mcp_resources", "codex", "codex")
    async with AsyncAgentSession(
        launch.spec, _Adapter(call), server_manager=_Manager(launch)
    ) as session:
        result = await session.send("what is 2+2?")
    assert result.snapshot.outcome is TurnOutcome.COMPLETED
    assert result.response is not None and result.response.content[0].text == "4"
