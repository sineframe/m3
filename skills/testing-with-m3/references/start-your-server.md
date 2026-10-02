<!-- Generated from docs/site/start/your-server.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Test your own server

Use a `StdioServer` when M3 should start your local server process for each
test connection. Use an `HTTPServer` when the MCP endpoint is already running.
Both direct paths can run without an agent provider.

## Requirements

Install the M3 CLI and prepare the project SDK as described in [install M3](start-install.md).
For stdio, the server command must run in the project environment. For HTTP,
start the service before running the test; M3 does not start or stop a deployed
HTTP service.

## Stdio: start a local process

The first-test project contains the server used below. Put its
`shipping_server.py` source (M3 repository: `sdk/examples/docs/first-test/shipping_server.py`)
beside your `tests` directory, then use this complete test as
`tests/test_m3_starter.py`:

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer

pytestmark = pytest.mark.m3(suite_name="shipping")


def test_shipping_quote() -> None:
    project_root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(project_root / "shipping_server.py"),),
        cwd=str(project_root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        tools = client.list_all_tools()
        assert [tool.name for tool in tools] == ["shipping_quote"]
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
```

The `StdioServer` command uses the test's Python environment and resolves the
server file relative to the test. The M3 context closes the client and owned
subprocess even when an assertion fails. The first-test project
source files (M3 repository: `sdk/examples/docs/first-test`)
include this server and test.

Run from the project root:

```sh
m3 test -- tests/test_m3_starter.py
```

One passing test means M3 connected, discovered the tool, and observed the
expected structured result. It does not certify behavior outside the inputs
you asserted.

To start your own module instead, replace the `StdioServer(...)` block with
this snippet, changing the module name, arguments, and working directory to
match your project:

```python
server = StdioServer(name="inventory", command=sys.executable,
    args=("-m", "inventory_mcp"),
    cwd=str(Path(__file__).parents[1]),
)
```

Then change the tool call and assertions to the behavior your server promises.
This replacement assumes `inventory_mcp` is installed in the project
environment; it is not a runnable server supplied by M3.

## HTTP: connect to a running endpoint

The direct client accepts an `HTTPServer` for a Streamable HTTP MCP endpoint.
Start your service separately, then use a complete test such as:

```python
import pytest

from m3 import MCPTestKit
from m3.types import HTTPServer, TrustLevel

pytestmark = pytest.mark.m3(suite_name="shipping-http")


def test_shipping_quote_over_http() -> None:
    server = HTTPServer(
        name="shipping",
        url="http://127.0.0.1:8765/mcp/",
        trust=TrustLevel.TRUSTED_PRIVATE,
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        assert "shipping_quote" in {tool.name for tool in client.list_all_tools()}
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
```

Run the HTTP test while the endpoint is available:

```sh
m3 test -- tests/test_shipping_http.py
```

For this loopback service, set `TrustLevel.TRUSTED_PRIVATE` as shown;
`HTTPServer` otherwise defaults to untrusted.

The [HTTP guide](guides-servers-http.md) includes a local server and test that
you can run in two terminals. For authentication, use a header or secret
reference; never put a credential in the URL.
