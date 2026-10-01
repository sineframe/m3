from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any


def send(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await reader.readline()
    if not line:
        raise EOFError
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("ACP input frame must be a JSON object")
    return value


class Agent:
    def __init__(self) -> None:
        self.session_id = "m3-wrapper-" + uuid.uuid4().hex[:12]
        self.mcp: asyncio.subprocess.Process | None = None
        self.mcp_reader: asyncio.StreamReader | None = None
        self.mcp_writer: asyncio.StreamWriter | None = None
        self.servers: list[dict[str, Any]] = []
        self.rpc_id = 0
        self.prompt_task: asyncio.Task[None] | None = None

    async def start_mcp(self, servers: list[dict[str, Any]]) -> None:
        if len(servers) != 1 or servers[0].get("type", "stdio") != "stdio":
            raise ValueError("example wrapper expects one stdio MCP server")

        server = servers[0]
        self.servers = servers
        environment = os.environ.copy()
        for item in server.get("env") or []:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                environment[item["name"]] = str(item.get("value", ""))

        self.mcp = await asyncio.create_subprocess_exec(
            str(server["command"]),
            *(str(arg) for arg in server.get("args", [])),
            cwd=server.get("cwd"),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.mcp_reader = self.mcp.stdout
        self.mcp_writer = self.mcp.stdin
        await self.mcp_call(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "wrapped-agent", "version": "1"},
            },
        )
        assert self.mcp_writer is not None
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self.mcp_writer.write((json.dumps(notification) + "\n").encode())
        await self.mcp_writer.drain()
        await self.mcp_call("tools/list", {})

    async def mcp_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.mcp_reader is None or self.mcp_writer is None:
            raise RuntimeError("MCP server is not connected")

        self.rpc_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.rpc_id,
            "method": method,
            "params": params,
        }
        self.mcp_writer.write((json.dumps(request) + "\n").encode())
        await self.mcp_writer.drain()
        line = await self.mcp_reader.readline()
        if not line:
            raise RuntimeError("MCP server exited before responding")
        response = json.loads(line)
        if "error" in response or not isinstance(response.get("result"), dict):
            raise RuntimeError("MCP request failed")
        return response["result"]

    async def run_agent(self, prompt: str) -> tuple[dict[str, Any], str]:
        """Replace this seam with agent logic and return its actual MCP result."""
        if self.mcp is None or self.mcp.returncode is not None:
            await self.start_mcp(self.servers)
        request = json.loads(prompt)
        result = await self.mcp_call(
            "tools/call",
            {"name": request["tool"], "arguments": request["arguments"]},
        )
        text = "".join(
            item.get("text", "")
            for item in result.get("content", [])
            if isinstance(item, dict)
        )
        return result, text

    async def prompt(self, request: dict[str, Any]) -> None:
        identifier = request["id"]
        try:
            parts = request.get("params", {}).get("prompt", [])
            text = " ".join(
                item.get("text", "")
                for item in parts
                if isinstance(item, dict) and item.get("type") == "text"
            )
            result, message = await self.run_agent(text)
            arguments = json.loads(text)["arguments"]
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": self.session_id,
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "echo-1",
                            "title": "echo:echo",
                            "rawInput": arguments,
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
                        "sessionId": self.session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": message},
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
        except asyncio.CancelledError:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "result": {"stopReason": "cancelled"},
                }
            )
            raise
        except Exception as exc:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "error": {"code": -32000, "message": str(exc)},
                }
            )

    async def close(self) -> None:
        await self.cancel_prompt()
        await self.stop_mcp()

    async def cancel_prompt(self) -> None:
        if self.prompt_task is not None and not self.prompt_task.done():
            self.prompt_task.cancel()
            await asyncio.gather(self.prompt_task, return_exceptions=True)

    async def stop_mcp(self) -> None:
        if self.mcp is not None and self.mcp.returncode is None:
            self.mcp.terminate()
            try:
                await asyncio.wait_for(self.mcp.wait(), timeout=1)
            except asyncio.TimeoutError:
                self.mcp.kill()
                await self.mcp.wait()
        self.mcp = None
        self.mcp_reader = None
        self.mcp_writer = None


async def serve() -> None:
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
    agent = Agent()
    try:
        while True:
            request = await read_message(reader)
            method = request.get("method")
            identifier = request.get("id")
            params = request.get("params") or {}
            if method == "initialize":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": identifier,
                        "result": {
                            "protocolVersion": 1,
                            "agentInfo": {
                                "name": "m3-wrapper-example",
                                "version": "1",
                            },
                            "agentCapabilities": {
                                "mcpCapabilities": {
                                    "stdio": True,
                                    "http": False,
                                }
                            },
                        },
                    }
                )
            elif method == "session/new":
                try:
                    await agent.start_mcp(params.get("mcpServers", []))
                    send(
                        {
                            "jsonrpc": "2.0",
                            "id": identifier,
                            "result": {"sessionId": agent.session_id},
                        }
                    )
                except Exception as exc:
                    send(
                        {
                            "jsonrpc": "2.0",
                            "id": identifier,
                            "error": {"code": -32000, "message": str(exc)},
                        }
                    )
            elif method == "session/prompt":
                agent.prompt_task = asyncio.create_task(agent.prompt(request))
            elif method == "session/cancel":
                await agent.cancel_prompt()
                await agent.stop_mcp()
            elif identifier is not None:
                send({"jsonrpc": "2.0", "id": identifier, "result": {}})
    except (asyncio.IncompleteReadError, ConnectionError, EOFError):
        pass
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(serve())
