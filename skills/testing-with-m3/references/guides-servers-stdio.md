<!-- Generated from docs/site/guides/servers/stdio.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Test a stdio server

Use `StdioServer` when M3 should launch a local MCP server process for the
test. M3 starts it for the connection and closes the process when the direct
client closes.

## Requirements

Use Python 3.10 or newer and run `m3 init` and `m3 setup` in the project. The
server command must be available in the project environment. Download the
example `shipping_server.py` (M3 repository: `sdk/examples/docs/servers-stdio/shipping_server.py`)
and save it as `shipping_server.py` in the project root. It starts without
credentials or external services.

## Discover and call a tool

Save this complete file as `tests/test_tools.py`:

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer

pytestmark = pytest.mark.m3(suite_name="shipping-direct")


def test_discover_and_call_shipping_quote() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        tools = client.list_all_tools()
        shipping = next(tool for tool in tools if tool.name == "shipping_quote")
        assert list(shipping.input_schema["required"]) == ["weight_kg", "zone"]
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
```

From the project root, run:

```sh
m3 test -- tests/test_tools.py
```

The server script is resolved from the test file, and `cwd` sets its working
directory. The assertion checks discovery and the returned quote. Change the
command, arguments, and working directory to match your own server; see
[test your own server](start-your-server.md).

## If startup fails

First run the server command manually from the configured working directory.
Check that it starts an MCP stdio transport and does not print ordinary logs
to stdout, where they would interfere with protocol messages. Then check the
Python executable and paths in `StdioServer`. Continue to [troubleshoot server
startup](troubleshooting-servers.md).
