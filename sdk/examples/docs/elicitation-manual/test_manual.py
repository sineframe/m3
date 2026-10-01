from __future__ import annotations

from mcp import types
from shipping_server import build_server

from m3 import Config, InProcessServer, MCPTestKit
from m3.sync_api import InputRequiredResult, ToolCallResult


def test_manual_input_reuses_returned_state_and_keyed_response() -> None:
    calls: list[dict[str, object]] = []
    server = InProcessServer(name="shipping", factory=lambda: build_server(calls))
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            pending = client.call_tool(
                "book_shipment", {"weight_kg": 2}, allow_input_required=True
            )
            assert isinstance(pending, InputRequiredResult)
            assert set(pending.input_requests) == {"shipping_address"}
            assert pending.request_state == "shipping-address:opaque-v1"

            result = client.call_tool(
                "book_shipment",
                {"weight_kg": 2},
                request_state=pending.request_state,
                input_responses={
                    "shipping_address": types.ElicitResult(
                        action="accept",
                        content={"street": "1 Main Street", "city": "Pune"},
                    )
                },
            )

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "city": "Pune"}
    assert calls[1]["request_state"] == pending.request_state
    assert set(calls[1]["responses"]) == set(pending.input_requests)
