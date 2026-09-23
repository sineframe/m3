"""Low-level MCP SDK 2.0 server and manual keyed-response example."""

from __future__ import annotations

from typing import cast

import pytest
from mcp import types

from examples.servers.modern_mrtr_server import build_server
from m3 import Config, InProcessServer, MCPTestKit
from m3.sync_api import InputRequiredResult, ToolCallResult


@pytest.fixture
def example_server() -> tuple[InProcessServer, list[dict[str, object]]]:
    """Return the low-level SDK server and its wire observations."""

    calls: list[dict[str, object]] = []
    server = InProcessServer(
        name="modern-mrtr-example",
        factory=lambda: build_server(calls),
    )
    return server, calls


def test_low_level_sdk_server_returns_typed_input_required_then_completes(
    example_server: tuple[InProcessServer, list[dict[str, object]]],
) -> None:
    server, calls = example_server
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            pending = client.call_tool(
                "book_shipment",
                {"weight_kg": 2, "zone": "local"},
                allow_input_required=True,
            )
            assert isinstance(pending, InputRequiredResult)
            assert pending.request_state == "shipping-address"

            result = client.call_tool(
                "book_shipment",
                {"weight_kg": 2, "zone": "local"},
                request_state=pending.request_state,
                input_responses={
                    "shipping_address": types.ElicitResult(
                        action="accept",
                        content={
                            "street": "1 Main Street",
                            "city": "Pune",
                            "postal_code": "411001",
                        },
                    )
                },
            )

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content["status"] == "booked"
    assert [call["request_state"] for call in calls] == [None, "shipping-address"]
    responses = cast(dict[str, object], calls[1]["input_responses"])
    response = cast(dict[str, object], responses["shipping_address"])
    assert response["action"] == "accept"
    assert response["content"] == {
        "street": "1 Main Street",
        "city": "Pune",
        "postal_code": "411001",
    }
