from __future__ import annotations

import os
import sys
from pathlib import Path

from m3 import (
    ClaudeCode,
    Codex,
    ExecutionOutcome,
    HarnessCase,
    HarnessMatrix,
    MCPTestKit,
    OpenCode,
    Pi,
    ServerCase,
    ToolCase,
    expect,
)
from m3.types import SecretReference, StdioServer

HERE = Path(__file__).resolve().parent


def test_tools_across_servers_harnesses_and_trials() -> None:
    servers = (
        ServerCase(
            name="shipping",
            server=StdioServer(
                name="shipping",
                command=sys.executable,
                args=(str(HERE / "shipping_server.py"),),
                cwd=str(HERE),
            ),
            tools=(
                ToolCase(
                    name="shipping_quote",
                    id="local",
                    arguments={"weight_kg": 2, "zone": "local"},
                    prompt="Call shipping_quote with weight_kg 2 and zone local.",
                ),
                ToolCase(
                    name="shipping_window",
                    id="window",
                    arguments={},
                    prompt="Call shipping_window.",
                ),
            ),
        ),
        ServerCase(
            name="inventory",
            server=StdioServer(
                name="inventory",
                command=sys.executable,
                args=(str(HERE / "inventory_server.py"),),
                cwd=str(HERE),
            ),
            tools=(
                ToolCase(
                    name="stock_count",
                    id="stock",
                    arguments={"sku": "widget-1"},
                    prompt="Call stock_count for sku widget-1.",
                ),
                ToolCase(
                    name="reorder_status",
                    id="reorder",
                    arguments={"sku": "widget-1"},
                    prompt="Call reorder_status for sku widget-1.",
                ),
            ),
        ),
    )
    harnesses = (
        HarnessCase(
            name="codex", harness=Codex(model=os.environ["M3_DOCS_CODEX_MODEL"])
        ),
        HarnessCase(
            name="pi",
            harness=Pi(
                provider=os.environ["M3_DOCS_PI_PROVIDER"],
                model=os.environ["M3_DOCS_PI_MODEL"],
                credential_references={
                    os.environ["M3_DOCS_PI_KEY_NAME"]: SecretReference(
                        source="environment", name="M3_DOCS_PI_API_KEY"
                    )
                },
            ),
        ),
        HarnessCase(
            name="claude_code",
            harness=ClaudeCode(
                model=os.environ["M3_DOCS_CLAUDE_MODEL"],
                credential_references={
                    os.environ["M3_DOCS_CLAUDE_KEY_NAME"]: SecretReference(
                        source="environment", name="M3_DOCS_CLAUDE_API_KEY"
                    )
                },
            ),
        ),
        HarnessCase(
            name="opencode",
            harness=OpenCode(
                provider=os.environ["M3_DOCS_OPENCODE_PROVIDER"],
                model=os.environ["M3_DOCS_OPENCODE_MODEL"],
                credential_references={
                    os.environ["M3_DOCS_OPENCODE_KEY_NAME"]: SecretReference(
                        source="environment", name="M3_DOCS_OPENCODE_API_KEY"
                    )
                },
            ),
        ),
    )
    matrix = HarnessMatrix.each_tool(
        servers=servers, harnesses=harnesses, trials=2, id="agent-tools-by-server"
    )
    cases = matrix.cases()
    assert len(cases) == 32

    with MCPTestKit(env={}) as kit:
        results = tuple(case.run(kit=kit, timeout=180) for case in cases)

    for case, result in zip(cases, results, strict=True):
        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
        expected_result = {
            "shipping_quote": {"amount": 9.0, "currency": "USD"},
            "shipping_window": {"days": 2, "zone": "local"},
            "stock_count": {"sku": "widget-1", "quantity": 12},
            "reorder_status": {"sku": "widget-1", "reorder": False},
        }[case.tool.name]
        expect(result).to_have_tool_call(
            case.tool.name,
            server=case.server.name,
            arguments=case.tool.arguments,
            status="success",
            count=1,
            result={"structured_content": expected_result},
            result_partial=True,
        )
