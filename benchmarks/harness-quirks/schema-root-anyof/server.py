"""Q01 schema-root-anyof: `pick` has a ROOT-LEVEL anyOf in its inputSchema."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.lowlevel import error, serve, text

TOOLS = [
    types.Tool(
        name="pick",
        description="Record a choice. Provide a or b.",
        input_schema={
            "type": "object",
            "properties": {
                "a": {"type": "string", "description": "First option."},
                "b": {"type": "string", "description": "Second option."},
            },
            "anyOf": [{"required": ["a"]}, {"required": ["b"]}],
            "additionalProperties": False,
        },
    )
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    if name != "pick" or not ({"a", "b"} & set(arguments)):
        return error("provide a or b")
    return text(f"picked {arguments}")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
