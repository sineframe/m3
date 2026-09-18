"""Opt-in live harness example: let OpenCode choose a real MCP tool.

Run this separately from the deterministic catalog:

    set -a; source .env; set +a
    MCP_PAL_RUN_LIVE_OPENCODE=1 uv run --project cli mcp-pal test \
      --env-file .env --harness opencode=opencode/big-pickle -- \
      -q sdk/examples/tests/test_live_opencode.py

The provider response is nondeterministic and may incur cost. The assertion
is intentionally narrow: the harness must use the allowed tool with the
requested semantic argument and the captured wire result must succeed.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from mcp_pal import expect
from mcp_pal.types import StdioServer, TurnOutcome

pytestmark = [pytest.mark.e2e, pytest.mark.live]

_EXAMPLES_ROOT = Path(__file__).parents[1]
_REPOSITORY_ROOT = _EXAMPLES_ROOT.parents[1]
_LIVE_ENABLED = os.environ.get("MCP_PAL_RUN_LIVE_OPENCODE") == "1"


@pytest.fixture
def example_server() -> StdioServer:
    return StdioServer(
        name="example-mcp",
        command=sys.executable,
        args=(str(_EXAMPLES_ROOT / "servers" / "example_mcp_server.py"),),
        cwd=str(_EXAMPLES_ROOT),
    )


@pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason="set MCP_PAL_RUN_LIVE_OPENCODE=1 to call OpenCode",
)
@pytest.mark.mcp_pal(
    agents=[{"harness": "opencode", "models": ["opencode/big-pickle"]}]
)
def test_live_opencode_uses_shipping_quote_and_captures_wire_evidence(
    agent, example_server
) -> None:
    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")
    if not os.environ.get("OPENCODE_API_KEY"):
        pytest.skip("OPENCODE_API_KEY is not available")

    with agent.session(server=example_server, timeout=120) as session:
        turn = session.send(
            "Use the example-mcp shipping_quote tool with weight_kg 2 and "
            "zone local. Return the tool result and do not use any other tool."
        )

        assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
        view = session.result.trace_view
        assert view is not None
        expect(session.result).to_have_tool_call(
            "shipping_quote",
            turn=turn,
            server="example-mcp",
            arguments={"weight_kg": 2, "zone": "local"},
            result={"structured_content": {"currency": "USD"}},
            result_partial=True,
            status="success",
            count=1,
        )
        calls = [
            call
            for call in view.tool_calls
            if call.tool.value == "shipping_quote"
            and call.server.value == "example-mcp"
            and call.turn_id == turn.snapshot.turn_id
        ]
        assert len(calls) == 1
        call = calls[0]
        assert call.arguments.value == {"zone": "local", "weight_kg": 2}
        assert call.wire.state.value == "observed"
        assert call.result.value is not None
        assert call.result.value.structured_content.value["currency"] == "USD"

        usage = view.summary.usage.value
        assert usage is not None, "OpenCode did not report usage"
        assert usage.cost.value is not None, "OpenCode did not report cost"
        assert usage.cost.value < 100.0, (
            f"OpenCode cost exceeded budget: {usage.cost.value}"
        )
