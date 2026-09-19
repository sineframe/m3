"""Private bounded JSONL bridge used by the bundled Pi extension.

The bridge protocol is intentionally private: Pi receives a dynamic catalog
and forwards calls, while the SDK's capture proxies remain authoritative for
MCP wire evidence.  Transport clients are injected by the adapter in live
launches; this module also provides deterministic framing for fixtures.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from contextlib import AsyncExitStack
from typing import Any

MAX_FRAME_BYTES = 1024 * 1024


class BridgeError(RuntimeError):
    pass


def qualified_tool_name(server: str, tool: str) -> str:
    # Provider names are short ASCII identifiers. Keep readable slugs, with a
    # pair digest to distinguish identical slugs and preserve routing.
    def slug(value: str) -> str:
        value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
        return value or "unnamed"

    digest = hashlib.sha256(f"{server}\0{tool}".encode()).hexdigest()[:12]
    # Keep the MCP names visible to the model while reserving room for a
    # collision-resistant suffix and the required provider-safe prefix.
    prefix = f"mcp_{slug(server)[:20]}_{slug(tool)[:20]}"
    return f"{prefix}_{digest}"[:64]


class MCPBridge:
    """Catalog/call router; MCP sessions are owned by the caller."""

    def __init__(
        self,
        tools: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
        sessions: Mapping[str, Any] | None = None,
    ) -> None:
        self._tools = {
            str(server): {str(name): dict(value) for name, value in values.items()}
            for server, values in (tools or {}).items()
        }
        self._sessions = dict(sessions or {})

    def list_tools(self) -> list[dict[str, Any]]:
        output = []
        for server, tools in self._tools.items():
            for tool, descriptor in tools.items():
                name = qualified_tool_name(server, tool)
                public = {
                    key: value
                    for key, value in descriptor.items()
                    if key not in {"call", "name", "label", "server", "tool"}
                }
                output.append(
                    {
                        "name": name,
                        "label": f"{server}: {tool}",
                        "server": server,
                        "tool": tool,
                        **public,
                    }
                )
        mapping_path = os.environ.get("M3_PI_TOOL_MAP")
        if mapping_path:
            try:
                parent = os.path.dirname(mapping_path) or "."
                temporary: str | None = None
                fd, temporary = tempfile.mkstemp(prefix=".mapping-", dir=parent)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            item["name"]: [item["server"], item["tool"]]
                            for item in output
                        },
                        handle,
                    )
                os.replace(temporary, mapping_path)
            except OSError:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
        return output

    async def call_tool(self, server: str, tool: str, arguments: Any) -> Any:
        descriptor = self._tools.get(server, {}).get(tool)
        if descriptor is None:
            raise BridgeError("MCP tool is unavailable")
        callback = descriptor.get("call")
        if callable(callback):
            return (
                await callback(arguments)
                if asyncio.iscoroutinefunction(callback)
                else callback(arguments)
            )
        session = self._sessions.get(server)
        if session is None:
            raise BridgeError("MCP tool transport is unavailable")
        try:
            result = await session.call_tool(
                tool, arguments if isinstance(arguments, Mapping) else {}
            )
            return (
                result.model_dump(mode="json")
                if hasattr(result, "model_dump")
                else result
            )
        except Exception:
            raise BridgeError("MCP tool call failed") from None


async def serve(bridge: MCPBridge) -> None:
    """Serve private catalog/call frames over stdin/stdout for fixtures."""
    while True:
        response: dict[str, Any] = {}
        line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME_BYTES + 1)
        if not line:
            break
        if len(line) > MAX_FRAME_BYTES:
            request_id = None
            response = {
                "id": request_id,
                "ok": False,
                "error": "bridge frame exceeded safe size",
            }
            print(json.dumps(response), flush=True)
            continue
        try:
            request = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            response = {"id": None, "ok": False, "error": "invalid bridge frame"}
            print(json.dumps(response), flush=True)
            continue
        if not isinstance(request, Mapping):
            print(
                json.dumps({"id": None, "ok": False, "error": "invalid bridge frame"}),
                flush=True,
            )
            continue
        request_id = request.get("id") if isinstance(request, Mapping) else None
        if request.get("method") == "list_tools":
            response = {"ok": True, "tools": bridge.list_tools()}
        elif request.get("method") == "call_tool":
            try:
                result = await bridge.call_tool(
                    str(request.get("server")),
                    str(request.get("tool")),
                    request.get("arguments"),
                )
                response = {"ok": True, "result": result}
            except BridgeError:
                response = {"ok": False, "error": "MCP tool call failed"}
        else:
            response = {"ok": False, "error": "unknown bridge method"}
        response["id"] = request_id
        print(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True
        )


async def connect_configured_sessions(
    config: Mapping[str, Any], stack: AsyncExitStack
) -> dict[str, Any]:
    """Create official MCP ClientSession connections for configured servers."""
    if not config:
        return {}
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    sessions: dict[str, Any] = {}
    for server, descriptor in config.items():
        transport = descriptor.get("transport", "stdio")
        if transport == "stdio":
            params = StdioServerParameters(
                command=descriptor["command"],
                args=list(descriptor.get("args", ())),
                env=dict(descriptor.get("env", {})) or None,
                cwd=descriptor.get("cwd"),
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        elif transport == "sse":
            from mcp.client.sse import sse_client

            read, write = await stack.enter_async_context(
                sse_client(descriptor["url"], headers=descriptor.get("headers"))
            )
        elif transport in {"http", "streamable_http"}:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            client = await stack.enter_async_context(
                httpx2.AsyncClient(headers=descriptor.get("headers") or {})
            )
            streams = await stack.enter_async_context(
                streamable_http_client(descriptor["url"], http_client=client)
            )
            if isinstance(streams, tuple):
                read, write = streams[0], streams[1]
            else:
                read, write = streams.read_stream, streams.write_stream
        else:
            raise BridgeError("unsupported MCP transport")
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        sessions[str(server)] = session
    return sessions


async def configured_bridge() -> tuple[MCPBridge, AsyncExitStack]:
    stack = AsyncExitStack()
    await stack.__aenter__()
    raw = os.environ.get("M3_MCP_CONFIG", "{}")
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        await stack.aclose()
        raise BridgeError("MCP configuration is invalid") from None
    sessions = await connect_configured_sessions(config, stack)
    tools: dict[str, dict[str, Mapping[str, Any]]] = {}
    for server, session in sessions.items():
        result = await session.list_tools()
        tools[server] = {}
        for tool in result.tools:
            descriptor = (
                tool.model_dump(mode="json")
                if hasattr(tool, "model_dump")
                else dict(tool)
            )
            tools[server][str(descriptor.get("name"))] = descriptor
    return MCPBridge(tools, sessions), stack


async def main() -> None:
    bridge, stack = await configured_bridge()
    try:
        await serve(bridge)
    finally:
        await stack.aclose()


if __name__ == "__main__":
    asyncio.run(main())
