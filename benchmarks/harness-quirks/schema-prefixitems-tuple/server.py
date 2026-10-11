"""Q38 schema-prefixitems-tuple: `set_pair` takes a [string, integer] tuple (prefixItems).

Codex's schema sanitizer keeps `prefixItems` only as an "array" hint and
substitutes `items: {type: string}` (tools/src/json_schema.rs
`sanitize_json_schema`), so code mode shows the model `Array<string>`.
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
        name="set_pair",
        description="Store a named integer setting.",
        input_schema={
            "type": "object",
            "properties": {
                "pair": {
                    "type": "array",
                    "prefixItems": [{"type": "string"}, {"type": "integer"}],
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "[setting name, integer value]",
                }
            },
            "required": ["pair"],
            "additionalProperties": False,
        },
    )
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    pair = arguments.get("pair")
    valid = (
        name == "set_pair"
        and isinstance(pair, list)
        and len(pair) == 2
        and isinstance(pair[0], str)
        and isinstance(pair[1], int)
        and not isinstance(pair[1], bool)
    )
    if not valid:
        return error("pair must be [string, integer]")
    return text(f"stored {pair[0]}={pair[1]}")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
