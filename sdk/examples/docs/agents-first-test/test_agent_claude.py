import os
import sys
from pathlib import Path

from m3 import MCPTestKit, expect
from m3.types import StdioServer, TurnOutcome

HERE = Path(__file__).resolve().parent


def test_claude_code_calls_the_shipping_tool() -> None:
    model = os.environ["M3_DOCS_CLAUDE_MODEL"]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    with MCPTestKit(env={}) as kit:
        agent = kit.agents([{"harness": "claude_code", "models": [model]}])[0]
        session = agent.session(server=server, timeout=120)
        with session:
            turn = session.send(
                "Use the shipping_quote tool once with weight_kg 2 and zone local. "
                "Report the returned amount.",
                timeout=120,
            )
        result = session.result

    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        turn=turn,
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
