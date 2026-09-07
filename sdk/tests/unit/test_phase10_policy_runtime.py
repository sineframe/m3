from __future__ import annotations

from collections.abc import Mapping

import pytest

from mcp_pal.agent_session import AdapterTurn, AsyncAgentSession
from mcp_pal.errors import UnsupportedFeature
from mcp_pal.policy import ToolDescriptor, ToolPolicyEvidence
from mcp_pal.server_group import ServerGroupManager
from mcp_pal.types import (
    ACPAgent,
    ActivityHealth,
    AgentSpec,
    ErrorCode,
    EventKind,
    RestrictiveToolPolicy,
    ServerBinding,
    StdioServer,
    TextContent,
    TurnResponse,
)


class ReportingAdapter:
    name = "fixture"

    def __init__(self, tool_call: Mapping[str, object]) -> None:
        self.tool_call = dict(tool_call)
        self.last_policy_evidence = ToolPolicyEvidence(
            requested="restrictive", enforced="portable", observed="preflight"
        )
        self.opened = False

    async def preflight(self, _launch: object) -> object:
        from mcp_pal.types import Readiness

        return Readiness(ready=True)

    async def open(self, _launch: object) -> "ReportingAdapter":
        self.opened = True
        return self

    async def start(self, _spec: AgentSpec) -> None:
        self.opened = True

    async def send(self, message, *, timeout=None, metadata=None) -> AdapterTurn:
        del timeout, metadata
        return AdapterTurn(
            response=TurnResponse(content=(TextContent(text=message.content[0].text),)),
            tool_calls=(self.tool_call,),
        )

    async def close(self) -> None:
        return None


def _spec(policy: RestrictiveToolPolicy) -> AgentSpec:
    return AgentSpec(
        harness=ACPAgent(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="fixture")),),
        tool_policy=policy,
    )


@pytest.mark.asyncio
async def test_disallowed_reported_tool_fails_turn_and_emits_safe_policy_evidence() -> None:
    events: list[tuple[EventKind, Mapping[str, object]]] = []
    adapter = ReportingAdapter({"server": "fixture", "tool": "blocked", "arguments": {"secret": "never"}})

    def sink(kind, payload, _session, _turn, _phase) -> None:
        events.append((kind, payload))

    async with AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("fixture:allowed",))),
        adapter,
        event_sink=sink,
    ) as session:
        result = await session.send("run")

    assert result.error is not None and result.error.code is ErrorCode.UNSUPPORTED
    assert result.error.message == "tool policy violation"
    assert "never" not in repr(events)
    assert any(
        kind is EventKind.TOOL_RESULT_RECEIVED and payload.get("policy_violation") is True
        for kind, payload in events
    )
    # Policy outcome is deliberately separate from adapter-reported MCP
    # activity health.
    assert session.result.activity_health is ActivityHealth.ALL_SUCCEEDED


@pytest.mark.asyncio
async def test_allowed_reported_qualified_tool_preserves_turn_and_health() -> None:
    adapter = ReportingAdapter({"server": "fixture", "tool": "allowed"})
    async with AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("fixture:allowed",))),
        adapter,
    ) as session:
        result = await session.send("run")
    assert result.error is None
    assert result.response is not None
    assert session.result.activity_health is ActivityHealth.ALL_SUCCEEDED


def test_advisory_identity_requires_a_stable_call_from_the_same_turn() -> None:
    adapter = ReportingAdapter({"kind": "tool_call"})
    session = AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("fixture:allowed",))),
        adapter,
    )
    session._tool_policy_evidence = adapter.last_policy_evidence
    raw = AdapterTurn(
        response=TurnResponse(content=(TextContent(text="done"),)),
        tool_calls=({"kind": "tool_call"},),
    )
    stable = (ToolDescriptor(server="fixture", name="allowed"),)
    assert session._evaluate_reported_tool_calls(raw, captured_tool_calls=stable) == ()
    violations = session._evaluate_reported_tool_calls(raw, captured_tool_calls=())
    assert violations[0]["reason"] == "tool_identity_unavailable"


