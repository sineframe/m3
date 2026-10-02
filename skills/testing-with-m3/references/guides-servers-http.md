<!-- Generated from docs/site/guides/servers/http.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Test a Streamable HTTP server

Use `HTTPServer` for an MCP endpoint that is already running. The direct test
below uses a local deterministic service, so it does not depend on a public
endpoint or network service.

## Requirements

Use Python 3.10 or newer and install the CLI/project SDK with `m3 init` and
`m3 setup`. The service below
listens on loopback port 8765 and needs no credentials.

## Add the local MCP service

Save this complete file as `shipping_http_server.py` in the project root. It
serves the `shipping_quote` tool over Streamable HTTP at
`http://127.0.0.1:8765/mcp/`.

```python
"""Deterministic Streamable HTTP MCP server for the HTTP guide."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount


async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="shipping_quote",
                description="Calculate a deterministic shipping quote",
                input_schema={
                    "type": "object",
                    "properties": {
                        "weight_kg": {"type": "number", "exclusiveMinimum": 0},
                        "zone": {
                            "type": "string",
                            "enum": ["local", "regional", "international"],
                        },
                    },
                    "required": ["weight_kg", "zone"],
                    "additionalProperties": False,
                },
            )
        ]
    )


async def call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    arguments = params.arguments or {}
    rates = {"local": 2.0, "regional": 3.5, "international": 7.0}
    amount = round(5 + float(arguments["weight_kg"]) * rates[str(arguments["zone"])], 2)
    return types.CallToolResult(
        content=[types.TextContent(text=f"{amount:.2f} USD")],
        structured_content={"amount": amount, "currency": "USD"},
    )


server = Server(
    "shipping-http-server",
    version="1.0.0",
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)
session_manager = StreamableHTTPSessionManager(server)


@asynccontextmanager
async def lifespan(_app: Starlette) -> AsyncIterator[None]:
    async with session_manager.run():
        yield


app = Starlette(
    routes=[Mount("/mcp", app=session_manager.handle_request)],
    lifespan=lifespan,
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
```

## Start the local MCP service

In terminal one, from the project root, run the service with the project interpreter selected and
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
See [test your own server](start-your-server.md) and the
[HTTPServer reference](reference-python-m3-types.md).
