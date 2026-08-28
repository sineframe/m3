"""Opt-in live harness example: let OpenCode choose a real MCP tool.

Run this separately from the deterministic catalog:

    set -a; source .env; set +a
    MCP_PAL_RUN_LIVE_OPENCODE=1 uv run --project sdk --all-extras \
      pytest -q sdk/examples/tests/test_live_opencode.py

The provider response is nondeterministic and may incur cost. The assertion
is intentionally narrow: the harness must use the allowed tool with the
requested semantic argument and the captured wire result must succeed.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from mcp_pal.types import (
    AgentExecutionSpec,
    OpenCode,
    RestrictiveToolPolicy,
    SecretReference,
    ServerBinding,
    StdioServer,
    TurnOutcome,
)

from mcp_pal import MCPTestKit

pytestmark = [pytest.mark.e2e, pytest.mark.live]

_EXAMPLES_ROOT = Path(__file__).parents[1]
_REPOSITORY_ROOT = _EXAMPLES_ROOT.parents[1]
_LIVE_ENABLED = os.environ.get("MCP_PAL_RUN_LIVE_OPENCODE") == "1"


def _spec(executable: str, model: str) -> AgentExecutionSpec:
    provider = model.split("/", 1)[0] if "/" in model else None
    server = StdioServer(
        name="example-mcp",
        command=sys.executable,
        args=(str(_EXAMPLES_ROOT / "servers" / "example_mcp_server.py"),),
        cwd=str(_EXAMPLES_ROOT),
    )
    return AgentExecutionSpec(
        harness=OpenCode(
            model=model,
            provider=provider,
            executable=executable,
            credential_references={
                "OPENCODE_API_KEY": SecretReference(
                    source="environment", name="OPENCODE_API_KEY"
                )
            },
        ),
        servers=(ServerBinding(server=server, alias="example-mcp"),),
        tool_policy=RestrictiveToolPolicy(
            allowed_tools=("example-mcp:shipping_quote",)
        ),
    )


@pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason="set MCP_PAL_RUN_LIVE_OPENCODE=1 to call OpenCode",
)
def test_live_opencode_uses_shipping_quote_and_captures_wire_evidence() -> None:
    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")
    if not os.environ.get("OPENCODE_API_KEY"):
        pytest.skip("OPENCODE_API_KEY is not available")

    model = os.environ.get("MCP_PAL_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(_spec(executable, model)) as session:
            turn = session.send(
                "Use the example-mcp shipping_quote tool with weight_kg 2 and "
                "zone local. Return the tool result and do not use any other tool.",
                timeout=120,
            )

        assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
        trace = session.result.trace
        assert trace is not None
        requests = [
            event
            for event in trace.events
            if event.kind.value == "tool.call_requested"
            and event.server_binding == "example-mcp"
            and event.turn_id == turn.snapshot.turn_id
            and event.payload.get("tool") == "shipping_quote"
        ]
        assert len(requests) == 1
        request = requests[0]
        arguments = request.payload.get("arguments")
        assert isinstance(arguments, Mapping)
        assert arguments["zone"] == "local"
        assert arguments["weight_kg"] == 2

        results = [
            event
            for event in trace.events
            if event.kind.value == "tool.result_received"
            and event.server_binding == "example-mcp"
            and event.turn_id == turn.snapshot.turn_id
            and event.correlation is not None
            and request.correlation is not None
            and event.correlation.request_sequence
            == request.correlation.request_sequence
        ]
        assert len(results) == 1
        result = results[0].payload.get("result")
        assert isinstance(result, Mapping)
        assert result["isError"] is False
        assert result["structuredContent"]["currency"] == "USD"
