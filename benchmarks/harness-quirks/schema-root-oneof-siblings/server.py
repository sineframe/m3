"""Q02 schema-root-oneof-siblings: `find_tenant` has a root-level oneOf next to its properties.

Codex code mode renders a schema with a root `oneOf` as the union of its
variants only (code-mode-protocol json_schema_types.rs `render_map`), so the
sibling `properties` (and their names) never reach the model.
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
        name="find_tenant",
        description="Find a tenant by billing key or serial number.",
        input_schema={
            "type": "object",
            "properties": {
                "bkey_v2": {
                    "type": "string",
                    "description": "Opaque billing key issued by the billing system (format T-nnnn).",
                },
                "serial_no": {
                    "type": "integer",
                    "description": "Tenant serial number.",
                },
            },
            "oneOf": [{"required": ["bkey_v2"]}, {"required": ["serial_no"]}],
            "additionalProperties": False,
        },
    )
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    if name != "find_tenant" or arguments.get("bkey_v2") != "T-5521":
        return error("missing or invalid identifier")
    return text("tenant found")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
