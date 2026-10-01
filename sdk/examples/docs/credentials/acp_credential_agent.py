from __future__ import annotations

import json
import os
import subprocess
import sys


def send(value: dict[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def call_shipping_tool(server: dict[str, object]) -> dict[str, object]:
    environment = {
        name: os.environ[name]
        for name in ("HOME", "PATH", "LANG", "LC_ALL", "TZ", "PYTHONIOENCODING")
        if name in os.environ
    }
    for item in server.get("env", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            environment[item["name"]] = str(item.get("value", ""))
    process = subprocess.Popen(
        [
            str(server["command"]),
            *(str(argument) for argument in server.get("args", [])),
        ],
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
                    "clientInfo": {"name": "credentials-example", "version": "1"},
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
                    raise RuntimeError("MCP request failed")
                if request["method"] == "tools/call":
                    result = response["result"]
        return result
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


session_id = "credential-session"
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
        send(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {"sessionId": session_id},
            }
        )
    elif method == "session/prompt":
        actual_key = os.environ.get("PROVIDER_API_KEY")
        expected_home = os.environ.get("EXPECTED_PARENT_HOME")
        isolated_home = os.environ.get("HOME")
        if not actual_key or actual_key != "dummy-agent-key":
            raise RuntimeError("ACP credential reference was not resolved")
        if not expected_home or expected_home == isolated_home:
            raise RuntimeError("ACP HOME was not isolated from parent HOME")
        server = next(item for item in servers if item.get("name") == "shipping")
        result = call_shipping_tool(server)
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "shipping-call",
                        "title": "shipping:shipping_quote",
                        "rawInput": {"weight_kg": 2, "zone": "local"},
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
                            "text": "Dummy credential resolved; ACP HOME isolated.",
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
