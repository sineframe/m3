from __future__ import annotations

from mcp import types
from shipping_server import build_server

from m3 import Config, InProcessServer, MCPTestKit
from m3.sync_api import InputRequiredResult

ADDRESS = {"street": "1 Main Street", "city": "Pune"}


def test_answer_the_returned_request_yourself() -> None:
    server = InProcessServer(name="shipping", factory=build_server)
    config = Config(protocol_revision="2026-07-28")
    with MCPTestKit(config=config, env={}) as kit, kit.direct(server) as client:
        pending = client.call_tool(
            "book_shipment", {"weight_kg": 2}, allow_input_required=True
        )
        assert isinstance(pending, InputRequiredResult)
        assert set(pending.input_requests) == {"shipping_address"}

        result = client.call_tool(
            "book_shipment",
            {"weight_kg": 2},
            request_state=pending.request_state,
            input_responses={
                "shipping_address": types.ElicitResult(action="accept", content=ADDRESS)
            },
        )

    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "address": ADDRESS}
