"""Trackly MCP server over stdio: `server.py --variant V --nonce N`.

One Store(7, nonce) per process. Env MT_CALL_LOG appends one JSONL line per call. Never prints to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _backend.store import Store
from _lib.lowlevel import serve

from _server.surfaces import build_surface


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="good")
    parser.add_argument("--nonce", default="N0")
    ns = parser.parse_args()
    surface = build_surface(ns.variant)
    store = Store(7, ns.nonce)
    tools = [
        types.Tool(
            name=t["name"], description=t["description"], input_schema=t["inputSchema"]
        )
        for t in surface.tools
    ]
    seq = 0

    async def handle(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        nonlocal seq
        before = len(store.mutations)
        text, is_error = surface.dispatch(store, name, arguments)
        seq += 1
        path = os.environ.get("MT_CALL_LOG")
        if path:
            receipt = (
                store.mutations[-1]["receipt"]
                if len(store.mutations) > before
                else None
            )
            line = {
                "seq": seq,
                "tool": name,
                "arguments": arguments,
                "is_error": is_error,
                "receipt": receipt,
            }
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, sort_keys=True, default=str) + "\n")
        return types.CallToolResult(
            content=[types.TextContent(text=text)], is_error=is_error
        )

    serve("tracker", tools, handle)


if __name__ == "__main__":
    main()
