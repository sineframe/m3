"""Packaged dependency-free MCP echo server used by ACP probes."""

import json
import sys
from typing import Any


def send(i: Any, result: Any) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": i, "result": result}), flush=True)


for line in sys.stdin:
    try:
        q = json.loads(line)
    except Exception:
        continue
    if q.get("method") == "initialize":
        send(
            q.get("id"),
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-server", "version": "0.1"},
            },
        )
    elif q.get("method") == "tools/list":
        send(
            q.get("id"),
            {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]},
        )
    elif q.get("method") == "tools/call":
        send(
            q.get("id"),
            {
                "content": [
                    {
                        "type": "text",
                        "text": q.get("params", {})
                        .get("arguments", {})
                        .get("text", ""),
                    }
                ],
                "isError": False,
            },
        )