def test_explicit_provider_native_tool_is_not_evaluated_as_mcp_traffic() -> None:
    adapter = ReportingAdapter(
        {"server": None, "tool": "provider_read", "call_id": "native-1"}
    )
    session = AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("fixture:allowed",))),
        adapter,
    )
    session._tool_policy_evidence = adapter.last_policy_evidence
    raw = AdapterTurn(
        response=TurnResponse(content=(TextContent(text="done"),)),
        tool_calls=(
            {"server": None, "tool": "provider_read", "call_id": "native-1"},
        ),
    )

    assert session._evaluate_reported_tool_calls(raw) == ()


def test_unqualified_reported_tool_without_server_field_still_fails_closed() -> None:
    adapter = ReportingAdapter({"tool": "provider_read", "call_id": "unknown-1"})
    session = AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("fixture:allowed",))),
        adapter,
    )
    session._tool_policy_evidence = adapter.last_policy_evidence
    raw = AdapterTurn(
        response=TurnResponse(content=(TextContent(text="done"),)),
        tool_calls=({"tool": "provider_read", "call_id": "unknown-1"},),
    )

    violations = session._evaluate_reported_tool_calls(raw)
    assert len(violations) == 1
    assert violations[0]["reason"] == "tool_identity_invalid"


def test_two_anonymous_updates_correlate_to_two_same_turn_stable_calls() -> None:
    adapter = ReportingAdapter({"kind": "tool_call"})
    session = AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("fixture:first", "fixture:second"))),
        adapter,
    )
    session._tool_policy_evidence = adapter.last_policy_evidence
    raw = AdapterTurn(
        response=TurnResponse(content=(TextContent(text="done"),)),
        tool_calls=({"kind": "tool_call"}, {"kind": "tool_call"}),
    )
    stable = (
        ToolDescriptor(server="fixture", name="first"),
        ToolDescriptor(server="fixture", name="second"),
    )
    assert session._evaluate_reported_tool_calls(raw, captured_tool_calls=stable) == ()


@pytest.mark.asyncio
async def test_allowing_policy_without_preflight_proof_fails_before_opening() -> None:
    class NoProof(ReportingAdapter):
        async def preflight(self, _launch: object) -> object:
            raise AssertionError("preflight should not be available")

    adapter = NoProof({"server": "fixture", "tool": "allowed"})
    adapter.preflight = None  # type: ignore[assignment]
    session = AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("fixture:allowed",))),
        adapter,
    )
    with pytest.raises(UnsupportedFeature):
        await session.__aenter__()
    assert adapter.opened is False


@pytest.mark.asyncio
async def test_duplicate_unqualified_tool_and_unknown_server_are_violations() -> None:
    bindings = (
        ServerBinding(server=StdioServer(name="one", command="one")),
        ServerBinding(server=StdioServer(name="two", command="two")),
    )
    manager = ServerGroupManager(bindings)
    await manager.start()
    manager.register_tools("one", ("read",))
    manager.register_tools("two", ("read",))
    ambiguous_spec = AgentSpec(
        harness=ACPAgent(model="fixture"),
        servers=bindings,
        tool_policy=RestrictiveToolPolicy(allowed_tools=("read",)),
    )
    adapter = ReportingAdapter({"name": "read"})
    async with AsyncAgentSession(ambiguous_spec, adapter, server_manager=manager) as session:
        ambiguous = await session.send("run")
    assert ambiguous.error is not None and ambiguous.error.code is ErrorCode.UNSUPPORTED

    manager2 = ServerGroupManager((ServerBinding(server=StdioServer(name="one", command="one")),))
    unknown_adapter = ReportingAdapter({"server": "missing", "tool": "read"})
    async with AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("missing:read",))),
        unknown_adapter,
        server_manager=manager2,
    ) as session2:
        unknown = await session2.send("run")
    assert unknown.error is not None and unknown.error.code is ErrorCode.UNSUPPORTED
