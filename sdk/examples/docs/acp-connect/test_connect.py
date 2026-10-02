from __future__ import annotations

import json
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_existing_local_acp_agent_calls_shipping_tool() -> None:
    manifest = {
        "schema_version": "m3.harness.v1",
        "protocol": "acp",
        "protocol_version": 1,
        "command": sys.executable,
        "args": [str(HERE / "deterministic_acp_agent.py")],
    }
    selection = {"harness": "acp", "models": ["fixture"], "manifest": manifest}
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    prompt = json.dumps(
        {
            "server": "shipping",
            "tool": "shipping_quote",
            "arguments": {"weight_kg": 2, "zone": "local"},
        }
    )
    with MCPTestKit(env={}) as kit:
        result = kit.agents([selection])[0].run(
            prompt, server=server, tools=["shipping:shipping_quote"], timeout=20
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
    captured = result.trace_view.tool_calls[0].result.value
    assert captured.content[0].text == "9.00 USD"
    assert captured.structured_content.value == {
        "amount": 9.0,
        "currency": "USD",
    }
