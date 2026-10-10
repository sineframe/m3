"""Q45 result-missing-structured-content: `get_item` declares an outputSchema but returns text only.

A very common server bug (hand-written servers, SDK wrappers that always
declare outputSchema). The marker `item-OS77` exists only in the text block,
so the agent can report it only if the client delivers a result that has no
structuredContent although the tool declared an outputSchema.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.lowlevel import error, serve, text

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
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    item_id = arguments.get("id")
    if name != "get_item" or not isinstance(item_id, str):
        return error("provide id")
    # Text only: no structuredContent, although outputSchema is declared.
    return text(f"Item {item_id} is named item-OS77.")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
