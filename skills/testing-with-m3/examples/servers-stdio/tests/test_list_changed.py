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
