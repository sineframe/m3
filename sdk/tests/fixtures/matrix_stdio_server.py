"""Deterministic JSON-RPC MCP server used by the four-transport matrix."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _record_observation(request: dict[str, Any]) -> None:
    """Optionally expose process/request evidence for black-box E2E tests."""

    marker = os.environ.get("MCP_PAL_E2E_MCP_MARKER")
    if not marker:
        return
    observation = {
        "arguments": request.get("params", {}).get("arguments")
        if isinstance(request.get("params"), dict)
        else None,
        "id": request.get("id"),
        "method": request.get("method"),
        "name": request.get("params", {}).get("name")
        if isinstance(request.get("params"), dict)
        else None,
        "pid": os.getpid(),
    }
    with open(marker, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(observation, separators=(",", ":")) + "\n")


def _tools(cursor: str | None) -> dict[str, Any]:
    first = {
        "name": "echo",
        "description": "Return supplied text",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    }
    second = {
        "name": "failure",
        "description": "Return an MCP tool error",
        "inputSchema": {"type": "object"},
    }
    return {
        "tools": [second] if cursor else [first],
        **({} if cursor else {"nextCursor": "page-2"}),
    }


def _result(request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        value = {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "matrix-stdio", "version": "1"},
            "instructions": "deterministic matrix fixture",
        }
    elif method == "tools/list":
        value = _tools(params.get("cursor"))
    elif method == "tools/call":
        arguments = params.get("arguments") or {}
        if params.get("name") == "failure":
            value = {
                "content": [{"type": "text", "text": "expected failure"}],
                "isError": True,
            }
        else:
            value = {
                "content": [{"type": "text", "text": arguments.get("text", "ok")}],
                "isError": False,
            }
    elif method == "resources/list":
        value = {
            "resources": [
                {
                    "name": "document",
                    "uri": "memory://document",
                    "mimeType": "text/plain",
                }
            ]
        }
    elif method == "resources/templates/list":
        value = {
            "resourceTemplates": [{"name": "item", "uriTemplate": "memory://item/{id}"}]
        }
    elif method == "resources/read":
        value = {
            "contents": [
                {
                    "uri": params.get("uri"),
                    "mimeType": "text/plain",
                    "text": "resource value",
                }
            ]
        }
    elif method == "prompts/list":
        value = {"prompts": [{"name": "greeting", "description": "A greeting"}]}
    elif method == "prompts/get":
        value = {
            "description": "Generated greeting",
            "messages": [
                {"role": "user", "content": {"type": "text", "text": "hello greeting"}}
            ],
        }
    elif method == "ping":
        value = {}
    else:
        value = {}
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": value}


def main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except (TypeError, ValueError):
            continue
        _record_observation(request)
        if request.get("method", "").startswith("notifications/"):
            continue
        print(json.dumps(_result(request), separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
