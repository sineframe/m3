# Test state across tool calls

Keep dependent calls inside one direct-client context when a later operation
uses state created by an earlier operation. This example captures the
server-returned customer and order identifiers and passes them to subsequent
calls.

## Requirements

Use Python 3.10 or newer with a project prepared by `m3 setup`. Download the
example [`shipping_server.py`](../../../../sdk/examples/docs/servers-stdio/shipping_server.py)
and save it as `shipping_server.py` in the project root. It holds orders in
process memory for the lifetime of its subprocess.

## Create and retrieve an order

Save this complete test as `tests/test_stateful.py`:

```python
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
```

From the project root, run:

```sh
m3 test -- tests/test_stateful.py
```

The order ID comes from this test's `create_order` response. The client context
keeps one MCP connection open for all three calls, and M3 starts a fresh server
process for the test. This example's in-memory order does not persist between
separate test runs. For saved M3 execution history, see [persist test
results](/guides/results/persistence).
