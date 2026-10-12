"""A stdio MCP server with a configurable startup delay."""

import json
import os
import sys
import time


def send(request_id, result):
    response = {"jsonrpc": "2.0", "id": request_id, "result": result}
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


def main():
    time.sleep(float(os.environ.get("DELAY_S", "0")))
    for line in sys.stdin:
        request = json.loads(line)
        request_id = request.get("id")
        if request_id is None:
            continue
        method = request.get("method")
        if method == "initialize":
            send(
                request_id,
                {
                    "protocolVersion": request["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "slow", "version": "1"},
                },
            )
        elif method == "tools/list":
            send(
                request_id,
                {
                    "tools": [
                        {
                            "name": "lookup",
                            "description": "Return the code.",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            )
        elif method == "tools/call":
            send(request_id, {"content": [{"type": "text", "text": "code: ALPHA-7"}]})
        else:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "method not found"},
            }
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
