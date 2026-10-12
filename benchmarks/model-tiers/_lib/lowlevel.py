"""Minimal stdio runner around the low-level MCP server.

Quirk servers hand-write their Tool objects and JSON schemas. Nothing here
normalises schemas or results, so the quirk reaches the wire unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from _lib.call_log import log_call

CallHandler = Callable[[str, dict[str, Any]], Awaitable[types.CallToolResult]]


def error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(text=text)], is_error=True)


def text(value: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(text=value)])


def serve(name: str, tools: Sequence[types.Tool], handle: CallHandler) -> None:
    async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(tools))

    async def call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        log_call(name, params.name, arguments)
        return await handle(params.name, arguments)

    async def main() -> None:
        server: Server[object] = Server(
            name, version="1.0.0", on_list_tools=list_tools, on_call_tool=call_tool
        )
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )

    anyio.run(main)
