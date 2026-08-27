#!/usr/bin/env python3
"""Own an embedded SQLite SDK worker until the parent closes stdin."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.storage import SQLiteExecutionStore


async def _run(database: Path) -> None:
    store = SQLiteExecutionStore(database.resolve())
    async with AsyncMCPTestKit(store=store, env={}) as _kit:
        print("READY", flush=True)
        await asyncio.to_thread(sys.stdin.readline)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    asyncio.run(_run(Path(sys.argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
