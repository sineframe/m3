"""Provider-neutral live test used by the two-harness UI gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mcp_pal import EvaluationDecision, EvaluationStatus, expect
from mcp_pal.types import PermissionPolicy, StdioServer

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.mcp_pal]

_ROOT = Path(__file__).parents[1]


@pytest.fixture
def shipping_server() -> StdioServer:
    return StdioServer(
        name="example-mcp",
        command=sys.executable,
        args=(str(_ROOT / "servers" / "example_mcp_server.py"),),
        cwd=str(_ROOT),
    )


def test_selected_agents_choose_shipping_tool(agent, shipping_server) -> None:
    result = agent.run(
        "Use shipping_quote with weight_kg 2 and zone local. Return its quote.",
        server=shipping_server,
        permission_policy=PermissionPolicy(mode="allow"),
    )
    expect(result).to_have_tool_call(
        "shipping_quote", server=shipping_server.name, status="success", count=1
    )
    agent.kit.register_evaluator(
        "live.shipping.v1",
        lambda _context: EvaluationDecision(status=EvaluationStatus.PASSED, score=1.0),
    )
    agent.kit.evaluate(result, "live.shipping.v1")
