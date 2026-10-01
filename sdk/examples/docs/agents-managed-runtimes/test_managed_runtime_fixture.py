import sys
from pathlib import Path

import pytest

from m3 import ExecutionOutcome, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent

pytestmark = pytest.mark.m3(suite_name="pinned-runtime")


def test_cli_selected_pin_is_recorded(agent) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    result = agent.run(
        "Use shipping:shipping_quote once with weight_kg 2 and zone local.",
        server=server,
        tools=["shipping:shipping_quote"],
        timeout=180,
    )
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    identity = result.snapshot.agent
    assert identity is not None
    assert identity.harness.runtime == "managed"
    assert identity.harness.requested_selector == identity.harness.resolved_version
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
        result={"structured_content": {"amount": 9.0, "currency": "USD"}},
        result_partial=True,
    )
