---
title: "Pass a credential to a stdio MCP server"
description: "Map a parent environment variable to a stdio server and verify its tool result."
---

# Pass a credential to a stdio MCP server

Pass a parent environment value to a local stdio MCP server and verify that the server accepts it without returning the credential.

## Requirements

Use Python 3.10 or later with `sf-m3[pytest]` installed in the project environment. This example starts a local process and makes no external provider request.

## Complete project

Create `stdio_server.py` and `test_credentials.py` in one project directory.

`stdio_server.py`:

```python
from __future__ import annotations

import os

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="credential_check",
                description="Return whether the expected dummy service credential arrived.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        ]
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult:
    expected = os.environ.get("DEMO_SERVICE_TOKEN")
    if params.name != "credential_check" or expected != "dummy-service-token":
        return types.CallToolResult(
            content=[types.TextContent(text="credential check failed")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text="credential accepted")],
        structured_content={"authenticated": True},
    )


async def main() -> None:
    server: Server[object] = Server(
        "credential-demo", on_list_tools=list_tools, on_call_tool=call_tool
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
```

`test_credentials.py`:

```python
from __future__ import annotations

import sys

import pytest

from m3.async_api import AsyncMCPTestKit
from m3.types import SecretReference, StdioServer


@pytest.mark.asyncio
async def test_stdio_server_receives_named_credential(monkeypatch):
    monkeypatch.setenv("M3_DEMO_SERVICE_KEY", "dummy-service-token")
    kit = AsyncMCPTestKit(env={})
    try:
        server = StdioServer(
            name="credential-demo",
            command=sys.executable,
            args=("stdio_server.py",),
            environment={
                "DEMO_SERVICE_TOKEN": SecretReference(
                    source="environment", name="M3_DEMO_SERVICE_KEY"
                )
            },
        )
        async with kit.direct(server) as client:
            result = await client.call_tool("credential_check", {})
            assert result.content[0]["text"] == "credential accepted"
            assert result.structured_content == {"authenticated": True}
    finally:
        await kit.aclose()
```

## Run it

From the project directory, run:

```sh
python -m pytest -q test_credentials.py
```

The test sets `M3_DEMO_SERVICE_KEY` to a dummy value. M3 maps it to the child variable `DEMO_SERVICE_TOKEN`. The `credential accepted` result proves the server received the expected credential without returning it.

```text
1 passed
```

If the source variable is missing, stdio startup fails. A present empty value is passed to the child as an empty string. See the [credential reference](../../reference/credentials.md) for the exact endpoint behavior and the other credential paths. To continue with another task, return to [Configure credentials](../credentials.md).
