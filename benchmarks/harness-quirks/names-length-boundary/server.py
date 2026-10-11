"""Q09 names-length-boundary: two tools whose full Claude Code names are 64 and 65 chars.

Claude Code names MCP tools `mcp__<server>__<tool>`; the server alias is `quirk`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.lowlevel import error, serve, text

PREFIX = "mcp__quirk__"
TOOL_64 = "len64_" + "a" * (64 - len(PREFIX) - len("len64_"))
TOOL_65 = "len65_" + "b" * (65 - len(PREFIX) - len("len65_"))
assert len(PREFIX + TOOL_64) == 64 and len(PREFIX + TOOL_65) == 65

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}

TOOLS = [
    types.Tool(
        name=TOOL_64, description="Echo text (64-char full name).", input_schema=SCHEMA
    ),
    types.Tool(
        name=TOOL_65, description="Echo text (65-char full name).", input_schema=SCHEMA
    ),
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    value = arguments.get("text")
    if name not in {TOOL_64, TOOL_65} or not isinstance(value, str):
        return error("bad call")
    return text(value)


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
