<!-- Generated from docs/site/guides/evaluations/assertions.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Assert the evidence that matters

Use ordinary Python assertions for direct operation results. Use `expect` when
you need to ask a question about a complete M3 execution or agent turn.

## Direct result

Save this as `tests/test_direct_result.py` in the project from
[Write your first MCP test](getting-started.md). It starts the
`shipping_server.py` in the project root.

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer

pytestmark = pytest.mark.m3(suite_name="shipping")


def test_direct_result() -> None:
    project_root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(project_root / "shipping_server.py"),),
        cwd=str(project_root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
```

From the project root, run `m3 test -- tests/test_direct_result.py`. The test
checks the server response directly.

## Agent evidence

Save this as `tests/test_agent_evidence.py`. The `agent` fixture needs a harness
selection, so the command names one. Replace `YOUR_AGENT_MODEL` with a model
your Codex setup serves, as described in [your first agent test](guides-agents-first-test.md).

```python
import sys
from pathlib import Path

import pytest

from m3 import StdioServer, expect

pytestmark = pytest.mark.m3(suite_name="shipping-agent")


def test_agent_evidence(agent) -> None:
    project_root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(project_root / "shipping_server.py"),),
        cwd=str(project_root),
    )
    result = agent.run(
        "Quote a 2 kg parcel in the local zone.",
        server=server,
        permission_policy="allow",
    )

    expect(result).to_have_tool_call(
        "shipping_quote",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
    )
```

```sh
m3 test --harness codex=YOUR_AGENT_MODEL -- tests/test_agent_evidence.py
```

The test checks the captured call, its arguments, and its result status. It does
not accept the agent's prose as proof that the call occurred. Grant tool
approval only to the scoped test server and workspace.

See the [matcher reference](reference-python-m3-matchers.md) for count, choice,
and result predicates.
