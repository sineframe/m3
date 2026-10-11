"""Q37 schema-large-optional-unions: a 120-field, Pydantic-style schema (> 5 KB).

Every optional field is `anyOf [string, null]`, as Pydantic emits for
`Optional[str]`. `severity` is an `anyOf` of an enum and null, so its allowed
values exist only inside a composition. Codex compacts tool schemas larger than
5,000 bytes (tools/src/json_schema/compaction.rs): after descriptions and
`$defs` are dropped it replaces every composition with `{}`, so the model
sees `severity?: unknown`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.lowlevel import error, serve, text

SEVERITIES = ["sev-3-low", "sev-2-medium", "sev-1-urgent"]
FIELD_COUNT = 120

_properties: dict[str, Any] = {
    "title": {"type": "string", "description": "Ticket title."}
}
for _index in range(FIELD_COUNT):
    _properties[f"field_{_index:03d}"] = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
        "description": f"Optional free-text attribute {_index} of the ticket.",
        "title": f"Field {_index:03d}",
    }
_properties["severity"] = {
    "anyOf": [{"type": "string", "enum": SEVERITIES}, {"type": "null"}],
    "default": None,
    "description": "Ticket severity.",
    "title": "Severity",
}

TOOLS = [
    types.Tool(
        name="create_ticket",
        description="Create a support ticket.",
        input_schema={
            "type": "object",
            "properties": _properties,
            "required": ["title"],
            "additionalProperties": False,
        },
    )
]
assert len(str(_properties)) > 10_000


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    if name != "create_ticket" or not isinstance(arguments.get("title"), str):
        return error("provide title")
    if arguments.get("severity") not in SEVERITIES:
        return error("invalid or missing severity")
    return text(f"ticket created with severity {arguments['severity']}")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
