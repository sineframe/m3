"""Control server: one plain, valid `echo` tool.

Runs next to every quirk server, so a quirk that poisons its own server can't
poison the control, and alone as the per-agent baseline.
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
        name="echo",
        description="Return the given text unchanged.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    value = arguments.get("text")
    if name != "echo" or not isinstance(value, str):
        return error("bad call")
    return text(value)


if __name__ == "__main__":
    serve("control", TOOLS, handle)
