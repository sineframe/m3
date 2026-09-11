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


def _close_server(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _initialize_server(process: subprocess.Popen[str]) -> None:
    _rpc(
        process,
        1,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "example", "version": "1"},
        },
    )
    _rpc(process, 2, "tools/list", {})


def _result_text(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        text = "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
        if text:
            return text
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def run() -> int:
    servers: dict[str, dict[str, Any]] = {}
    active_server: str | None = None
    mcp: subprocess.Popen[str] | None = None
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
                servers = {
                    str(candidate["name"]): candidate
                    for candidate in candidates
                    if isinstance(candidate, dict)
                    and isinstance(candidate.get("name"), str)
                }
                active_server = next(iter(servers), None)
                mcp = (
                    _start_server(servers[active_server])
                    if active_server is not None
                    else None
                )
                if mcp is not None:
                    _initialize_server(mcp)
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": identifier,
                        "result": {"sessionId": session_id},
                    }
                )
            elif method == "session/prompt":
                turn += 1
                prompt = " ".join(
                    item.get("text", "")
                    for item in params.get("prompt", ())
                    if isinstance(item, dict) and item.get("type") == "text"
                )
                try:
                    instruction = json.loads(prompt)
                except (TypeError, ValueError):
                    instruction = {}
                if not isinstance(instruction, dict):
                    instruction = {}
                requested_server = instruction.get("server")
                explicit_instruction = any(
                    key in instruction for key in ("server", "tool", "arguments")
                )
                selected_server = (
                    requested_server
                    if isinstance(requested_server, str) and requested_server in servers
                    else active_server
                )
                if selected_server is None or selected_server not in servers:
                    raise RuntimeError("deterministic ACP agent has no MCP server")
                if selected_server != active_server:
                    _close_server(mcp)
                    mcp = _start_server(servers[selected_server])
                    _initialize_server(mcp)
                    active_server = selected_server
                if explicit_instruction:
                    tool = instruction.get("tool", "shipping_quote")
                    arguments = instruction.get("arguments", {})
                    if not isinstance(tool, str) or not isinstance(arguments, dict):
                        raise ValueError(
                            "matrix instructions require tool and object arguments"
                        )
                else:
                    weight = 2 if turn == 1 else 3
                    zone = "local" if turn == 1 else "regional"
                    tool = "shipping_quote"
                    arguments = {"weight_kg": weight, "zone": zone}
                if mcp is None:
                    raise RuntimeError(
                        "deterministic ACP MCP connection is unavailable"
                    )
                result = _rpc(
                    mcp,
                    turn + 2,
                    "tools/call",
                    {"name": tool, "arguments": arguments},
                ).get("result", {})
                if not isinstance(result, dict):
                    raise TypeError("MCP tool result is not an object")
                _send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call_update",
                                "toolCallId": f"example-call-{turn}",
                                "title": f"{selected_server}:{tool}",
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
                                "content": {
                                    "type": "text",
                                    "text": _result_text(result)
                                    if explicit_instruction
                                    else f"quoted {zone}",
                                },
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
        _close_server(mcp)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
