"""The smallest useful synchronous MCP server test."""

from mcp_pal.sync_api import ToolCallResult

from mcp_pal import MCPTestKit, StdioServer


def test_discover_and_call_a_tool(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        tools = client.list_all_tools()
        assert "shipping_quote" in {tool.name for tool in tools}

        result = client.call_tool(
            "shipping_quote",
            {"weight_kg": 2, "zone": "local"},
        )

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
