"""The smallest useful synchronous MCP server test."""

from mcp_pal import MCPTestKit, StdioServer
from mcp_pal.sync_api import ToolCallResult


def test_discover_and_call_a_tool(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        tools = client.list_all_tools()
        shipping_quote = next(tool for tool in tools if tool.name == "shipping_quote")
        assert shipping_quote.description == "Calculate a deterministic shipping quote"
        properties = shipping_quote.input_schema["properties"]
        assert properties["weight_kg"]["type"] == "number"
        assert properties["weight_kg"]["exclusiveMinimum"] == 0
        assert properties["zone"]["type"] == "string"
        assert list(properties["zone"]["enum"]) == [
            "local",
            "regional",
            "international",
        ]
        assert list(shipping_quote.input_schema["required"]) == [
            "weight_kg",
            "zone",
        ]

        result = client.call_tool(
            "shipping_quote",
            {"weight_kg": 2, "zone": "local"},
        )

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
