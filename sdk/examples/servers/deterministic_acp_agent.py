"""Deterministic local ACP agent used by the public SDK examples.

This is a small protocol harness, not an LLM.  It invokes the MCP server
provided by ``session/new`` over stdio and reports the real tool result back
through ACP, so examples exercise both process boundaries without credentials
or network access.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any


def _send(value: dict[str, Any]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def _start_server(server: dict[str, Any]) -> subprocess.Popen[str]:
    command = server.get("command")
    if not isinstance(command, str) or not command:
        raise RuntimeError("deterministic ACP agent requires a stdio MCP server")
    environment = os.environ.copy()
    for item in server.get("env") or ():
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            environment[item["name"]] = str(item.get("value", ""))
    return subprocess.Popen(
        [command, *(str(arg) for arg in server.get("args") or ())],
        cwd=server.get("cwd") if isinstance(server.get("cwd"), str) else None,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _rpc(
    process: subprocess.Popen[str], request_id: int, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("MCP stdio is unavailable")
    process.stdin.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        + "\n"
    )
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("MCP server exited")
    response = json.loads(line)
    if not isinstance(response, dict):
        raise TypeError("MCP response is not an object")
    return response


def run() -> int:
    server: dict[str, Any] | None = None
    mcp: subprocess.Popen[str] | None = None
    request_id = 0
    turn = 0
    session_id = "deterministic-example-session"
    try:
        for line in sys.stdin:
            request = json.loads(line)
            method = request.get("method")
            identifier = request.get("id")
            params = request.get("params") or {}
            if method == "initialize":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": identifier,
                        "result": {"protocolVersion": 1},
                    }
                )
            elif method == "session/new":
                candidates = params.get("mcpServers") or []
                server = (
                    candidates[0]
                    if candidates and isinstance(candidates[0], dict)
                    else None
                )
                mcp = _start_server(server or {})
                request_id += 1
                _rpc(
                    mcp,
                    request_id,
                    "initialize",
                    {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "example", "version": "1"},
                    },
                )
                request_id += 1
                _rpc(mcp, request_id, "tools/list", {})
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": identifier,
                        "result": {"sessionId": session_id},
                    }
                )
            elif method == "session/prompt":
                turn += 1
                weight = 2 if turn == 1 else 3
                zone = "local" if turn == 1 else "regional"
                arguments = {"weight_kg": weight, "zone": zone}
                request_id += 1
                result = (
                    _rpc(
                        mcp,
                        request_id,
                        "tools/call",
                        {"name": "shipping_quote", "arguments": arguments},
                    ).get("result", {})
                    if mcp is not None
                    else {}
                )
                _send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call_update",
                                "toolCallId": f"example-call-{turn}",
                                "title": "example-mcp:shipping_quote",
                                "rawInput": arguments,
                                "rawOutput": result,
                                "status": "completed",
                            },
                        },
                    }
                )
                _send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": f"quoted {zone}"},
                            },
                        },
                    }
                )
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": identifier,
                        "result": {"stopReason": "end_turn"},
                    }
                )
            elif identifier is not None:
                _send({"jsonrpc": "2.0", "id": identifier, "result": {}})
    finally:
        if mcp is not None:
            mcp.terminate()
            try:
                mcp.wait(timeout=2)
            except subprocess.TimeoutExpired:
                mcp.kill()
                mcp.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
