"""Deterministic arithmetic MCP server used by the live evaluation example.

The server intentionally exposes several overlapping arithmetic tools.  This
lets a harness choose from the complete advertised catalog while the example
evaluator checks both the final answer and (optionally) the observed tool.

Run this module directly to serve MCP over stdio::

    python -m servers.math_mcp_server
"""

from __future__ import annotations

import json
import math
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    required: tuple[str, ...],
) -> types.Tool:
    return types.Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    )


_NUMBER = {"type": "number"}
_BINARY = {"a": _NUMBER, "b": _NUMBER}

_TOOLS = (
    _tool("add_tool", "Add two numbers.", _BINARY, required=("a", "b")),
    _tool("subtract_tool", "Subtract b from a.", _BINARY, required=("a", "b")),
    _tool("multiply_tool", "Multiply two numbers.", _BINARY, required=("a", "b")),
    _tool(
        "divide_tool",
        "Divide a by b.",
        _BINARY,
        required=("a", "b"),
    ),
    _tool("power_tool", "Raise a to the power b.", _BINARY, required=("a", "b")),
    _tool(
        "modulo_tool",
        "Return the remainder of a divided by b.",
        _BINARY,
        required=("a", "b"),
    ),
    _tool(
        "average_tool",
        "Compute the arithmetic mean of a list of numbers.",
        {"values": {"type": "array", "items": _NUMBER, "minItems": 1}},
        required=("values",),
    ),
    _tool(
        "min_tool",
        "Return the smallest number in a list.",
        {"values": {"type": "array", "items": _NUMBER, "minItems": 1}},
        required=("values",),
    ),
    _tool(
        "max_tool",
        "Return the largest number in a list.",
        {"values": {"type": "array", "items": _NUMBER, "minItems": 1}},
        required=("values",),
    ),
    _tool(
        "sqrt_tool",
        "Return the non-negative square root of a number.",
        {"value": _NUMBER},
        required=("value",),
    ),
)


def _result(value: float | int) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(text=json.dumps({"result": value}))],
        structured_content={"result": value},
    )


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(text=message)],
        is_error=True,
    )


async def _list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(tools=list(_TOOLS))


async def _call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    arguments = params.arguments or {}
    name = params.name

    try:
        if name == "add_tool":
            value = arguments["a"] + arguments["b"]
        elif name == "subtract_tool":
            value = arguments["a"] - arguments["b"]
        elif name == "multiply_tool":
            value = arguments["a"] * arguments["b"]
        elif name == "divide_tool":
            value = arguments["a"] / arguments["b"]
        elif name == "power_tool":
            value = arguments["a"] ** arguments["b"]
        elif name == "modulo_tool":
            value = arguments["a"] % arguments["b"]
        elif name == "average_tool":
            value = sum(arguments["values"]) / len(arguments["values"])
        elif name == "min_tool":
            value = min(arguments["values"])
        elif name == "max_tool":
            value = max(arguments["values"])
        elif name == "sqrt_tool":
            value = math.sqrt(arguments["value"])
        else:
            return _error(f"unknown tool: {name}")
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return _error(f"invalid arithmetic request: {exc}")

    return _result(value)


async def main() -> None:
    server: Server[object] = Server(
        "m3-math-server",
        version="1.0.0",
        instructions="Deterministic arithmetic tools for M3 evaluations",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    anyio.run(main)
