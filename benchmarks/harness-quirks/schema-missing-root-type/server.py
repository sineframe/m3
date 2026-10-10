"""Q29 schema-missing-root-type: one tool's inputSchema has no `type: "object"`.

`ping` is a normal sibling. `legacy_echo` declares `inputSchema` with
`properties` but no root `type`. The MCP spec asks for `type: "object"`, but
many hand-rolled servers omit it. Claude Code rejects the whole tools/list
response, so even the valid sibling `ping` disappears (the server stays
"connected" with zero tools).

This server speaks JSON-RPC over stdio directly (stdlib only): the SDK's
low-level server refuses to emit a schema without a root `type`
(`Handler returned an invalid result`), which would normalise the quirk away.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.call_log import log_call

MODERN = "2026-07-28"
META = "io.modelcontextprotocol/protocolVersion"
SERVER_INFO = {"name": "quirk", "version": "1.0.0"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "ping",
        "description": "Echo text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "legacy_echo",
        "description": "Echo text back (schema lacks a root type).",
        # No root "type": the quirk.
        "inputSchema": {"properties": {"text": {"type": "string"}}},
    },
]


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def result_for(request_id: Any, result: dict[str, Any], modern: bool) -> None:
    if modern:
        result = {
            **result,
            "resultType": "complete",
            "ttlMs": 0,
            "cacheScope": "private",
            "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO},
        }
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    log_call("quirk", name, arguments)
    value = arguments.get("text")
    if name not in {"ping", "legacy_echo"} or not isinstance(value, str):
        return {"content": [{"type": "text", "text": "bad call"}], "isError": True}
    return {"content": [{"type": "text", "text": value}]}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        method, request_id = message.get("method"), message.get("id")
        params = message.get("params") or {}
        modern = (params.get("_meta") or {}).get(META) == MODERN
        if request_id is None:
            continue  # notifications
        if method == "initialize":
            result_for(
                request_id,
                {
                    "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                },
                False,
            )
        elif method == "server/discover":
            result_for(
                request_id,
                {
                    "supportedVersions": [MODERN],
                    "capabilities": {"tools": {"listChanged": False}},
                },
                modern,
            )
        elif method == "tools/list":
            result_for(request_id, {"tools": TOOLS}, modern)
        elif method == "tools/call":
            result_for(
                request_id,
                call(params.get("name", ""), params.get("arguments") or {}),
                modern,
            )
        elif method == "ping":
            result_for(request_id, {}, modern)
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )


if __name__ == "__main__":
    main()
