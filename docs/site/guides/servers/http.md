# Test a Streamable HTTP server

Use `HTTPServer` for an MCP endpoint that is already running. The direct test
below uses a local deterministic service, so it does not depend on a public
endpoint or network service.

## Requirements

Use Python 3.10 or newer and install the CLI/project SDK with `m3 init` and
`m3 setup`. Run the supplied service and test from the
[HTTP example project](https://github.com/sineframe/m3/tree/main/sdk/examples/docs/servers-http). The
service listens on loopback port 8765 and needs no credentials.

## Start the local MCP service

In terminal one, run the service with the project interpreter selected and
reported by `m3 setup`. When setup created the default `.venv`, run:

```sh
./.venv/bin/python shipping_http_server.py
```

On Windows with the default `.venv`, run `.\.venv\Scripts\python.exe shipping_http_server.py`.
If setup selected an active virtual or Conda environment, use that
environment's Python command instead.

Keep it running while the test executes. The server exposes the
`shipping_quote` tool at `http://127.0.0.1:8765/mcp/`.

## Connect and assert the response

Save this complete test as `tests/test_shipping_http.py`:

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

In terminal two, from the same project root, run:

```sh
m3 test -- tests/test_shipping_http.py
```

The test checks tool discovery and the structured quote returned by the local
service. The explicit `TRUSTED_PRIVATE` value allows a direct connection to a
loopback endpoint; direct `HTTPServer` values otherwise default to untrusted.
Stop the server in terminal one with Ctrl-C when the test is done. M3 owns the
client connection; it does not manage the HTTP server process.

## Add authentication

For an endpoint that requires authentication, pass static headers on
`HTTPServer.headers` or use a `SecretReference` for a credential supplied by
environment. Keep provider credentials separate from MCP endpoint headers.
See [test your own server](/start/your-server) and the
[HTTPServer reference](/reference/python/m3/types).
