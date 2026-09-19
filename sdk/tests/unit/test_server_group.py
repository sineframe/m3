from __future__ import annotations

import pytest
from mcp import types
from mcp.server.lowlevel import Server

from m3.async_api import AsyncMCPTestKit
from m3.server_group import (
    AmbiguousToolError,
    ServerGroupManager,
    ServerStartupError,
    ServerUnavailableError,
)
from m3.types import (
    HTTPServer,
    InProcessServer,
    ServerBinding,
    StdioServer,
    TrustLevel,
)


def _memory_server() -> Server:
    async def list_tools(_context: object, _params: object) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="remember",
                    description="stateful",
                    input_schema={"type": "object"},
                )
            ]
        )

    async def call_tool(
        _context: object, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(text=params.name)])

    return Server("memory", on_list_tools=list_tools, on_call_tool=call_tool)


@pytest.mark.asyncio
async def test_required_and_optional_server_startup_and_duplicate_tool_routing() -> (
    None
):
    manager = ServerGroupManager(
        (
            ServerBinding(server=StdioServer(name="one", command="one"), alias="one"),
            ServerBinding(
                server=StdioServer(name="two", command="two"),
                alias="two",
                required=False,
            ),
        ),
        unavailable={"two": "not_installed"},
    )
    await manager.start()
    manager.register_tools("one", ("echo", "shared"))
    manager.register_tools("two", ("shared",))
    snapshot = manager.snapshot()
    assert snapshot.resolve("one").available
    with pytest.raises(ServerUnavailableError):
        snapshot.resolve("two")
    assert snapshot.route_tool("echo").key == "one"
    await manager.close()

    duplicate = ServerGroupManager(
        (
            ServerBinding(server=StdioServer(name="one", command="one"), alias="one"),
            ServerBinding(server=StdioServer(name="two", command="two"), alias="two"),
        )
    )
    await duplicate.start()
    duplicate.register_tools("one", ("shared",))
    duplicate.register_tools("two", ("shared",))
    with pytest.raises(AmbiguousToolError):
        duplicate.snapshot().route_tool("shared")
    assert duplicate.snapshot().route_tool("shared", server="two").key == "two"
    await duplicate.close()


@pytest.mark.asyncio
async def test_required_server_failure_prevents_group_open() -> None:
    manager = ServerGroupManager(
        (ServerBinding(server=StdioServer(name="required", command="required")),),
        unavailable={"required": "startup_failed"},
    )
    with pytest.raises(ServerStartupError):
        await manager.start()
    await manager.close()


def test_untrusted_private_endpoint_is_rejected_without_network_access() -> None:
    manager = ServerGroupManager(
        (
            ServerBinding(
                server=HTTPServer(
                    name="private",
                    url="http://127.0.0.1:1234/mcp",
                    trust=TrustLevel.UNTRUSTED,
                )
            ),
        )
    )
    snapshot = manager.preflight()
    assert not snapshot.records[0].available
    assert snapshot.records[0].reason == "preflight_failed"


@pytest.mark.asyncio
async def test_in_process_server_gets_loopback_endpoint_and_preserves_state() -> None:
    manager = ServerGroupManager(
        (ServerBinding(server=InProcessServer(name="memory", factory=_memory_server)),)
    )
    first = await manager.start()
    config = manager.configurations()[0]
    assert first.records[0].endpoint == config.endpoint
    assert config.endpoint is not None and config.endpoint.startswith(
        "http://127.0.0.1:"
    )
    connection_id = first.records[0].connection_id
    second = await manager.start()
    assert second.records[0].connection_id == connection_id
    assert second.records[0].endpoint == config.endpoint
    await manager.close()


@pytest.mark.asyncio
async def test_loopback_endpoint_is_reachable_by_the_official_direct_client() -> None:
    manager = ServerGroupManager(
        (ServerBinding(server=InProcessServer(name="memory", factory=_memory_server)),)
    )
    await manager.start()
    endpoint = manager.configurations()[0].endpoint
    assert endpoint is not None
    try:
        async with AsyncMCPTestKit() as kit:
            async with kit.direct(
                HTTPServer(
                    name="loopback",
                    url=endpoint,
                    trust=TrustLevel.SDK_LOOPBACK,
                )
            ) as client:
                await client.initialize()
                result = await client.list_tools()
                assert [tool.name for tool in result.tools] == ["remember"]
    finally:
        await manager.close()
