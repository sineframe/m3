import sys
from pathlib import Path

import pytest

from m3 import ExecutionOutcome, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent
PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}

ACP_MANIFEST = {
    "schema_version": "m3.harness.v1",
    "protocol": "acp",
    "protocol_version": 1,
    "command": sys.executable,
    "args": [str(HERE.parent / "deterministic_acp_agent.py")],
}
pytestmark = pytest.mark.m3(
    suite_name="shipping",
    agents=[{"harness": "acp", "models": ["fixture"], "manifest": ACP_MANIFEST}],
)


def test_selected_agent_calls_shipping_tool(agent) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE.parent / "shipping_server.py"),),
        cwd=str(HERE.parent),
    )
    result = agent.run(PROMPT, server=server, timeout=180)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
        result={"structured_content": EXPECTED_QUOTE},
        result_partial=True,
    )
