"""Q35 args-int64-precision: `get_record` takes an `id` integer that does not fit a JS double.

1234567890123456789 > 2**53, so any client that routes the argument through a
JavaScript number delivers 1234567890123456800. A client may also send the id
as a string. A strict server accepts only an exact JSON integer.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.lowlevel import error, serve, text

RECORD_ID = 1234567890123456789

TOOLS = [
    types.Tool(
        name="get_record",
        description="Fetch a record by its 64-bit integer id.",
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "64-bit record id."}
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    )
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    record_id = arguments.get("id")
    if (
        name != "get_record"
        or isinstance(record_id, bool)
        or not isinstance(record_id, int)
    ):
        return error("id must be an integer")
    if record_id != RECORD_ID:
        return error(f"no record with id {record_id}")
    return text(f"record {record_id} found")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
