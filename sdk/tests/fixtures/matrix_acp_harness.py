#!/usr/bin/env python3
"""Real test ACP process that discovers and calls configured stdio MCP tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any
import uuid


def _send(value: object) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def _environment(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, list):
        return {
            str(item["name"]): str(item["value"])
            for item in value
            if isinstance(item, dict) and "name" in item and "value" in item
        }
    return {}


def _start_server(server: dict[str, Any]) -> subprocess.Popen[str]:
    command = server.get("command")
    if not isinstance(command, str) or not command:
        raise RuntimeError("matrix ACP fixture requires a stdio MCP server")
    environment = os.environ.copy()
    environment.update(_environment(server.get("env")))
    return subprocess.Popen(
        [command, *(str(item) for item in server.get("args") or [])],
        cwd=str(server["cwd"]) if isinstance(server.get("cwd"), str) else None,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _rpc(
    process: subprocess.Popen[str],
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("matrix MCP pipes are unavailable")
    process.stdin.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("matrix MCP server exited")
    response = json.loads(line)
    if not isinstance(response, dict):
        raise RuntimeError("matrix MCP response is invalid")
    return response


def _request_from_text(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _servers(values: object) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            continue
        name = value.get("name")
        key = str(name) if isinstance(name, str) else f"server-{index}"
        output[key] = value
    return output


def _invoke(
    servers: dict[str, dict[str, Any]], request: dict[str, Any]
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    requested_server = request.get("server")
    server_name = (
        requested_server
        if isinstance(requested_server, str) and requested_server in servers
        else next(iter(servers))
    )
    process = _start_server(servers[server_name])
    try:
        request_id = 1
        _rpc(
            process,
            request_id,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "matrix-acp", "version": "1"},
            },
        )
        request_id += 1
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            listing = _rpc(
                process,
                request_id,
                "tools/list",
                {} if cursor is None else {"cursor": cursor},
            )
            request_id += 1
            result = listing.get("result")
            if isinstance(result, dict):
                tools.extend(
                    item for item in result.get("tools", []) if isinstance(item, dict)
                )
                next_cursor = result.get("nextCursor")
                cursor = next_cursor if isinstance(next_cursor, str) else None
            else:
                cursor = None
            if cursor is None:
                break
        requested_tool = request.get("tool")
        tool_name = (
            requested_tool
            if isinstance(requested_tool, str)
            else next(
                str(tool["name"])
                for tool in tools
                if isinstance(tool.get("name"), str)
                and tool.get("name") != "failure"
            )
        )
        arguments = request.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {"text": str(request.get("query", "matrix search"))}
        response = _rpc(
            process,
            request_id,
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("matrix MCP tool result is invalid")
        return server_name, tool_name, arguments, result
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    servers: dict[str, dict[str, Any]] = {}
    session_id = "matrix-acp-session"
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
            servers = _servers(params.get("mcpServers"))
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "result": {"sessionId": session_id},
                }
            )
        elif method == "session/prompt":
            text = " ".join(
                item.get("text", "")
                for item in params.get("prompt", ())
                if isinstance(item, dict) and item.get("type") == "text"
            )
            server, tool, arguments, result = _invoke(
                servers, _request_from_text(text)
            )
            call_id = "matrix-acp-" + uuid.uuid4().hex
            _send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": call_id,
                            "title": f"{server}:{tool}",
                            "rawInput": arguments,
                            "rawOutput": result,
                            "status": "failed"
                            if result.get("isError")
                            else "completed",
                        },
                    },
                }
            )
            content = result.get("content")
            response_text = "".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ) if isinstance(content, list) else ""
            if not response_text:
                response_text = json.dumps(result, sort_keys=True, separators=(",", ":"))
            _send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": response_text},
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
        elif method == "session/cancel":
            continue
        elif identifier is not None:
            _send({"jsonrpc": "2.0", "id": identifier, "result": {}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
