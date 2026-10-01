from __future__ import annotations

import json
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_custom_acp_agent_exposes_mcp_result() -> None:
    selection = {
        "harness": "acp",
        "models": ["fixture"],
        "manifest": {
            "schema_version": "m3.harness.v1",
            "protocol": "acp",
            "protocol_version": 1,
            "command": sys.executable,
            "args": [str(HERE / "wrapped_agent.py")],
        },
    }
    server = StdioServer(
        name="echo",
        command=sys.executable,
        args=(str(HERE / "echo_server.py"),),
        cwd=str(HERE),
    )
    prompt = json.dumps({"tool": "echo", "arguments": {"text": "m3-wrapper-ok"}})
    with MCPTestKit(env={}) as kit:
        result = kit.agents([selection])[0].run(
            prompt, server=server, tools=["echo:echo"], timeout=20
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "echo",
        server="echo",
        arguments={"text": "m3-wrapper-ok"},
        status="success",
        count=1,
    )
    trace = result.trace_view
    captured = trace.tool_calls[0].result.value
    assert captured.content[0].text == "m3-wrapper-ok"
    assert captured.structured_content.value == {"echo": "m3-wrapper-ok"}
    assert any(
        message.role == "assistant"
        and any(
            block.kind == "text" and block.text == "m3-wrapper-ok"
            for block in message.content
        )
        for message in trace.messages
    )
