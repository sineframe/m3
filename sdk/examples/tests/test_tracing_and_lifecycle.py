"""Inspect trace evidence and verify subprocess/session lifecycle behavior."""

from __future__ import annotations

import os
import time

import pytest
from mcp_pal.sync_api import ToolCallResult

from mcp_pal import MCPTestKit, StdioServer


def _process_has_exited(pid: int, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def test_inspect_a_finalized_trace(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit:
        client = kit.direct(example_server)
        with client:
            client.call_tool("shipping_quote", {"weight_kg": 1, "zone": "local"})

        trace = client.final_trace

    assert trace is not None
    event_kinds = [event.kind.value for event in trace.events]
    assert "tool.call_requested" in event_kinds
    assert "mcp.request" in event_kinds
    assert "mcp.response" in event_kinds
    assert event_kinds[-1] == "execution.finished"
    assert [event.sequence for event in trace.events] == list(range(len(trace.events)))


def test_closing_a_client_stops_its_server_process(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit:
        client = kit.direct(example_server)
        with client:
            pid_result = client.call_tool("process_id")
            assert isinstance(pid_result, ToolCallResult)
            pid = pid_result.structured_content["pid"]

        with pytest.raises(RuntimeError, match="closed"):
            client.list_all_tools()

    assert _process_has_exited(pid), (
        f"server process {pid} was still running after client close"
    )


def test_each_connection_has_isolated_server_state(example_server: StdioServer) -> None:
    with MCPTestKit(env={}) as kit:
        with kit.direct(example_server) as first:
            first.call_tool(
                "create_order",
                {"customer_id": "grace-hopper", "item": "compiler", "quantity": 1},
            )

        with kit.direct(example_server) as second:
            missing = second.call_tool("get_order", {"order_id": "order-001"})

    assert isinstance(missing, ToolCallResult)
    assert missing.is_error is True
    assert missing.content[0]["text"] == "order not found"
