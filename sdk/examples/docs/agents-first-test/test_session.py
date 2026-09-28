import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer, TurnOutcome

HERE = Path(__file__).resolve().parent


def test_two_turns_share_one_session() -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    with MCPTestKit(env={}) as kit:
        agent = kit.agents(
            [{"harness": "codex", "models": [os.environ["M3_DOCS_CODEX_MODEL"]]}]
        )[0]
        session = agent.session(
            server=server,
            tools=["shipping:shipping_quote"],
            timeout=120,
        )
        with session:
            first = session.send(
                "Call shipping_quote once for weight_kg 2 in zone local.", timeout=120
            )
            second = session.send(
                "Call shipping_quote once for weight_kg 3 in zone regional.",
                timeout=120,
            )
        result = session.result

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
    assert second.snapshot.outcome is TurnOutcome.COMPLETED, second.error
    assert first.snapshot.session_id == second.snapshot.session_id
    expect(result).to_have_tool_call(
        "shipping_quote",
        turn=first,
        server="shipping",
        status="success",
        count=1,
    )
    expect(result).to_have_tool_call(
        "shipping_quote",
        turn=second,
        server="shipping",
        status="success",
        count=1,
    )
