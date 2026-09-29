---
title: "Choose an agent harness"
description: "M3 connects to agent harnesses through native adapters or an ACP manifest. Choose the integration your agent provides, then check the capabilities and version your test needs."
---

# Choose an agent harness

M3 connects to agent harnesses through native adapters or an ACP manifest. Choose the integration your agent provides, then check the capabilities and version your test needs.

## Requirements

This example uses Codex. Install and sign in to the Codex CLI, install M3 with pytest, and set `M3_DOCS_CODEX_MODEL` to a model available to that login. Provider access is required and the run may incur cost. Review Codex’s tool approval prompt before accepting it; this test limits M3 tool access to one named tool.

## Run the same assertion through Codex

Save `shipping_server.py` from [the first agent test](first-test.md) beside this complete test as `test_harness.py`:

```python
import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_codex_calls_the_shipping_tool() -> None:
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
        result = agent.run(
            "Call shipping_quote once for weight_kg 2 in zone local.",
            server=server,
            tools=["shipping:shipping_quote"],
            timeout=120,
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
    )
```

From the directory containing both files, run `python -m pytest -q test_harness.py`. Keep the server, prompt, and assertion fixed when comparing harnesses.

| Integration | Configuration | Difference to account for |
|---|---|---|
| Codex, Pi, Claude Code, OpenCode | Native M3 harness selection | Each needs its installed executable and provider configuration. Readiness and captured evidence vary by integration. |
| ACP agent | Manifest with command, arguments, and protocol settings | M3 launches the supplied ACP process; it is not a native CLI runtime. |

These adapters do not have identical capabilities. M3 tests agent-driven elicitation with Codex CLI `0.156.1` and Pi `0.85.1`. That limit does not apply to direct SDK elicitation. The [compatibility reference](../../reference/compatibility.md) has the feature-specific support notes.

If M3 reports a capability as unavailable, keep the assertion and inspect readiness or the execution result. Next: [configure an ACP agent](acp.md) or [pin a managed runtime](managed-runtimes.md).
