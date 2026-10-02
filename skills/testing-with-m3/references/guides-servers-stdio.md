<!-- Generated from docs/site/guides/servers/stdio.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Test a stdio server

Use `StdioServer` when M3 should launch a local MCP server process for the
test. M3 starts it for the connection and closes the process when the direct
client closes.

## Requirements

Use Python 3.10 or newer and run `m3 init` and `m3 setup` in the project. The
server command must be available in the project environment. The example
server below starts without credentials or external services.

## Add the shipping server

Save this complete file as `shipping_server.py` in the project root. It is a
small MCP server over stdio. It advertises the `shipping_quote`,
`normalize_customer`, `create_order`, `get_order`, and `always_fails` tools, the
`testing-guide` resource, and the `review_order` prompt. It keeps orders in
process memory. The tool, resource and prompt, error and schema, and stateful
test guides in this section all run against this file.

```python
"""Deterministic MCP server used by direct-server guide examples."""

from __future__ import annotations

import json
import re
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

_orders: dict[str, dict[str, Any]] = {}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    output_properties: dict[str, Any] | None = None,
) -> types.Tool:
    output_schema = None
    if output_properties is not None:
        output_schema = {
            "type": "object",
            "properties": output_properties,
            "required": list(output_properties),
            "additionalProperties": False,
        }
    return types.Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        output_schema=output_schema,
    )


_TOOLS = (
    _tool(
        "shipping_quote",
        "Calculate a deterministic shipping quote",
        {
            "weight_kg": {"type": "number", "exclusiveMinimum": 0},
            "zone": {"type": "string", "enum": ["local", "regional", "international"]},
        },
        required=["weight_kg", "zone"],
        output_properties={
            "amount": {"type": "number"},
            "currency": {"type": "string"},
        },
    ),
    _tool(
        "normalize_customer",
        "Convert a customer name into a stable identifier",
        {"name": {"type": "string", "minLength": 1}},
        required=["name"],
        output_properties={"customer_id": {"type": "string"}},
    ),
    _tool(
        "create_order",
        "Create an order for a normalized customer",
        {
            "customer_id": {"type": "string", "minLength": 1},
            "item": {"type": "string", "minLength": 1},
            "quantity": {"type": "integer", "minimum": 1},
        },
        required=["customer_id", "item", "quantity"],
        output_properties={"order_id": {"type": "string"}},
    ),
    _tool(
        "get_order",
        "Retrieve an order created in this server process",
        {"order_id": {"type": "string", "minLength": 1}},
        required=["order_id"],
        output_properties={
            "order_id": {"type": "string"},
            "customer_id": {"type": "string"},
            "item": {"type": "string"},
            "quantity": {"type": "integer"},
        },
    ),
    _tool("always_fails", "Return a normal MCP tool error", {}),
)


async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
    return types.ListToolsResult(tools=list(_TOOLS))


def _success(value: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(text=json.dumps(value, sort_keys=True))],
        structured_content=value,
    )


async def call_tool(
    _context: object, params: types.CallToolRequestParams
) -> types.CallToolResult:
    arguments = params.arguments or {}
    if params.name == "shipping_quote":
        rates = {"local": 2.0, "regional": 3.5, "international": 7.0}
        amount = round(
            5 + float(arguments["weight_kg"]) * rates[str(arguments["zone"])], 2
        )
        return _success({"amount": amount, "currency": "USD"})
    if params.name == "normalize_customer":
        customer_id = re.sub(
            r"[^a-z0-9]+", "-", str(arguments["name"]).strip().lower()
        ).strip("-")
        return _success({"customer_id": customer_id})
    if params.name == "create_order":
        order_id = f"order-{len(_orders) + 1:03d}"
        _orders[order_id] = {
            "order_id": order_id,
            "customer_id": arguments["customer_id"],
            "item": arguments["item"],
            "quantity": arguments["quantity"],
        }
        return _success({"order_id": order_id})
    if params.name == "get_order":
        order = _orders.get(str(arguments["order_id"]))
        if order is None:
            return types.CallToolResult(
                content=[types.TextContent(text="order not found")], is_error=True
            )
        return _success(order)
    if params.name == "always_fails":
        return types.CallToolResult(
            content=[types.TextContent(text="expected example failure")],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(text=f"unknown tool: {params.name}")],
        is_error=True,
    )


async def list_resources(
    _context: object, _params: object
) -> types.ListResourcesResult:
    return types.ListResourcesResult(
        resources=[
            types.Resource(
                name="testing-guide",
                uri="memory://testing-guide",
                description="A short guide exposed by the example server",
                mime_type="text/markdown",
            )
        ]
    )


async def read_resource(
    _context: object, params: types.ReadResourceRequestParams
) -> types.ReadResourceResult:
    return types.ReadResourceResult(
        contents=[
            types.TextResourceContents(
                uri=params.uri,
                mime_type="text/markdown",
                text="# Testing guide\n\nDiscover capabilities before asserting behavior.",
            )
        ]
    )


async def list_prompts(_context: object, _params: object) -> types.ListPromptsResult:
    return types.ListPromptsResult(
        prompts=[
            types.Prompt(
                name="review_order",
                description="Ask an assistant to review an order",
                arguments=[types.PromptArgument(name="order_id", required=True)],
            )
        ]
    )


async def get_prompt(
    _context: object, params: types.GetPromptRequestParams
) -> types.GetPromptResult:
    order_id = (params.arguments or {}).get("order_id", "unknown")
    return types.GetPromptResult(
        description="Review a stored order",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(
                    text=f"Review order {order_id} for correctness."
                ),
            )
        ],
    )


async def main() -> None:
    server = Server(
        "m3-shipping-example",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_read_resource=read_resource,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
```

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
