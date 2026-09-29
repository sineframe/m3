---
title: "Test a multi-turn agent session"
description: "An agent session keeps one conversation open across turns. Use it when later prompts depend on earlier work, and assert evidence against the turn that produced it."
---

# Test a multi-turn agent session

An agent session keeps one conversation open across turns. Use it when later prompts depend on earlier work, and assert evidence against the turn that produced it.

## Requirements

Use `shipping_server.py` from [the first agent test](first-test.md), install pytest, sign in to Codex, and set `M3_DOCS_CODEX_MODEL` to a model available to that login. This live example needs provider access. The selected harness must report multi-turn readiness before the test relies on it.

## Send two turns and inspect each separately

Save this complete test as `test_session.py` beside `shipping_server.py`:

```python
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
```

Run it from the project directory with `python -m pytest -q test_session.py`. The assertions prove both turns belong to one session and each has one successful call. They do not prove that the model used the first amount in the second turn; add a result assertion if that behavior matters.

Read `result` after the session context closes so the execution has reached its terminal state. A failed first turn may leave the session unusable for the second; this test reports either turn’s failure.

Next: [inspect turn-level traces](../results/traces.md) or run cases with [a matrix](matrices.md).
