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
