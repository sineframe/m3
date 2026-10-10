"""Q44 names-sanitize-collision: two tools whose names differ only by `.` vs `_`.

Clients that rewrite tool names to `[A-Za-z0-9_-]` map both to `fetch_page`.
Each tool returns its own code, so the model can report both only when both
tools are individually reachable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.lowlevel import error, serve, text

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"url": {"type": "string"}},
    "required": ["url"],
    "additionalProperties": False,
}
CODES = {"fetch.page": "ZK41", "fetch_page": "QW87"}

TOOLS = [
    types.Tool(
        name="fetch.page",
        description="Fetch a page (dotted tool). Returns a code.",
        input_schema=SCHEMA,
    ),
    types.Tool(
        name="fetch_page",
        description="Fetch a page (underscore tool). Returns a code.",
        input_schema=SCHEMA,
    ),
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    if name not in CODES or not isinstance(arguments.get("url"), str):
        return error("provide url")
    return text(f"{name} code: {CODES[name]}")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
