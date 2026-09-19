#!/usr/bin/env python3
"""Own an embedded SQLite SDK worker until the parent closes stdin."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from m3.async_api import AsyncMCPTestKit
from m3.storage import SQLiteExecutionStore


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
