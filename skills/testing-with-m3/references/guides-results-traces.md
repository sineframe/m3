<!-- Generated from docs/site/guides/results/traces.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Read a finalized trace

Use the operation result for the immediate response and the finalized trace for
the complete observed execution.

## Requirements

Start from the project in [Your first MCP test](getting-started.md). This example
uses the same `server` fixture and needs no credentials.

## Complete test

Save as `tests/test_trace.py`:

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer
from m3.types import ExecutionOutcome

pytestmark = pytest.mark.m3(suite_name="shipping")


def test_shipping_trace():
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})
        assert result.structured_content == {"amount": 9.0, "currency": "USD"}

    trace = client.final_trace
    assert trace is not None
    view = trace.view()
    assert view.outcome is ExecutionOutcome.COMPLETED
    call = view.tool_calls[0]
    assert call.tool.value == "shipping_quote"
    assert call.arguments.value == {"weight_kg": 2, "zone": "local"}
    assert call.wire.state.value == "observed"
```

Run it from the project root:

```sh
m3 test -- tests/test_trace.py
```

The result assertion checks the server response. The trace assertions check
the call M3 observed. Moving `client.final_trace` inside the `with` block is an
error because the trace is not final yet.

Next, [open the saved run](guides-results-viewer.md) or read about
[evidence availability](concepts-evidence.md).

For what each trace limitation means, see [CLI output and trace limitations](reference-output.md).
