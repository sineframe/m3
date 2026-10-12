from __future__ import annotations

from collections.abc import Mapping

import pytest

from m3._types.specs import AgentSpec
from m3.agent_session import AdapterTurn, AsyncAgentSession
from m3.errors import UnsupportedFeature
from m3.policy import ToolDescriptor, ToolPolicyEvidence
from m3.server_group import ServerGroupManager
from m3.types import (
    ACPAgent,
    ActivityHealth,
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
        from m3.types import Readiness

        return Readiness(ready=True)

    async def open(self, _launch: object) -> ReportingAdapter:
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
async def test_disallowed_reported_tool_fails_turn_and_emits_safe_policy_evidence() -> (
    None
):
    events: list[tuple[EventKind, Mapping[str, object]]] = []
    adapter = ReportingAdapter(
        {"server": "fixture", "tool": "blocked", "arguments": {"secret": "never"}}
    )

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
        kind is EventKind.TOOL_RESULT_RECEIVED
        and payload.get("policy_violation") is True
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
        tool_calls=({"server": None, "tool": "provider_read", "call_id": "native-1"},),
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
async def test_allowed_tool_called_twice_in_one_turn_keeps_the_reply() -> None:
    class Repeating(ReportingAdapter):
        async def send(self, message, *, timeout=None, metadata=None) -> AdapterTurn:
            turn = await super().send(message, timeout=timeout, metadata=metadata)
            return AdapterTurn(
                response=turn.response, tool_calls=(self.tool_call, self.tool_call)
            )

    async with AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("fixture:allowed",))),
        Repeating({"server": "fixture", "tool": "allowed"}),
    ) as session:
        result = await session.send("run")
    assert result.error is None
    assert result.response is not None


def _evaluate(
    policy: RestrictiveToolPolicy,
    calls: tuple[Mapping[str, object], ...],
    captured: tuple[ToolDescriptor, ...] = (),
) -> tuple[dict[str, object], ...]:
    adapter = ReportingAdapter({"kind": "tool_call"})
    session = AsyncAgentSession(_spec(policy), adapter)
    session._tool_policy_evidence = adapter.last_policy_evidence
    raw = AdapterTurn(
        response=TurnResponse(content=(TextContent(text="done"),)), tool_calls=calls
    )
    return session._evaluate_reported_tool_calls(raw, captured_tool_calls=captured)


def test_repeated_allowed_tool_does_not_hide_a_denied_call() -> None:
    allowed = {"server": "fixture", "tool": "allowed"}
    violations = _evaluate(
        RestrictiveToolPolicy(allowed_tools=("fixture:allowed",)),
        (allowed, allowed, {"server": "fixture", "tool": "blocked"}),
    )
    assert [v["reason"] for v in violations] == ["tool denied by restrictive policy"]


def test_repeated_tool_keeps_each_calls_destructive_flag() -> None:
    violations = _evaluate(
        RestrictiveToolPolicy(allowed_tools=("fixture:allowed",)),
        (
            {"server": "fixture", "tool": "allowed"},
            {"server": "fixture", "tool": "allowed", "destructive": True},
        ),
    )
    assert [v["reason"] for v in violations] == [
        "destructive tool requires confirmation"
    ]


def test_anonymous_call_beside_an_identified_call_is_evaluated() -> None:
    violations = _evaluate(
        RestrictiveToolPolicy(allowed_tools=("fixture:first", "fixture:second")),
        ({"server": "fixture", "tool": "first"}, {"kind": "tool_call"}),
        (
            ToolDescriptor(server="fixture", name="first"),
            ToolDescriptor(server="fixture", name="second"),
        ),
    )
    assert violations == ()


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
    async with AsyncAgentSession(
        ambiguous_spec, adapter, server_manager=manager
    ) as session:
        ambiguous = await session.send("run")
    assert ambiguous.error is not None and ambiguous.error.code is ErrorCode.UNSUPPORTED

    manager2 = ServerGroupManager(
        (ServerBinding(server=StdioServer(name="one", command="one")),)
    )
    unknown_adapter = ReportingAdapter({"server": "missing", "tool": "read"})
    async with AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("missing:read",))),
        unknown_adapter,
        server_manager=manager2,
    ) as session2:
        unknown = await session2.send("run")
    assert unknown.error is not None and unknown.error.code is ErrorCode.UNSUPPORTED


