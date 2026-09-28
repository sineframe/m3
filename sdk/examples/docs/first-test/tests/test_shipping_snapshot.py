import sys
from pathlib import Path

from m3 import MCPTestKit, StdioServer
from m3.snapshots import snapshot


def test_shipping_snapshot() -> None:
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})

    captured = snapshot(result)
    assert captured["is_error"] is False
    assert captured["structured_content"] == {
        "amount": 9.0,
        "currency": "USD",
    }
