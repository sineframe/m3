import sys
from pathlib import Path

from m3 import MCPTestKit, StdioServer
from m3.types import ExecutionOutcome


def test_shipping_trace():
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})
        assert result.structured_content == {"amount": 9.0, "currency": "USD"}

    trace = client.final_trace
    assert trace is not None
    view = trace.view()
    assert view.outcome is ExecutionOutcome.COMPLETED
    call = view.tool_calls[0]
    assert call.tool.value == "shipping_quote"
    assert call.arguments.value == {"weight_kg": 2, "zone": "local"}
    assert call.wire.state.value == "observed"