def _codex_reported_calls(*items: Mapping[str, object]) -> tuple[dict, ...]:
    """Decode Codex App Server ``mcpToolCall`` items the way a turn reports them."""

    from datetime import datetime, timezone

    from m3.harness.codex import CodexHarnessAdapter

    adapter = CodexHarnessAdapter(executable="codex")
    calls: list[dict] = []
    for index, item in enumerate(items):
        _, _, decoded = adapter.consume_frame(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "mcpToolCall",
                        "id": f"call-{index}",
                        "status": "completed",
                        **item,
                    }
                },
            },
            1,
            datetime.now(timezone.utc),
            0.0,
            [],
        )  # type: ignore[arg-type]
        calls.extend(decoded)
    return tuple(calls)


async def _send_with_bound(
    calls: tuple[Mapping[str, object], ...],
    *,
    server: str,
    tools: tuple[str, ...],
    allowed: tuple[str, ...],
):
    class Reporting(ReportingAdapter):
        async def send(self, message, *, timeout=None, metadata=None) -> AdapterTurn:
            turn = await super().send(message, timeout=timeout, metadata=metadata)
            return AdapterTurn(response=turn.response, tool_calls=calls)

    binding = ServerBinding(server=StdioServer(name=server, command=server))
    manager = ServerGroupManager((binding,))
    await manager.start()
    manager.register_tools(server, tools)
    spec = AgentSpec(
        harness=ACPAgent(model="fixture"),
        servers=(binding,),
        tool_policy=RestrictiveToolPolicy(allowed_tools=allowed),
    )
    async with AsyncAgentSession(
        spec, Reporting({"kind": "tool_call"}), server_manager=manager
    ) as session:
        return await session.send("answer")


@pytest.mark.asyncio
async def test_codex_resource_introspection_is_not_evaluated_as_mcp_tool_traffic() -> (
    None
):
    calls = _codex_reported_calls(
        {"server": "codex", "tool": "list_mcp_resources", "arguments": {}},
        {
            "server": "fixture",
            "tool": "list_mcp_resource_templates",
            "arguments": {"server": "fixture"},
        },
        {
            "server": "fixture",
            "tool": "read_mcp_resource",
            "arguments": {"server": "fixture", "uri": "file:///notes"},
        },
    )
    result = await _send_with_bound(
        calls, server="fixture", tools=("allowed",), allowed=("fixture:allowed",)
    )
    assert result.error is None
    assert result.response is not None


@pytest.mark.asyncio
async def test_introspection_of_an_unbound_server_is_a_violation() -> None:
    calls = _codex_reported_calls(
        {
            "server": "missing",
            "tool": "read_mcp_resource",
            "arguments": {"server": "missing", "uri": "file:///notes"},
        }
    )
    result = await _send_with_bound(
        calls, server="fixture", tools=("allowed",), allowed=("fixture:allowed",)
    )
    assert result.error is not None
    violations = result.error.details["policy_violations"]
    assert [(v["server"], v["reason"]) for v in violations] == [
        ("missing", "server_unavailable")
    ]


@pytest.mark.parametrize(
    "tools",
    [("list_mcp_resources",), ()],
    ids=["advertises-colliding-tool", "unknown-tool-list"],
)
@pytest.mark.asyncio
async def test_bound_server_named_codex_cannot_use_the_introspection_exemption(
    tools: tuple[str, ...],
) -> None:
    calls = _codex_reported_calls(
        {"server": "codex", "tool": "list_mcp_resources", "arguments": {}}
    )
    result = await _send_with_bound(
        calls, server="codex", tools=tools, allowed=("codex:other",)
    )
    assert result.error is not None
    assert result.error.details["policy_violations"][0]["tool"] == (
        "list_mcp_resources"
    )


