"""A multi-step test that passes each tool result into the next call."""

from mcp_pal.sync_api import ToolCallResult

from mcp_pal import MCPTestKit, StdioServer


def test_create_then_retrieve_an_order(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit, kit.direct(example_server) as client:
        normalized = client.call_tool("normalize_customer", {"name": "Ada Lovelace"})
        assert isinstance(normalized, ToolCallResult)
        customer_id = normalized.structured_content["customer_id"]

        created = client.call_tool(
            "create_order",
            {
                "customer_id": customer_id,
                "item": "analytical engine",
                "quantity": 2,
            },
        )
        assert isinstance(created, ToolCallResult)
        order_id = created.structured_content["order_id"]

        retrieved = client.call_tool("get_order", {"order_id": order_id})

    assert isinstance(retrieved, ToolCallResult)
    assert retrieved.is_error is False
    assert retrieved.structured_content == {
        "order_id": "order-001",
        "customer_id": "ada-lovelace",
        "item": "analytical engine",
        "quantity": 2,
    }
