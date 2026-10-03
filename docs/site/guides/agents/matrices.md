---
title: "Run agent cases as a matrix"
description: "Use HarnessMatrix to run the same MCP cases across harnesses and trials. A matrix expands test work; it does not make model behavior deterministic."
---

# Run agent cases as a matrix

Use `HarnessMatrix` to run the same MCP cases across harnesses and trials. A matrix expands test work; it does not make model behavior deterministic.

## Requirements

This live example runs four Codex executions. Install M3 with pytest, sign in to Codex, and set `M3_DOCS_CODEX_MODEL` to a model available to that login. The test restricts M3 tool access to the selected shipping tool, and M3 approves Codex's calls to it.

## Define and run the cases

Save `shipping_server.py` from [the first agent test](first-test.md) beside this complete test as `test_agent_matrix.py`:

```python
from __future__ import annotations

import os
import sys
from pathlib import Path

from m3 import (
    Codex,
    ExecutionOutcome,
    HarnessCase,
    HarnessMatrix,
    MCPTestKit,
    ServerCase,
    ToolCase,
    expect,
)
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_each_tool_twice() -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    matrix = HarnessMatrix.each_tool(
        servers=(
            ServerCase(
                name="shipping",
                server=server,
                tools=(
                    ToolCase(
                        name="shipping_quote",
                        id="local",
                        arguments={"weight_kg": 2, "zone": "local"},
                        prompt="Call shipping_quote with weight_kg 2 and zone local.",
                    ),
                    ToolCase(
                        name="shipping_quote",
                        id="regional",
                        arguments={"weight_kg": 2, "zone": "regional"},
                        prompt="Call shipping_quote with weight_kg 2 and zone regional.",
                    ),
                ),
            ),
        ),
        harnesses=(
            HarnessCase(
                name="codex",
                harness=Codex(model=os.environ["M3_DOCS_CODEX_MODEL"]),
            ),
        ),
        trials=2,
        id="shipping-harness-matrix",
    )
    cases = matrix.cases()
    assert len(cases) == 4

    with MCPTestKit(env={}) as kit:
        results = tuple(case.run(kit=kit, timeout=120) for case in cases)

    for case, result in zip(cases, results, strict=True):
        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
        expect(result).to_have_tool_call(
            case.tool.name,
            server=case.server.name,
            arguments=case.tool.arguments,
            status="success",
            count=1,
        )
```

Run `python -m pytest -q test_agent_matrix.py` from the project directory. Two tool cases × one harness × two trials produce four executions. The test checks each completed result and the observed call for that case.

`each_server` creates a case for each server and harness. `all_servers` gives one harness case all declared servers. `each_tool` creates a case for each tool, server, harness, and trial. Trials repeat a case; they are not automatic retries. Keep the harness, model, server, and prompts fixed when comparing runs.

The assertions check observed calls, not whether the agent’s prose is correct. Next: [read the pytest selection rules](../../reference/pytest.md).
