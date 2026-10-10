"""Q08 args-omit-optional-default: `search` has optional params with `default`s."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.lowlevel import error, serve, text

TOOLS = [
    types.Tool(
        name="search",
        description="Search the catalogue. Only query is required.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "page_size": {
                    "type": "integer",
                    "default": 10,
                    "description": "Results per page.",
                },
                "filter": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Optional filter expression.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    query = arguments.get("query")
    if name != "search" or not isinstance(query, str):
        return error("provide query")
    return text(f"results for {query}")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
