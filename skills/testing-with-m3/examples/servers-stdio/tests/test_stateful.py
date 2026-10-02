import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer

pytestmark = pytest.mark.m3(suite_name="shipping-direct")


def test_create_then_retrieve_an_order_in_one_connection() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        normalized = client.call_tool("normalize_customer", {"name": "Ada Lovelace"})
        customer_id = normalized.structured_content["customer_id"]
        created = client.call_tool(
            "create_order",
            {
                "customer_id": customer_id,
                "item": "analytical engine",
                "quantity": 2,
            },
        )
        order_id = created.structured_content["order_id"]
        retrieved = client.call_tool("get_order", {"order_id": order_id})

    assert retrieved.is_error is False
    assert retrieved.structured_content == {
        "order_id": order_id,
        "customer_id": "ada-lovelace",
        "item": "analytical engine",
        "quantity": 2,
    }
