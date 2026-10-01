from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_external_acp_agent_calls_shipping_tool() -> None:
    manifest = {
        "schema_version": "m3.harness.v1",
        "protocol": "acp",
        "protocol_version": 1,
        "command": os.environ["ACP_AGENT_COMMAND"],
        "args": json.loads(os.environ.get("ACP_AGENT_ARGS", "[]")),
        "env": {os.environ["ACP_AGENT_CREDENTIAL_ENV"]: "${M3_DOCS_PROVIDER_API_KEY}"},
    }
    selection = {
        "harness": "acp",
        "models": [os.environ["M3_DOCS_AGENT_MODEL"]],
        "manifest": manifest,
    }
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    prompt = (
        "Call shipping_quote once for weight_kg 2 in zone local. "
        "Return the amount from the tool result."
    )
    with MCPTestKit() as kit:
        result = kit.agents([selection])[0].run(
            prompt, server=server, tools=["shipping:shipping_quote"], timeout=120
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
