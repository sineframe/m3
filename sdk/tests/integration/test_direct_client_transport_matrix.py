"""One public direct-client contract exercised across every local transport."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from typing import Any, AsyncIterator, Literal

import pytest

from mcp import types
from mcp.server.lowlevel import Server

from mcp_pal.async_api import AsyncDirectClient, AsyncMCPTestKit, CallToolResult, PromptResult, ResourceReadResult
from mcp_pal.types import InProcessServer, SSEServer, StdioServer, HTTPServer, TrustLevel


pytestmark = pytest.mark.process_lifecycle


_ROOT = Path(__file__).parents[2]
_STDIO_FIXTURE = Path(__file__).parents[1] / "fixtures" / "matrix_stdio_server.py"
TransportKind = Literal["inprocess", "stdio", "streamable_http", "sse"]


def _cursor(params: Any) -> str | None:
    value = getattr(params, "cursor", None)
    return value if isinstance(value, str) else None


def _tool_values(cursor: str | None) -> list[types.Tool]:
    if cursor:
        return [types.Tool(name="failure", description="Return an MCP tool error", input_schema={"type": "object"})]
    return [
        types.Tool(
            name="echo",
            description="Return supplied text",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        )
    ]


async def _list_tools(_context: Any, params: Any) -> types.ListToolsResult:
    cursor = _cursor(params)
    return types.ListToolsResult(tools=_tool_values(cursor), next_cursor=None if cursor else "page-2")


async def _call_tool(_context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    if params.name == "failure":
        return types.CallToolResult(content=[types.TextContent(text="expected failure")], is_error=True)
    return types.CallToolResult(
        content=[types.TextContent(text=str((params.arguments or {}).get("text", "ok")))],
        is_error=False,
    )


async def _list_resources(_context: Any, _params: Any) -> types.ListResourcesResult:
    return types.ListResourcesResult(
        resources=[types.Resource(name="document", uri="memory://document", mime_type="text/plain")]
    )


async def _list_templates(_context: Any, _params: Any) -> types.ListResourceTemplatesResult:
    return types.ListResourceTemplatesResult(
        resource_templates=[types.ResourceTemplate(name="item", uri_template="memory://item/{id}")]
    )


async def _read_resource(_context: Any, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
    return types.ReadResourceResult(
        contents=[types.TextResourceContents(uri=params.uri, mime_type="text/plain", text="resource value")]
    )


async def _list_prompts(_context: Any, _params: Any) -> types.ListPromptsResult:
    return types.ListPromptsResult(prompts=[types.Prompt(name="greeting", description="A greeting")])


async def _get_prompt(_context: Any, _params: types.GetPromptRequestParams) -> types.GetPromptResult:
    return types.GetPromptResult(
        description="Generated greeting",
        messages=[types.PromptMessage(role="user", content=types.TextContent(text="hello greeting"))],
    )


def _in_process_server() -> Server:
    return Server(
        "matrix-inprocess",
        version="1",
        instructions="deterministic matrix fixture",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
        on_list_resources=_list_resources,
        on_list_resource_templates=_list_templates,
        on_read_resource=_read_resource,
        on_list_prompts=_list_prompts,
        on_get_prompt=_get_prompt,
    )


def _wire_result(request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "matrix-remote", "version": "1"},
            "instructions": "deterministic matrix fixture",
        }
    elif method == "tools/list":
        cursor = params.get("cursor")
        result = {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                }
                for tool in _tool_values(cursor)
            ],
            **({} if cursor else {"nextCursor": "page-2"}),
        }
    elif method == "tools/call":
        result = {
            "content": [{"type": "text", "text": "expected failure" if params.get("name") == "failure" else (params.get("arguments") or {}).get("text", "ok")}],
            "isError": params.get("name") == "failure",
        }
    elif method == "resources/list":
        result = {"resources": [{"name": "document", "uri": "memory://document", "mimeType": "text/plain"}]}
    elif method == "resources/templates/list":
        result = {"resourceTemplates": [{"name": "item", "uriTemplate": "memory://item/{id}"}]}
    elif method == "resources/read":
        result = {"contents": [{"uri": params.get("uri"), "mimeType": "text/plain", "text": "resource value"}]}
    elif method == "prompts/list":
        result = {"prompts": [{"name": "greeting", "description": "A greeting"}]}
    elif method == "prompts/get":
        result = {"description": "Generated greeting", "messages": [{"role": "user", "content": {"type": "text", "text": "hello greeting"}}]}
    else:
        result = {}
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}


class _RemoteMatrixFixture:
    def __init__(self, transport: Literal["streamable_http", "sse"]) -> None:
        self.transport = transport
        self.server: asyncio.AbstractServer | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.writers: set[asyncio.StreamWriter] = set()
        self.ready = asyncio.Event()
        self.closed = asyncio.Event()
        self.lock = asyncio.Lock()

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = int(self.server.sockets[0].getsockname()[1])
        return f"http://127.0.0.1:{port}/{'sse' if self.transport == 'sse' else 'mcp'}"

    async def _request(self, reader: asyncio.StreamReader) -> tuple[str, str, bytes]:
        header_bytes = await reader.readuntil(b"\r\n\r\n")
        lines = header_bytes[:-4].split(b"\r\n")
        method, target, _ = lines[0].decode().split(" ", 2)
        headers = {line.decode().split(":", 1)[0].lower(): line.decode().split(":", 1)[1].strip() for line in lines[1:]}
        length = int(headers.get("content-length", "0"))
        return method, target, await reader.readexactly(length) if length else b""

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.add(writer)
        keep_open = False
        try:
            method, target, body = await self._request(reader)
            if self.transport == "sse" and method == "GET":
                self.writer = writer
                self.ready.set()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: keep-alive\r\n\r\n")
                writer.write(b"event: endpoint\ndata: /messages?session_id=matrix\n\n")
                await writer.drain()
                keep_open = True
                await self.closed.wait()
                return
            request = json.loads(body or b"{}")
            response = _wire_result(request)
            if self.transport == "sse":
                await self.ready.wait()
                writer.write(b"HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                if self.writer is not None:
                    async with self.lock:
                        self.writer.write(b"data: " + json.dumps(response, separators=(",", ":")).encode() + b"\n\n")
                        await self.writer.drain()
            else:
                encoded = json.dumps(response, separators=(",", ":")).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(encoded)).encode() + b"\r\nMcp-Session-Id: matrix\r\n\r\n" + encoded)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, json.JSONDecodeError):
            pass
        finally:
            self.writers.discard(writer)
            if not keep_open:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, asyncio.CancelledError):
                    pass

    async def stop(self) -> None:
        self.closed.set()
        for writer in tuple(self.writers):
            writer.close()
        for writer in tuple(self.writers):
            try:
                await writer.wait_closed()
            except (ConnectionError, asyncio.CancelledError):
                pass
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


@asynccontextmanager
async def _client_for(kind: TransportKind) -> AsyncIterator[AsyncDirectClient]:
    fixture: _RemoteMatrixFixture | None = None
    if kind == "inprocess":
        binding: Any = InProcessServer(name="matrix-inprocess", factory=_in_process_server)
    elif kind == "stdio":
        binding = StdioServer(name="matrix-stdio", command=sys.executable, args=(str(_STDIO_FIXTURE),), cwd=str(_ROOT))
    else:
        fixture = _RemoteMatrixFixture(kind)
        url = await fixture.start()
        binding = (
            HTTPServer(name="matrix-remote", url=url, trust=TrustLevel.TRUSTED_PRIVATE)
            if kind == "streamable_http"
            else SSEServer(name="matrix-remote", url=url, trust=TrustLevel.TRUSTED_PRIVATE)
        )
    kit = AsyncMCPTestKit(env={}, cwd=str(_ROOT))
    try:
        client = kit.direct(binding, resolve_host=lambda _host, _port: ("127.0.0.1",)) if fixture else kit.direct(binding)
        async with client:
            yield client
    finally:
        await kit.aclose()
        if fixture is not None:
            await fixture.stop()


async def _assert_matrix_contract(client: AsyncDirectClient) -> None:
    assert client.initialization is not None
    assert client.initialization.server_info["name"].startswith("matrix-")
    assert client.initialization.instructions == "deterministic matrix fixture"

    first_page = await client.list_tools()
    assert first_page.next_cursor == "page-2"
    all_tools = await client.list_all_tools()
    assert {tool.name for tool in all_tools} == {"echo", "failure"}
    success = await client.call_tool("echo", {"text": "hello"})
    assert isinstance(success, CallToolResult)
    assert success.is_error is False
    assert success.content[0]["text"] == "hello"
    failure = await client.call_tool("failure", {})
    assert isinstance(failure, CallToolResult)
    assert failure.is_error is True

    resources = await client.list_all_resources()
    assert resources[0].uri == "memory://document"
    resource = await client.read_resource(resources[0].uri)
    assert isinstance(resource, ResourceReadResult)
    assert resource.text == "resource value"
    templates = await client.list_all_resource_templates()
    assert templates[0].uri_template == "memory://item/{id}"
    prompts = await client.list_all_prompts()
    assert prompts[0].name == "greeting"
    prompt = await client.get_prompt("greeting")
    assert isinstance(prompt, PromptResult)
    assert prompt.messages[0]["content"]["text"] == "hello greeting"
    await client.ping()

    trace = client.trace
    assert trace is not None
    assert trace.events
    assert any(event.kind.value == "mcp.request" for event in trace.events)
    assert any(event.kind.value == "mcp.response" for event in trace.events)
    assert any(event.kind.value == "tool.call_requested" for event in trace.events)
    assert [event.sequence for event in trace.events] == list(range(len(trace.events)))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["inprocess", "stdio", "streamable_http", "sse"])
async def test_direct_client_contract_is_transport_parity(kind: TransportKind) -> None:
    async with _client_for(kind) as client:
        await _assert_matrix_contract(client)
    assert client.final_trace is not None
    view = client.final_trace.view()
    assert view.runtime.kind == "direct"
    assert view.runtime.initialization.state.value == "observed"
    assert view.runtime.initialization.value.server_name.value.startswith("matrix-")
    assert view.tool_calls
    assert view.summary.tool_call_count == 2
