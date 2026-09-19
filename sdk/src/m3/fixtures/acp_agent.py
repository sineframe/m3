"""A deterministic ACP fixture agent for a structured non-ACP CLI.

This is intentionally a small fixture agent, not a generic adapter for
vendor-specific CLIs. It accepts ACP v1 on stdin/stdout, invokes one selected
stdio MCP server, and passes a structured JSON request to a configured
non-ACP command. It is useful for validating a local manifest and for the
keyless E2E; real agents should use their vendor-supported ACP implementation.

Run directly, for example::

    python -m m3.fixtures.acp_agent \
      --target python --target-args-json '["-m", "m3.fixtures.structured_cli"]'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from typing import Any, cast


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _rpc(
    process: subprocess.Popen[str],
    number: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": number, "method": method}
    if params is not None:
        request["params"] = params
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("selected MCP server exited before responding")
    response = json.loads(line)
    if "error" in response:
        raise RuntimeError("selected MCP server returned an error")
    return cast(dict[str, Any], response)


def _env_from_mcp(server: dict[str, Any]) -> dict[str, str]:
    raw = server.get("env") or {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    # ACP SDK schema serializes stdio env as [{name, value}, ...].
    return {
        str(item["name"]): str(item["value"])
        for item in raw
        if isinstance(item, dict) and "name" in item
    }


def _selected_stdio(server: dict[str, Any]) -> subprocess.Popen[str]:
    command = server.get("command")
    if not command:
        raise RuntimeError("ACP fixture agent supports stdio MCP servers only")
    env = os.environ.copy()
    env.update(_env_from_mcp(server))
    return subprocess.Popen(
        [str(command), *(str(arg) for arg in server.get("args") or [])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # The ACP fixture agent does not expose MCP stderr. Avoid an unread pipe that can
        # fill and deadlock a valid noisy server before its stdout response.
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )


def _target_result(
    command: str | None, args: list[str], request: dict[str, Any]
) -> str:
    if not command:
        return str(request.get("nonce") or "")
    completed = subprocess.run(
        [command, *args],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        raise RuntimeError("structured target exited unsuccessfully")
    line = next(
        (line.strip() for line in completed.stdout.splitlines() if line.strip()), ""
    )
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError("structured target did not return JSON") from exc
    if isinstance(value, dict) and isinstance(value.get("final_output"), str):
        return cast(str, value["final_output"])
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return cast(str, value["text"])
    raise RuntimeError("structured target JSON lacks final_output")


def run(*, target: str | None = None, target_args: list[str] | None = None) -> int:
    session_id = "acp-fixture-" + uuid.uuid4().hex[:12]
    mode_id = "default"
    config: dict[str, Any] = {}
    mcp: subprocess.Popen[str] | None = None
    request_counter = 0
    target_args = target_args or []
    try:
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                # ACP stdout is protocol-only.  A malformed incoming request
                # is answered as an ordinary JSON-RPC error.
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "invalid JSON"},
                    }
                )
                continue
            method = message.get("method")
            ident = message.get("id")
            params = message.get("params") or {}
            if method == "initialize":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": ident,
                        "result": {
                            "protocolVersion": 1,
                            "agentInfo": {
                                "name": "acp-fixture-agent",
                                "version": "1",
                            },
                            "agentCapabilities": {
                                "mcpCapabilities": {
                                    "stdio": True,
                                    "http": False,
                                    "sse": False,
                                }
                            },
                        },
                    }
                )
            elif method == "session/new":
                servers = params.get("mcpServers") or []
                if servers:
                    candidate = servers[0]
                    if isinstance(candidate, dict) and candidate.get("type") in {
                        "http",
                        "sse",
                    }:
                        raise RuntimeError(
                            "ACP fixture agent supports stdio MCP servers only"
                        )
                    mcp = _selected_stdio(candidate)
                    request_counter += 1
                    _rpc(
                        mcp,
                        request_counter,
                        "initialize",
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "acp-fixture-agent", "version": "1"},
                        },
                    )
                    request_counter += 1
                    _rpc(mcp, request_counter, "tools/list", {})
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": ident,
                        "result": {
                            "sessionId": session_id,
                            "modes": {
                                "currentModeId": mode_id,
                                "availableModes": [{"id": mode_id, "name": "Default"}],
                            },
                            "configOptions": [
                                {
                                    "id": "model",
                                    "name": "Model",
                                    "category": "model",
                                    "type": "select",
                                    "currentValue": "agent-default",
                                    "options": [
                                        {
                                            "value": "agent-default",
                                            "name": "Agent default",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                )
            elif method == "session/set_mode":
                mode_id = params.get("modeId", mode_id)
                _send({"jsonrpc": "2.0", "id": ident, "result": {"modeId": mode_id}})
            elif method == "session/set_config_option":
                config[params.get("configId", "")] = params.get("value")
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": ident,
                        "result": {
                            "configOptions": [
                                {
                                    "id": "model",
                                    "name": "Model",
                                    "category": "model",
                                    "type": "select",
                                    "currentValue": config.get(
                                        "model", "agent-default"
                                    ),
                                    "options": [
                                        {
                                            "value": "agent-default",
                                            "name": "Agent default",
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                )
            elif method == "session/cancel":
                # ACP cancellation is a notification and must not receive a
                # JSON-RPC response. Parent process cleanup remains the hard
                # cancellation boundary for synchronous target/MCP work.
                continue
            elif method == "session/prompt":
                text = " ".join(
                    item.get("text", "")
                    for item in params.get("prompt", [])
                    if isinstance(item, dict)
                )
                match = re.search(r"m3-probe-[0-9a-f]+|nonce-[A-Za-z0-9_.:-]+", text)
                nonce = match.group(0) if match else text.strip() or "acp-fixture-echo"
                wire_result = None
                if mcp is not None:
                    request_counter += 1
                    wire_result = _rpc(
                        mcp,
                        request_counter,
                        "tools/call",
                        {"name": "echo", "arguments": {"text": nonce}},
                    ).get("result")
                _send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_thought_chunk",
                                "content": {
                                    "type": "text",
                                    "text": "Calling the deterministic echo tool",
                                },
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
                                "sessionUpdate": "tool_call",
                                "toolCallId": "acp-fixture-call",
                                "title": "echo",
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
                                "sessionUpdate": "tool_call_update",
                                "toolCallId": "acp-fixture-call",
                                "status": "completed",
                                "content": [
                                    {
                                        "type": "content",
                                        "content": {"type": "text", "text": nonce},
                                    }
                                ],
                            },
                        },
                    }
                )
                output = _target_result(
                    target,
                    target_args,
                    {
                        "prompt": text,
                        "nonce": nonce,
                        "tool_result": wire_result,
                        "mode": mode_id,
                        "config": config,
                    },
                )
                _send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": output},
                            },
                        },
                    }
                )
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": ident,
                        "result": {"stopReason": "end_turn"},
                    }
                )
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m m3.fixtures.acp_agent")
    parser.add_argument("--target", help="non-ACP structured CLI executable")
    parser.add_argument(
        "--target-args-json", default="[]", help="JSON array of target arguments"
    )
    args = parser.parse_args(argv)
    try:
        target_args = json.loads(args.target_args_json)
        if not isinstance(target_args, list) or not all(
            isinstance(item, str) for item in target_args
        ):
            raise ValueError("--target-args-json must be a string array")
        return run(target=args.target, target_args=target_args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"python -m m3.fixtures.acp_agent: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
