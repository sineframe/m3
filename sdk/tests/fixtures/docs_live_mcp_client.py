"""Local native-process fixture helper that performs real MCP RPCs.

This is deliberately below the harness adapter: the fixture CLI reads the
configuration M3 wrote for its native client, starts the configured instrumented
server process, and sends initialize/list/call JSON-RPC messages over stdio.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, supported by the SDK test extra.
    import tomli as tomllib

_MCP_SESSIONS: dict[str, tuple[subprocess.Popen[str], list[Any], int]] = {}


def prompt_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(prompt_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(prompt_text(item) for item in value)
    return ""


def _arguments(tool: str, prompt: str) -> dict[str, Any]:
    if tool == "shipping_quote":
        weight = re.search(r"weight_kg\s*(?:=|is)?\s*(\d+(?:\.\d+)?)", prompt)
        zone = re.search(r"zone\s*(?:=|is)?\s*([a-z]+)", prompt)
        return {
            "weight_kg": float(weight.group(1))
            if weight and "." in weight.group(1)
            else int(weight.group(1))
            if weight
            else 2,
            "zone": zone.group(1) if zone else "local",
        }
    if tool in {"stock_count", "reorder_status"}:
        sku = re.search(r"sku\s*(?:=|is)?\s*([\w-]+)", prompt)
        return {"sku": sku.group(1) if sku else "widget-1"}
    if tool == "book_shipment":
        return {"weight_kg": 1}
    if tool == "book_verified_shipment":
        return {"address_kind": "business"}
    return {}


def _select_tool(tools: list[Any], prompt: str) -> str:
    names = [
        item["name"]
        for item in tools
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    requested = [name for name in names if name in prompt]
    if requested:
        return requested[0]
    phrases = {
        "Book one business shipment and report its status.": "book_verified_shipment",
        "Book one 2 kg shipment and report its status.": "book_shipment",
    }
    selected = phrases.get(prompt)
    if selected in names:
        return selected
    raise RuntimeError("fixture prompt did not identify a configured MCP tool")


def _response(process: subprocess.Popen[str], expected_id: int) -> dict[str, Any]:
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + 20
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not selector.select(remaining):
            raise TimeoutError("configured MCP server did not answer within 20 seconds")
        line = process.stdout.readline()
        if not line:
            detail = ""
            if process.stderr is not None and process.poll() is not None:
                raw_detail = process.stderr.read(8192)
                detail = (
                    raw_detail
                    if isinstance(raw_detail, str)
                    else raw_detail.decode("utf-8", errors="replace")
                )
                for name, value in os.environ.items():
                    if value and ("KEY" in name or "TOKEN" in name):
                        detail = detail.replace(value, "[REDACTED]")
                detail = detail[-500:].strip()
            raise RuntimeError(
                f"configured MCP server closed its output (exit={process.poll()}, stderr={detail!r})"
            )
        value = json.loads(line)
        if isinstance(value, dict) and value.get("id") == expected_id:
            if "error" in value:
                raise RuntimeError("configured MCP server rejected fixture request")
            result = value.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("configured MCP server returned an invalid result")
            return result


def call_server(
    server: dict[str, Any], prompt: str
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    command = server.get("command")
    args = server.get("args", [])
    if isinstance(command, list) and command:
        command, args = command[0], [*command[1:], *args]
    if not isinstance(command, str) or not isinstance(args, list):
        raise RuntimeError("native fixture config has no stdio server command")
    environment = os.environ.copy()
    configured_env = server.get("env", server.get("environment", {}))
    if isinstance(configured_env, dict):
        environment.update(
            {str(key): str(value) for key, value in configured_env.items()}
        )
    key = json.dumps(
        [server.get("name"), command, args, server.get("cwd")], sort_keys=True
    )
    if key not in _MCP_SESSIONS:
        process = subprocess.Popen(
            [command, *(str(value) for value in args)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
            cwd=server.get("cwd"),
            start_new_session=(os.name == "posix"),
        )
        try:
            assert process.stdin is not None
            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "m3-docs-local-provider", "version": "1"},
                },
            }
            process.stdin.write(json.dumps(initialize) + "\n")
            process.stdin.flush()
            _response(process, 1)
            process.stdin.write(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
                + "\n"
            )
            process.stdin.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
                )
                + "\n"
            )
            process.stdin.flush()
            tools = _response(process, 2).get("tools", [])
            _MCP_SESSIONS[key] = (process, tools, 3)
        except BaseException:
            _stop_process(process)
            raise
    process, tools, request_id = _MCP_SESSIONS[key]
    try:
        if process.poll() is not None:
            raise RuntimeError("configured MCP server process exited")
        assert process.stdin is not None
        tool = _select_tool(tools, prompt)
        arguments = _arguments(tool, prompt)
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
        result = _response(process, request_id)
        _MCP_SESSIONS[key] = (process, tools, request_id + 1)
        marker = os.environ.get("M3_DOCS_MCP_WIRE_MARKER")
        if marker:
            with Path(marker).open("a", encoding="utf-8") as output:
                output.write(
                    json.dumps(
                        {
                            "method": "tools/call",
                            "name": tool,
                            "arguments": arguments,
                            "result": result,
                            "runtime_kind": os.environ.get("M3_DOCS_RUNTIME_KIND"),
                            "runtime_version": os.environ.get(
                                "M3_DOCS_RUNTIME_VERSION"
                            ),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
        return tool, arguments, result
    except BaseException:
        _MCP_SESSIONS.pop(key, None)
        _stop_process(process)
        raise


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        else:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _close_sessions() -> None:
    for process, _tools, _request_id in tuple(_MCP_SESSIONS.values()):
        _stop_process(process)
    _MCP_SESSIONS.clear()


atexit.register(_close_sessions)


def codex_call(prompt: str) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    config_path = Path(os.environ["CODEX_HOME"]) / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    servers = config.get("mcp_servers", {})
    for name, server in servers.items():
        if not isinstance(server, dict) or not isinstance(server.get("command"), str):
            continue
        try:
            tool, arguments, result = call_server(server, prompt)
        except RuntimeError as error:
            if "did not identify" in str(error):
                continue
            raise
        return name, tool, arguments, result
    raise RuntimeError("no configured Codex MCP server matched the prompt")
