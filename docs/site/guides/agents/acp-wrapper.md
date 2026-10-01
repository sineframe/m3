---
title: "Expose your custom agent through ACP"
description: "Wrap custom agent logic in an ACP process and record the real MCP result."
---

# Expose your custom agent through ACP

Add a small ACP process when your agent logic does not already speak ACP but you want M3 to launch it and record its session. The complete local project below is deterministic: its `run_agent` seam selects a real MCP tool and returns the server’s result. Replace that seam with your agent’s orchestration while retaining the request, update, cancellation, and cleanup lifecycle.

## Requirements

Use Python 3.10 or later. From the repository root, install this development candidate with pytest:

```sh
python -m pip install -e 'sdk[pytest]'
```

The SDK pins `agent-client-protocol==0.12.1`; the project manifest records the same ACP dependency. The example uses one local stdio MCP server, no API key, and no external provider. Its JSON-RPC handler implements the ACP lifecycle used by this example. It is a focused wrapper example, not a claim of full ACP conformance. Installing `sf-m3[pytest]` from the package index selects a published release; this guide’s candidate comes from the checkout command above.

## Complete wrapper project

Create a directory named `acp-wrapper` and add these files.

`echo_server.py`:

```python
from __future__ import annotations

import json
import sys


def reply(value: dict[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    identifier = request.get("id")
    if method == "initialize":
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "echo", "version": "1"},
                },
            }
        )
    elif method == "tools/list":
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Return the supplied text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                                "additionalProperties": False,
                            },
                        }
                    ]
                },
            }
        )
    elif method == "tools/call":
        value = request.get("params", {}).get("arguments", {}).get("text", "")
        reply(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {
                    "content": [{"type": "text", "text": value}],
                    "structuredContent": {"echo": value},
                },
            }
        )
    elif identifier is not None:
        reply({"jsonrpc": "2.0", "id": identifier, "result": {}})
```

`wrapped_agent.py`:

```python
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
```

`test_wrapper.py`:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_custom_acp_agent_exposes_real_mcp_result() -> None:
    selection = {
        "harness": "acp",
        "models": ["fixture"],
        "manifest": {
            "schema_version": "m3.harness.v1",
            "protocol": "acp",
            "protocol_version": 1,
            "command": sys.executable,
            "args": [str(HERE / "wrapped_agent.py")],
        },
    }
    server = StdioServer(
        name="echo",
        command=sys.executable,
        args=(str(HERE / "echo_server.py"),),
        cwd=str(HERE),
    )
    prompt = json.dumps({"tool": "echo", "arguments": {"text": "m3-wrapper-ok"}})
    with MCPTestKit(env={}) as kit:
        result = kit.agents([selection])[0].run(
            prompt, server=server, tools=["echo:echo"], timeout=20
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "echo",
        server="echo",
        arguments={"text": "m3-wrapper-ok"},
        status="success",
        count=1,
    )
    trace = result.trace_view
    captured = trace.tool_calls[0].result.value
    assert captured.content[0].text == "m3-wrapper-ok"
    assert captured.structured_content.value == {"echo": "m3-wrapper-ok"}
    assert any(
        message.role == "assistant"
        and any(
            block.kind == "text" and block.text == "m3-wrapper-ok"
            for block in message.content
        )
        for message in trace.messages
    )
```

## Run and inspect the result

Run the command from the `acp-wrapper` project directory:

```sh
python -m pytest -q test_wrapper.py
```

The verified local output is:

```text
1 passed
```

The recorded tool-call assertion proves the echo server was called once with the requested arguments and returned successfully. The trace assertions check the captured MCP result and the message emitted from that result. The wrapper does not create synthetic tool evidence. Its `session/cancel` handler cancels the active prompt task and stops the MCP subprocess; a later prompt starts a fresh MCP subprocess. `close` waits for the active task and terminates the MCP subprocess, escalating to kill after one second. The deterministic test covers the normal call and cleanup path, not a cancellation race.

The `run_agent` method is the integration seam. Replace its JSON instruction parsing and fixed tool call with your agent logic. Return the actual tool result and text that the agent will report. Keep ACP stdout reserved for protocol frames; send diagnostics to stderr. This example supports one stdio server. It does not implement HTTP servers, model or mode selection, terminal or filesystem requests, permission negotiation, or every optional ACP update.

Complete source project: [`sdk/examples/docs/acp-wrapper`](../../../../sdk/examples/docs/acp-wrapper).

Use [configuration guidance](../../reference/configuration.md) when your wrapped agent needs a secret. The manifest child environment is isolated by M3, but processes you start from inside your wrapper inherit the wrapper’s environment unless you construct a narrower one. See the [ACP reference](../../reference/acp.md) for M3’s process boundary and evidence behavior.

If M3 cannot resolve the wrapper command, preflight reports `acp_executable_missing`. If your wrapper exits before completing a frame, startup or the turn fails with a typed harness error; inspect its stderr without printing secrets. Next: [connect an existing ACP-compatible agent](acp-connect.md) or review [ACP manifest and evidence details](../../reference/acp.md).
