"""Test-only MCP stdio server that requires an explicit runtime credential."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


EXPECTED_TOKEN = "acp-auth-server-canary"


def _reply(request: dict[str, Any], result: dict[str, Any]) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}, separators=(",", ":")), flush=True)


def main() -> int:
    # The stdio proxy must supply this from its protected handoff.  A direct
    # launch without the credential cannot initialize or answer tools.
    if os.environ.get("MCP_PAL_AUTH_TOKEN") != EXPECTED_TOKEN:
        return 17
    marker = os.environ.get("MCP_PAL_E2E_MCP_MARKER")
    observations: list[str] = []
    if marker:
        Path(marker).write_text(json.dumps({"authorized": True, "methods": observations}), encoding="utf-8")
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except (TypeError, ValueError):
            continue
        method = request.get("method")
        params = request.get("params") or {}
        observations.append(str(method))
        if marker:
            Path(marker).write_text(json.dumps({"authorized": True, "methods": observations}), encoding="utf-8")
        if method == "initialize":
            _reply(request, {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "auth-fixture", "version": "1"}})
        elif method == "tools/list":
            _reply(request, {"tools": [{"name": "echo", "description": "Authenticated echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]})
        elif method == "tools/call":
            arguments = params.get("arguments") or {}
            _reply(request, {"content": [{"type": "text", "text": "authenticated:" + str(arguments.get("text", ""))}], "isError": False})
        elif not str(method).startswith("notifications/"):
            _reply(request, {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
