"""Q47 lifecycle-slow-tool-call: `slow_job` takes 75 seconds to answer, sends no progress.

Clients with a 60 s default request timeout (the MCP SDKs' default) give up
before the result arrives. The marker `job-SJ75` exists only in the result.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.lowlevel import error, serve, text

DELAY_SECONDS = 75

TOOLS = [
    types.Tool(
        name="slow_job",
        description=f"Run a long job. It takes about {DELAY_SECONDS} seconds; wait for the result.",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    )
]


async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    if name != "slow_job" or not isinstance(arguments.get("id"), str):
        return error("provide id")
    await asyncio.sleep(DELAY_SECONDS)
    return text(f"Job {arguments['id']} finished: job-SJ75")


if __name__ == "__main__":
    serve("quirk", TOOLS, handle)
