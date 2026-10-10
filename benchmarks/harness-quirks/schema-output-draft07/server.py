"""Q07 schema-output-draft07: `get_item` declares a draft-07 `$schema` in outputSchema.

This is what @modelcontextprotocol/sdk 1.x emits (typescript-sdk#2721).
The result's structuredContent matches the schema.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.lowlevel import error, serve

TOOLS = [
    types.Tool(
        name="get_item",
        description="Look up an item by id.",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        output_schema={
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
            "required": ["id", "name"],
            "additionalProperties": False,
        },
    )
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    item_id = arguments.get("id")
    if name != "get_item" or not isinstance(item_id, str):
        return error("provide id")
    item = {"id": item_id, "name": f"item-{item_id}"}
    return types.CallToolResult(
        content=[types.TextContent(text=f"item {item_id} is named item-{item_id}")],
        structured_content=item,
    )


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
