# Run an ACP agent

Use ACP when the agent implements the Agent Client Protocol and you want M3 to launch it from a manifest. ACP launch configuration is distinct from M3’s native CLI adapters.

## Requirements

Install M3 with pytest and an ACP-compatible agent. Set `ACP_AGENT_COMMAND` to its executable, `ACP_AGENT_ARGS` to a JSON array of arguments, and `M3_DOCS_AGENT_MODEL` to the model identifier expected by that agent. Configure provider credentials through the agent’s documented login or environment mechanism. Do not put secrets in the manifest committed to source control.

The agent must support the MCP server access and tool behavior the test needs. Review any approval prompt from the agent before accepting it. ACP adapter support alone does not establish support for every interaction or evidence type.

## Launch from a manifest

Save `shipping_server.py` from [the first agent test](/guides/agents/first-test) beside this complete test as `test_acp_agent.py`:

```python
import json
import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_acp_agent_calls_the_shipping_tool() -> None:
    manifest = {
        "schema_version": "m3.harness.v1",
        "protocol": "acp",
        "protocol_version": 1,
        "command": os.environ["ACP_AGENT_COMMAND"],
        "args": json.loads(os.environ["ACP_AGENT_ARGS"]),
        "env": {},
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
    with MCPTestKit(env={}) as kit:
        result = kit.agents([selection])[0].run(
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

Run it from the project directory with `python -m pytest -q test_acp_agent.py`. The assertion checks M3’s recorded tool evidence. An ACP message that says the tool was used does not establish that it was.

The manifest’s `env` object is literal process configuration. Keep secrets in the ACP agent’s supported credential store or inject them from CI without committing them. ACP does not use M3-managed native harness downloads.

Next: [compare harness features](/guides/agents/harnesses) and review [the tested compatibility notes](/reference/compatibility).
