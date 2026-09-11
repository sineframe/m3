"""Deterministic ACP harness examples over the real example MCP server."""

from __future__ import annotations

import sys
from pathlib import Path

from mcp_pal import MCPTestKit, StdioServer, expect
from mcp_pal.types import (
    ACPAgent,
    AgentSpec,
    RestrictiveToolPolicy,
    ServerBinding,
    TurnOutcome,
)

_EXAMPLES_ROOT = Path(__file__).parents[1]
_AGENT = _EXAMPLES_ROOT / "servers" / "deterministic_acp_agent.py"
_SERVER = _EXAMPLES_ROOT / "servers" / "example_mcp_server.py"


def _spec() -> AgentSpec:
    server = StdioServer(
        name="example-mcp",
        command=sys.executable,
        args=(str(_SERVER),),
        cwd=str(_EXAMPLES_ROOT),
    )
    return AgentSpec(
        harness=ACPAgent(
            model="deterministic-fixture",
            manifest={
                "command": sys.executable,
                "args": (str(_AGENT),),
                "protocol": "acp",
                "protocol_version": 1,
            },
        ),
        servers=(ServerBinding(server=server, alias="example-mcp"),),
        tool_policy=RestrictiveToolPolicy(
            allowed_tools=("example-mcp:shipping_quote",)
        ),
    )


def test_deterministic_harness_turn_scope_uses_real_mcp_calls() -> None:
    """A local ACP process calls the real example server on two turns."""
    with MCPTestKit(env={}, cwd=str(_EXAMPLES_ROOT)) as kit:
        with kit.agent_session(_spec()) as session:
            first = session.send("Get a local quote", timeout=10)
            second = session.send("Get a regional quote", timeout=10)

        result = session.result
        view = result.trace_view
        assert view is not None
        assert first.snapshot.outcome is TurnOutcome.COMPLETED
        assert second.snapshot.outcome is TurnOutcome.COMPLETED
        assert len(view.tool_calls) == 2

        # The primary matcher intentionally uses the default (wire) evidence
        # source. ACP also exposes a reported-only view below where applicable.
        expect(result).to_have_tool_call(
            "shipping_quote",
            turn=first,
            server="example-mcp",
            arguments={"weight_kg": 2},
            arguments_partial=True,
            result={"structured_content": {"currency": "USD"}},
            result_partial=True,
            status="success",
            count=1,
            argument_predicate=lambda args: args["zone"] == "local",
            predicate=lambda call: call["server"] == "example-mcp",
            max_latency_ms=30_000,
        )
        expect(result).to_have_tool_call(
            "shipping_quote",
            turn=second,
            arguments={"weight_kg": 3, "zone": "regional"},
            result={"structured_content": {"amount": 15.5}},
            result_partial=True,
            count=1,
        )
        expect(result).to_not_have_tool_call("always_fails", turn=first)
        expect(result).to_have_tool_call("shipping_quote", min_count=2, max_count=2)
        # ACP supplies a harness-reported projection as well; ask for it
        # explicitly only when comparing evidence sources.
        expect(result).to_have_tool_call("shipping_quote", evidence="reported", count=2)
        expect(result).to_have_tool_call("shipping_quote", evidence="any", count=2)

        first_view = view.for_turn(first)
        second_view = view.for_turn(second.turn_id)
        assert len(first_view.tool_calls) == 1
        assert len(second_view.tool_calls) == 1
        assert first_view.tool_calls[0].arguments.value == {
            "weight_kg": 2,
            "zone": "local",
        }
        assert second_view.tool_calls[0].arguments.value == {
            "weight_kg": 3,
            "zone": "regional",
        }
