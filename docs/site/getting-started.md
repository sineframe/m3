---
title: "Write your first MCP test"
description: "This walkthrough starts a small MCP server as a local process, discovers its shipping_quote tool, calls it, and checks the structured response. You will also make the assertion fail, restore it, and open the saved run."
---

# Write your first MCP test

This walkthrough starts a small MCP server as a local process, discovers its
`shipping_quote` tool, calls it, and checks the structured response. You will
also make the assertion fail, restore it, and open the saved run.

## Requirements

- Python 3.10 or newer.
- [uv](https://docs.astral.sh/uv/) and the standalone M3 CLI.
- No agent provider key, M3 account, or external server.

Install the CLI once with uv:

```sh
uv tool install sf-m3-cli
```

On macOS or Linux, you can use the shell installer instead:

```sh
curl -LsSf https://m3.sineframe.com/install.sh | sh
```

Create a project, install the matching Python SDK, and check the environment:

```sh
mkdir shipping-test
cd shipping-test
m3 init
m3 setup
m3 doctor
```

When `m3 init` asks for a project name and suite name, enter `shipping-test`
and `shipping`. `m3 setup` installs the SDK and pytest support into the
project's Python environment. It does not install the CLI or edit your
dependency manifest or lockfile. The CLI and SDK are separate installations;
keep them on matching releases. See [install and update M3](start/install.md).

## Add the server and test

Create `shipping_server.py` beside the `tests` directory:

```python
"""A deterministic MCP shipping quote server used by the first-test guide."""

from __future__ import annotations

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
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
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult:
    arguments = params.arguments or {}
    rates = {"local": 2.0, "regional": 3.5, "international": 7.0}
    amount = round(5 + float(arguments["weight_kg"]) * rates[str(arguments["zone"])], 2)
    quote = {"amount": amount, "currency": "USD"}
    return types.CallToolResult(
        content=[types.TextContent(text=f"{amount:.2f} USD")],
        structured_content=quote,
    )


async def main() -> None:
    server = Server(
        "shipping-server",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
```

Replace the generated skipped test at `tests/test_m3_starter.py` with this
complete file:

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

The `StdioServer` command uses the same Python environment as pytest and points
at the server file relative to the test. The context managers close the MCP
connection and the subprocess after the assertion has its result.

## Run the test

From the project root, run:

```sh
m3 test -- tests/test_m3_starter.py
```

Pytest should report one passing test. The assertion checks both that discovery
returned the intended tool and that a 2 kg local quote has the structured value
`{"amount": 9.0, "currency": "USD"}`. A successful protocol call alone would
not prove that the server returned the right quote.

## See a real failure

Change the expected amount from `9.0` to `10.0` and rerun the same command. The
test should fail at the structured-content assertion and show the actual
`9.0` result. Restore `9.0` and rerun; the test should pass again.

## Open the saved run

From the project root, open the history created by the test:

```sh
m3 ui
```

M3 opens the saved-run list in a local browser. Select the run from the test
you completed. The CLI stores history in `.m3/executions.sqlite` by
default. See [inspect a saved run](guides/results/viewer.md) for navigating the
report.

To test a server you already have, continue to [test your own server](start/your-server.md).
