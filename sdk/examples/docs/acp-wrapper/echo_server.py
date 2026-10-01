from __future__ import annotations

import json
import sys


def reply(value: dict[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    identifier = request.get("id")
    if method == "initialize":
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "echo", "version": "1"},
                },
            }
        )
    elif method == "tools/list":
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Return the supplied text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                                "additionalProperties": False,
                            },
                        }
                    ]
                },
            }
        )
    elif method == "tools/call":
        value = request.get("params", {}).get("arguments", {}).get("text", "")
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "content": [{"type": "text", "text": value}],
                    "structuredContent": {"echo": value},
                },
            }
        )
    elif identifier is not None:
        reply({"jsonrpc": "2.0", "id": identifier, "result": {}})
