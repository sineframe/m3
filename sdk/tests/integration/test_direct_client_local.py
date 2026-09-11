"""Real ClientSession/AsyncDirectClient contracts over the local transport."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from mcp import ClientSession, types
from mcp.server.lowlevel import Server

from mcp_pal.direct_client import AsyncDirectClient
from mcp_pal.errors import OperationCancelled, ProtocolError
from mcp_pal.transport.local import (
    InProcessMCPTransport,
    TransportProcessError,
)


async def _list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="echo",
                description="Return the supplied text.",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            ),
            types.Tool(
                name="bad",
                description="Return an MCP tool error.",
                input_schema={"type": "object"},
            ),
            types.Tool(
                name="explode",
                description="Raise inside the server handler.",
                input_schema={"type": "object"},
            ),
            types.Tool(
                name="slow",
                description="Remain pending until the client cancels.",
                input_schema={"type": "object"},
            ),
        ]
    )


async def _call_tool(
    _context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    if params.name == "bad":
        return types.CallToolResult(
            content=[types.TextContent(text="expected tool failure")],
            is_error=True,
        )
    if params.name == "explode":
        raise RuntimeError("server handler failed")
    if params.name == "slow":
        await asyncio.sleep(60)
    text = str((params.arguments or {}).get("text", "ok"))
    return types.CallToolResult(content=[types.TextContent(text=text)])


async def _list_resources(_context: Any, _params: Any) -> types.ListResourcesResult:
    return types.ListResourcesResult(
        resources=[
            types.Resource(
                name="document", uri="memory://document", mime_type="text/plain"
            )
        ]
    )


async def _read_resource(
    _context: Any, params: types.ReadResourceRequestParams
) -> types.ReadResourceResult:
    return types.ReadResourceResult(
        contents=[
            types.TextResourceContents(
                uri=params.uri, mime_type="text/plain", text="resource value"
            )
        ]
    )


async def _list_prompts(_context: Any, _params: Any) -> types.ListPromptsResult:
    return types.ListPromptsResult(
        prompts=[types.Prompt(name="greeting", description="A greeting")]
    )


async def _get_prompt(
    _context: Any, params: types.GetPromptRequestParams
) -> types.GetPromptResult:
    return types.GetPromptResult(
        description="Generated greeting",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(text=f"hello {params.name}"),
            )
        ],
    )


def _server() -> Server:
    return Server(
        "mcp-pal-e2e",
        version="1.0",
        instructions="Use the fixture tools.",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
        on_list_resources=_list_resources,
        on_read_resource=_read_resource,
        on_list_prompts=_list_prompts,
        on_get_prompt=_get_prompt,
    )


def _client(connection: Any) -> AsyncDirectClient:
    session = ClientSession(connection.read_stream, connection.write_stream)
    return AsyncDirectClient(cast(Any, session))


@pytest.mark.asyncio
async def test_async_direct_client_real_e2e_tools_resources_and_prompts() -> None:
    async with InProcessMCPTransport(_server) as connection:
        async with _client(connection) as client:
            assert client.initialization is not None
            assert client.initialization.server_info["name"] == "mcp-pal-e2e"
            assert client.initialization.instructions == "Use the fixture tools."

            tools = await client.list_all_tools()
            assert {tool.name for tool in tools} == {"echo", "bad", "explode", "slow"}
            echoed = await client.call_tool("echo", {"text": "hello"})
            assert echoed.is_error is False
            assert echoed.content[0]["text"] == "hello"

            tool_error = await client.call_tool("bad", {})
            assert tool_error.is_error is True
            assert tool_error.content[0]["text"] == "expected tool failure"

            resources = await client.list_all_resources()
            assert resources[0].uri == "memory://document"
            resource = await client.read_resource(resources[0].uri)
            assert resource.text == "resource value"

            prompts = await client.list_all_prompts()
            assert prompts[0].name == "greeting"
            prompt = await client.get_prompt("greeting")
            assert prompt.messages[0]["content"]["text"] == "hello greeting"

    assert connection.evidence.closed is True


@pytest.mark.asyncio
async def test_server_exception_true_is_reported_as_partial_transport_failure() -> None:
    async with InProcessMCPTransport(
        _server, raise_server_exceptions=True
    ) as connection:
        async with _client(connection) as client:
            # AsyncDirectClient converts an official JSON-RPC failure into its
            # safe typed protocol error; the transport retains the stronger
            # server-task evidence for callers that need infrastructure state.
            with pytest.raises(ProtocolError) as error:
                await client.call_tool("explode", {})
            assert "server handler failed" not in str(error.value)

        await asyncio.sleep(0)
        with pytest.raises(TransportProcessError):
            connection.raise_if_failed()
        assert connection.evidence.partial is True


@pytest.mark.asyncio
async def test_server_exception_false_is_sanitized_without_process_failure() -> None:
    async with InProcessMCPTransport(
        _server, raise_server_exceptions=False
    ) as connection:
        async with _client(connection) as client:
            with pytest.raises(ProtocolError) as error:
                await client.call_tool("explode", {})
            assert "server handler failed" not in str(error.value)

        await asyncio.sleep(0)
        connection.raise_if_failed()
        assert connection.evidence.partial is False


@pytest.mark.asyncio
async def test_cancelled_direct_operation_and_nested_cleanup_leave_no_fixture_tasks() -> (
    None
):
    current = asyncio.current_task()
    baseline = {
        task for task in asyncio.all_tasks() if task is not current and not task.done()
    }
    async with InProcessMCPTransport(_server) as connection:
        async with _client(connection) as client:
            operation = asyncio.create_task(client.call_tool("slow", {}))
            await asyncio.sleep(0)
            operation.cancel()
            with pytest.raises(OperationCancelled):
                await operation

    await asyncio.sleep(0)
    leftovers = {
        task
        for task in asyncio.all_tasks()
        if task is not current and not task.done() and task not in baseline
    }
    assert leftovers == set()
    assert connection.evidence.closed is True
