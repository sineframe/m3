from __future__ import annotations

import json
import os
import subprocess
import sys

PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."


def send(value: dict[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def call_tool(server: dict[str, object]) -> dict[str, object]:
    environment = os.environ.copy()
    for item in server.get("env", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            environment[item["name"]] = str(item.get("value", ""))
    process = subprocess.Popen(
        [str(server["command"]), *(str(value) for value in server.get("args", []))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=environment,
        cwd=server.get("cwd"),
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        requests = (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "local-acp-fixture", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "shipping_quote",
                    "arguments": {"weight_kg": 2, "zone": "local"},
                },
            },
        )
        result: dict[str, object] = {}
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            if "id" in request:
                response = json.loads(process.stdout.readline())
                if "error" in response:
                    raise RuntimeError("local MCP request failed")
                result = response.get("result", {})
        return result
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


session_id = "local-multi-harness-acp"
servers: list[dict[str, object]] = []
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    identifier = request.get("id")
    params = request.get("params") or {}
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": identifier, "result": {"protocolVersion": 1}})
    elif method == "session/new":
        servers = params.get("mcpServers", [])
        send({"jsonrpc": "2.0", "id": identifier, "result": {"sessionId": session_id}})
    elif method == "session/prompt":
        prompt = " ".join(
            item["text"] for item in params.get("prompt", []) if item.get("type") == "text"
        )
        if prompt != PROMPT:
            raise RuntimeError("unexpected prompt for deterministic ACP fixture")
        server = next(item for item in servers if item["name"] == "shipping")
        result = call_tool(server)
        arguments = {"weight_kg": 2, "zone": "local"}
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "shipping-call-1",
                        "title": "shipping:shipping_quote",
                        "rawInput": arguments,
                        "rawOutput": result,
                        "status": "completed",
                    },
                },
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {
                            "type": "text",
                            "text": "The local quote is 9.00 USD.",
                        },
                    },
                },
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {"stopReason": "end_turn"},
            }
        )
    elif identifier is not None:
        send({"jsonrpc": "2.0", "id": identifier, "result": {}})
