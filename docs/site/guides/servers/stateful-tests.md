---
title: "Test state across tool calls"
description: "Keep dependent calls inside one direct-client context when a later operation uses state created by an earlier operation. This example captures the server-returned customer and order identifiers and passes them to subsequent calls."
---

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
results](../results/persistence.md).

## Observe a tool-list change

A server that changes its tool list at runtime announces it with
`notifications/tools/list_changed`. On MCP protocol 2026-07-28 the server
sends that notification only on a `subscriptions/listen` stream that the
client opened, so open one with `client.listen(...)` before the call that
changes the list. Download the example
[`catalog_server.py`](../../../../sdk/examples/docs/servers-stdio/catalog_server.py)
and save it in the project root. Its `enable_admin_tools` tool advertises a
new `refund_order` tool.

Save this complete test as `tests/test_list_changed.py`:

```python
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, StdioServer

pytestmark = pytest.mark.m3(suite_name="catalog-direct")


def test_enabling_admin_tools_announces_a_tool_list_change() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="catalog",
        command=sys.executable,
        args=(str(root / "catalog_server.py"),),
        cwd=str(root),
    )
    with (
        MCPTestKit(env={}) as kit,
        kit.direct(server, protocol="2026-07-28") as client,
        client.listen(tools_list_changed=True) as changes,
    ):
        client.call_tool("enable_admin_tools")
        change = changes.next(timeout=5)
        tools = [tool.name for tool in client.list_all_tools()]

    assert change is not None
    assert change.method == "notifications/tools/list_changed"
    assert "refund_order" in tools
    notifications = [
        event.payload["method"]
        for event in client.final_trace.events
        if event.kind.value == "mcp.notification"
    ]
    assert "notifications/tools/list_changed" in notifications
```

From the project root, run:

```sh
m3 test -- tests/test_list_changed.py
```

`listen(...)` accepts `tools_list_changed`, `prompts_list_changed`,
`resources_list_changed`, and `resource_subscriptions` (resource URIs whose
`notifications/resources/updated` events you want). `next(timeout=...)`
returns a `SubscriptionEvent` with `method` and, for resource updates, `uri`.
It raises `OperationTimeout` when no event arrives in time and returns `None`
after the server ends the stream. The events are also recorded in
`final_trace`. The stream closes with its `with` block or with the client.

On earlier protocol versions servers send these notifications on the
connection itself, so `final_trace` records them without a stream and
`listen(...)` raises `UnsupportedFeature`.
