from __future__ import annotations

import json
import sys


def reply(message: dict[str, object]) -> None:
    print(json.dumps(message, separators=(",", ":")), flush=True)


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
                    "serverInfo": {"name": "shipping", "version": "1"},
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
                            "name": "shipping_quote",
                            "description": "Calculate a deterministic shipping quote",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "weight_kg": {"type": "number"},
                                    "zone": {
                                        "type": "string",
                                        "enum": ["local", "regional"],
                                    },
                                },
                                "required": ["weight_kg", "zone"],
                                "additionalProperties": False,
                            },
                        }
                    ]
                },
            }
        )
    elif method == "tools/call":
        arguments = request.get("params", {}).get("arguments", {})
        rate = 2 if arguments["zone"] == "local" else 3.5
        amount = round(5 + float(arguments["weight_kg"]) * rate, 2)
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "content": [{"type": "text", "text": f"{amount:.2f} USD"}],
                    "structuredContent": {"amount": amount, "currency": "USD"},
                },
            }
        )
    elif identifier is not None:
        reply({"jsonrpc": "2.0", "id": identifier, "result": {}})
