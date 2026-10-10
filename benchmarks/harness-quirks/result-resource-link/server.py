"""Q11 result-resource-link: `view_item` returns a text block plus a resource_link.

The marker `item-RL42` appears only in the resource_link's name, so the agent
can report it only if the resource_link block reached the model.
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
        name="view_item",
        description="Show an item. Returns a link to the item resource.",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    )
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    item_id = arguments.get("id")
    if name != "view_item" or not isinstance(item_id, str):
        return error("provide id")
    return types.CallToolResult(
        content=[
            types.TextContent(
                text=f"Item {item_id} is available as a linked resource."
            ),
            types.ResourceLink(
                uri=f"quirk://items/RL{item_id}",
                name=f"item-RL{item_id}",
                mime_type="text/plain",
            ),
        ]
    )


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
