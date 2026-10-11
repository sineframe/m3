"""Q46 schema-boolean-subschema: one tool has a boolean property schema (`"payload": true`).

`true` is a valid JSON Schema 2020-12 subschema. The expectation targets the
innocent sibling `ping`: a client that rejects the whole tools/list result
because of `put` loses `ping` too.
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
        name="ping",
        description="Echo a message.",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="put",
        description="Store an arbitrary JSON payload.",
        input_schema={
            "type": "object",
            "properties": {"payload": True},
            "required": ["payload"],
        },
    ),
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    if name == "ping" and isinstance(arguments.get("msg"), str):
        return text(f"pong {arguments['msg']}")
    if name == "put" and "payload" in arguments:
        return text("stored")
    return error("bad call")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
