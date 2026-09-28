from __future__ import annotations

import sys
from pathlib import Path

from m3 import MCPTestKit, StdioServer
from m3.sync_api import ToolCallResult

HERE = Path(__file__).resolve().parent


def test_shipping_server_returns_a_structured_quote() -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}
