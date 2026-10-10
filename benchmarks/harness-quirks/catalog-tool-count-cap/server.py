"""Q23 catalog-tool-count-cap: one server advertising 2,049 tools (all minimal).

Codex caps a server's tools/list at 2,048 items (codex-mcp pagination.rs
MAX_MCP_CATALOG_ITEMS) and fails the whole listing instead of truncating, so
none of the server's tools are exposed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.lowlevel import error, serve, text

TOOL_COUNT = 2049
TOOLS = [
    types.Tool(
        name=f"t{index:04d}",
        description=f"Tool {index}.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    for index in range(TOOL_COUNT)
]
NAMES = {tool.name for tool in TOOLS}
assert len(NAMES) == TOOL_COUNT


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    if name not in NAMES or not isinstance(arguments.get("text"), str):
        return error("bad call")
    return text(f"{name} ok")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
