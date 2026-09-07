"""Phase 10 portable tool-policy contracts."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from mcp_pal.errors import UnsupportedFeature
from mcp_pal.agent_session import AsyncAgentSession
from mcp_pal.harness.contracts import DeterministicHarnessAdapter, HarnessAdapterCapabilities, HarnessLaunch
from mcp_pal.policy import ToolDescriptor, ToolPolicyEvaluator
from mcp_pal.server_group import ServerGroupManager, ServerGroupSnapshot, ServerRecord
from mcp_pal.types import TransportKind
from mcp_pal.types import ACPAgent, AgentSpec, FullToolPolicy, NativeToolPolicy, RestrictiveToolPolicy, ServerBinding, StdioServer, TurnResponse, UserMessage


def _evaluator() -> ToolPolicyEvaluator:
    return ToolPolicyEvaluator(
        (
            ToolDescriptor(server="one", name="read"),
            ToolDescriptor(server="one", name="delete", destructive=True),
            ToolDescriptor(server="two", name="read"),
        )
    )


def test_restrictive_policy_defaults_to_deny_and_qualified_allowlists_disambiguate() -> None:
    evaluator = _evaluator()
    default = evaluator.decide(
        RestrictiveToolPolicy(),
        ToolDescriptor(server="one", name="read"),
        harness_name="fixture",
        supports_enforcement=True,
    )
    assert default.allowed is False

    ambiguous = evaluator.decide(
        RestrictiveToolPolicy(allowed_tools=("read",)),
        ToolDescriptor(server="one", name="read"),
        harness_name="fixture",
        supports_enforcement=True,
    )
    assert ambiguous.allowed is False

    qualified = evaluator.decide(
        RestrictiveToolPolicy(allowed_tools=("one:read",)),
        ToolDescriptor(server="one", name="read"),
        harness_name="fixture",
        supports_enforcement=True,
    )
    assert qualified.allowed is True


def test_denylist_overrides_allowlist_and_destructive_tools_need_confirmation() -> None:
    evaluator = _evaluator()
    denied = evaluator.decide(
        RestrictiveToolPolicy(allowed_tools=("one:read",), denied_tools=("one:read",)),
        ToolDescriptor(server="one", name="read"),
        harness_name="fixture",
        supports_enforcement=True,
    )
    assert denied.allowed is False

    destructive = evaluator.decide(
        FullToolPolicy(acknowledge_risk=True),
        ToolDescriptor(server="one", name="delete", destructive=True),
        harness_name="fixture",
        supports_enforcement=True,
    )
    assert destructive.allowed is False and destructive.requires_confirmation is True
    confirmed = evaluator.decide(
        FullToolPolicy(acknowledge_risk=True),
        ToolDescriptor(server="one", name="delete", destructive=True),
        harness_name="fixture",
        supports_enforcement=True,
        confirm=lambda descriptor: descriptor.qualified_name == "one:delete",
    )
    assert confirmed.allowed is True


def test_preflight_fails_closed_for_unsupported_portable_policy() -> None:
    with pytest.raises(UnsupportedFeature):
        _evaluator().preflight(
            RestrictiveToolPolicy(allowed_tools=("one:read",)),
            harness_name="fixture",
            supports_enforcement=False,
        )


def test_native_escape_hatch_requires_matching_harness_and_records_nonportable_evidence() -> None:
    policy = NativeToolPolicy(harness="fixture", policy={"allow": "all"}, nonportable_reason="provider policy")
    evidence = _evaluator().preflight(policy, harness_name="fixture", supports_enforcement=False)
    assert evidence.portable is False
    assert evidence.nonportable_reason == "provider policy"
    with pytest.raises(UnsupportedFeature):
        _evaluator().preflight(policy, harness_name="other", supports_enforcement=True)


def test_invalid_full_policy_is_rejected_by_the_model() -> None:
    with pytest.raises(ValidationError):
        FullToolPolicy()


def test_tool_descriptors_reject_ambiguous_or_controlled_identities() -> None:
    with pytest.raises(ValidationError):
        ToolDescriptor(server="one:two", name="read")
    with pytest.raises(ValidationError):
        ToolDescriptor(server="one", name="read\nsecret")


@pytest.mark.asyncio
async def test_unavailable_server_tools_do_not_make_available_unqualified_tools_ambiguous() -> None:
    spec = AgentSpec(
        harness=ACPAgent(model="fixture"),
        servers=(
            ServerBinding(server=StdioServer(name="available", command="server")),
            ServerBinding(server=StdioServer(name="optional", command="server"), required=False),
        ),
        tool_policy=RestrictiveToolPolicy(allowed_tools=("read",)),
    )
    launch = HarnessLaunch(
        spec,
        ServerGroupSnapshot(
            records=(
                ServerRecord("available", spec.servers[0].server, True, True, "c1", TransportKind.STDIO, tools=("read",)),
                ServerRecord("optional", spec.servers[1].server, False, False, "c2", TransportKind.STDIO, tools=("read",)),
            )
        ),
        (),
        spec.tool_policy,
    )
    adapter = DeterministicHarnessAdapter(name="fixture")
    readiness = await adapter.preflight(launch)
    assert readiness.ready


def test_malformed_allowlist_fails_closed_without_parser_details() -> None:
    decision_policy = RestrictiveToolPolicy(allowed_tools=("one:read:extra",))
    with pytest.raises(UnsupportedFeature, match="tool policy entry is invalid"):
        _evaluator().decide(
            decision_policy,
            ToolDescriptor(server="one", name="read"),
            harness_name="fixture",
            supports_enforcement=True,
        )


@pytest.mark.asyncio
async def test_session_surfaces_portable_policy_preflight_failure_before_open() -> None:
    server = StdioServer(name="memory", command="memory-server")
    spec = AgentSpec(
        harness=ACPAgent(model="fixture"),
        servers=(ServerBinding(server=server),),
        tool_policy=RestrictiveToolPolicy(allowed_tools=("memory:read",)),
    )
    manager = ServerGroupManager(spec.servers)
    adapter = DeterministicHarnessAdapter(
        capabilities=HarnessAdapterCapabilities(name="fixture", supports_tool_policy=False)
    )
    session = AsyncAgentSession(spec, adapter, server_manager=manager)
    with pytest.raises(UnsupportedFeature):
        await session.__aenter__()
    assert adapter.open_count == 0
    assert manager.snapshot().evidence.closed is True


@pytest.mark.asyncio
async def test_adapter_without_policy_preflight_cannot_silently_accept_explicit_allowlist() -> None:
    spec = AgentSpec(
        harness=ACPAgent(model="fixture"),
        servers=(ServerBinding(server=StdioServer(name="memory", command="memory-server")),),
        tool_policy=RestrictiveToolPolicy(allowed_tools=("memory:read",)),
    )

    class LegacyAdapter:
        started = False

        async def start(self, _spec: AgentSpec) -> None:
            self.started = True

        async def send(
            self,
            _message: UserMessage,
            *,
            timeout: float | None = None,
            metadata: Mapping[str, object] | None = None,
        ) -> TurnResponse:
            del timeout, metadata
            return TurnResponse()

        async def close(self) -> None:
            return None

    adapter = LegacyAdapter()
    session = AsyncAgentSession(spec, adapter)
    with pytest.raises(UnsupportedFeature):
        await session.__aenter__()
    assert adapter.started is False
