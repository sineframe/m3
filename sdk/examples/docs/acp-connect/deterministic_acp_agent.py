from __future__ import annotations

import json
import os
import subprocess
import sys


def send(value: dict[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def call_mcp(
    server: dict[str, object],
    method: str,
    params: dict[str, object],
    request_id: int,
) -> dict[str, object]:
    command = str(server["command"])
    environment = os.environ.copy()
    for item in server.get("env", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            environment[item["name"]] = str(item.get("value", ""))
    process = subprocess.Popen(
        [command, *(str(arg) for arg in server.get("args", []))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=environment,
        cwd=server.get("cwd"),
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "local-acp-example", "version": "1"},
            },
        }
        process.stdin.write(json.dumps(initialize) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        if "error" in response:
            raise RuntimeError("MCP initialization failed")
        process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        for identifier, current_method, current_params in (
            (2, "tools/list", {}),
            (request_id, method, params),
        ):
            request = {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": current_method,
                "params": current_params,
            }
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            result = json.loads(process.stdout.readline())
            if "error" in result:
                raise RuntimeError("MCP request failed")
        return result["result"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


session_id = "local-acp-session"
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
        prompt_text = " ".join(
            item["text"]
            for item in params.get("prompt", [])
            if item.get("type") == "text"
        )
        try:
            instruction = json.loads(prompt_text)
        except json.JSONDecodeError:
            if prompt_text != (
                "Call shipping_quote once for weight_kg 2 in zone local. "
                "Return the amount from the tool result."
            ):
                raise
            instruction = {
                "server": "shipping",
                "tool": "shipping_quote",
                "arguments": {"weight_kg": 2, "zone": "local"},
            }
        server = next(item for item in servers if item["name"] == instruction["server"])
        result = call_mcp(
            server,
            "tools/call",
            {"name": instruction["tool"], "arguments": instruction["arguments"]},
            3,
        )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "shipping-call-1",
                        "title": f"{instruction['server']}:{instruction['tool']}",
                        "rawInput": instruction["arguments"],
                        "rawOutput": result,
                        "status": "completed",
                    },
                },
            }
        )
        text = "".join(
            item.get("text", "")
            for item in result.get("content", [])
            if isinstance(item, dict)
        )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    },
                },
            }
        )
        send({"jsonrpc": "2.0", "id": identifier, "result": {"stopReason": "end_turn"}})
    elif identifier is not None:
        send({"jsonrpc": "2.0", "id": identifier, "result": {}})
