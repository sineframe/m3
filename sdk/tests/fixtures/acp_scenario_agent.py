"""Small real ACP agent fixture used by public SDK E2E tests.

The fixture deliberately speaks ACP over stdin/stdout and invokes the MCP
server supplied in ``session/new`` over stdio.  It is test-only: the published
ACP adapter remains responsible for the protocol and lifecycle boundary.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def _send(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _server_environment(server: dict[str, Any]) -> dict[str, str]:
    value = server.get("env") or []
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    return {
        str(item["name"]): str(item["value"])
        for item in value
        if isinstance(item, dict) and "name" in item and "value" in item
    }


def _start_mcp(server: dict[str, Any]) -> subprocess.Popen[str]:
    command = server.get("command")
    if not command:
        raise RuntimeError("scenario fixture requires a stdio MCP server")
    environment = os.environ.copy()
    environment.update(_server_environment(server))
    return subprocess.Popen(
        [str(command), *(str(item) for item in server.get("args") or [])],
        cwd=str(server["cwd"]) if isinstance(server.get("cwd"), str) else None,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _rpc(process: subprocess.Popen[str], number: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("scenario MCP pipes are unavailable")
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params}) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("scenario MCP server exited")
    response = json.loads(line)
    if not isinstance(response, dict):
        raise RuntimeError("scenario MCP response is not an object")
    return response


def run(mode: str) -> int:
    mcp: subprocess.Popen[str] | None = None
    server_name = "e2e-mcp"
    request_id = 0
    turns = 0
    session_id = "scenario-session"
    try:
        pid_marker = os.environ.get("MCP_PAL_ACP_PID_FILE")
        if pid_marker:
            Path(pid_marker).write_text(str(os.getpid()), encoding="utf-8")
        for line in sys.stdin:
            request = json.loads(line)
            method = request.get("method")
            ident = request.get("id")
            params = request.get("params") or {}
            if method == "initialize":
                _send({"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": 1}})
            elif method == "session/new":
                servers = params.get("mcpServers") or []
                if servers and isinstance(servers[0], dict):
                    server_name = str(servers[0].get("name") or server_name)
                marker = os.environ.get("MCP_PAL_ACP_MARKER")
                if marker:
                    with open(marker, "w", encoding="utf-8") as output:
                        json.dump(params, output, separators=(",", ":"))
                if servers:
                    mcp = _start_mcp(servers[0])
                    request_id += 1
                    _rpc(mcp, request_id, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "scenario", "version": "1"}})
                    request_id += 1
                    listing = _rpc(mcp, request_id, "tools/list", {})
                    cursor = (listing.get("result") or {}).get("nextCursor") if isinstance(listing, dict) else None
                    if isinstance(cursor, str) and cursor:
                        request_id += 1
                        _rpc(mcp, request_id, "tools/list", {"cursor": cursor})
                _send({"jsonrpc": "2.0", "id": ident, "result": {"sessionId": session_id}})
            elif method == "session/prompt":
                turns += 1
                prompt = " ".join(
                    item.get("text", "")
                    for item in params.get("prompt", [])
                    if isinstance(item, dict) and item.get("type") == "text"
                )
                tool_name = "failure" if mode == "recover" and turns == 1 else "echo"
                request_id += 1
                result = _rpc(mcp, request_id, "tools/call", {"name": tool_name, "arguments": {"text": prompt}}).get("result") if mcp is not None else None
                status = "failed" if isinstance(result, dict) and result.get("isError") else "completed"
                _send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": {"sessionUpdate": "tool_call_update", "toolCallId": "scenario-call", "title": server_name + ":" + tool_name, "status": status, "content": [{"type": "content", "content": {"type": "text", "text": str(result)}}]}}})
                if mode == "loss":
                    os._exit(17)
                _send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "recovered:" + prompt}}}})
                _send({"jsonrpc": "2.0", "id": ident, "result": {"stopReason": "end_turn"}})
            elif method == "session/cancel":
                continue
            elif ident is not None:
                _send({"jsonrpc": "2.0", "id": ident, "result": {}})
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
    raise SystemExit(run(sys.argv[1] if len(sys.argv) > 1 else "recover"))