@pytest.mark.parametrize("gated", [True, False], ids=["wire-gated", "ungated"])
@pytest.mark.asyncio
async def test_unknown_tool_list_exempts_introspection_only_behind_a_wire_gate(
    gated: bool,
) -> None:
    from m3.server_group import ServerRecord

    class Capture:
        def snapshots(self) -> tuple[()]:
            return ()

        def enforces_portable_policy(self, connection_ids) -> bool:
            return gated and tuple(connection_ids) == ("conn-fixture",)

    class Manager:
        capture = Capture()

        def snapshot(self):
            record = ServerRecord(
                key="fixture",
                server=StdioServer(name="fixture", command="fixture"),
                required=True,
                available=True,
                connection_id="conn-fixture",
            )
            return type("Snapshot", (), {"records": (record,)})()

    adapter = ReportingAdapter({"kind": "tool_call"})
    session = AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("fixture:allowed",))), adapter
    )
    session._tool_policy_evidence = adapter.last_policy_evidence
    session._server_manager = Manager()  # type: ignore[assignment]
    raw = AdapterTurn(
        response=TurnResponse(content=(TextContent(text="done"),)),
        tool_calls=_codex_reported_calls(
            {
                "server": "fixture",
                "tool": "read_mcp_resource",
                "arguments": {"server": "fixture", "uri": "file:///notes"},
            }
        ),
    )
    violations = session._evaluate_reported_tool_calls(raw)
    assert (violations == ()) is gated


@pytest.mark.parametrize(
    "item",
    [
        # ``read_mcp_resource`` always names its server in the arguments.
        {"server": "codex", "tool": "read_mcp_resource", "arguments": {}},
        # The reported server must be the one Codex derives from arguments.
        {
            "server": "codex",
            "tool": "list_mcp_resources",
            "arguments": {"server": "fixture"},
        },
        {"server": "fixture", "tool": "list_mcp_resources", "arguments": {}},
    ],
    ids=["read-without-server", "server-argument-mismatch", "unqualified-list"],
)
@pytest.mark.asyncio
async def test_calls_outside_codex_introspection_shape_remain_mcp_traffic(
    item: Mapping[str, object],
) -> None:
    result = await _send_with_bound(
        _codex_reported_calls(item),
        server="fixture",
        tools=("allowed",),
        allowed=("fixture:allowed",),
    )
    assert result.error is not None
    assert result.error.details["policy_violations"]


@pytest.mark.asyncio
async def test_policy_violation_fails_turn_but_retains_and_persists_response(
    tmp_path,
) -> None:
    from m3.execution_trace import ExecutionTraceRecorder
    from m3.storage.sqlite import SQLiteExecutionStore
    from m3.types import TurnOutcome

    database = tmp_path / "results.sqlite"
    recorder = ExecutionTraceRecorder(SQLiteExecutionStore(database), "policy-retain")
    async with AsyncAgentSession(
        _spec(RestrictiveToolPolicy(allowed_tools=("fixture:allowed",))),
        ReportingAdapter({"server": "fixture", "tool": "blocked"}),
        trace_recorder=recorder,
    ) as session:
        result = await session.send("kept answer")

    assert result.snapshot.outcome is TurnOutcome.FAILED
    assert result.error is not None
    assert result.error.message == "tool policy violation"
    assert result.response is not None
    assert result.response.content[0].text == "kept answer"

    ((_, stored),) = SQLiteExecutionStore(database).turns(recorder.execution_id)
    assert stored is not None
    assert stored.snapshot.outcome is TurnOutcome.FAILED
    assert stored.error is not None and stored.error.details["policy_violations"]
    assert stored.response == result.response
