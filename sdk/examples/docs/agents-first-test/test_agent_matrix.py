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
