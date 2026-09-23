"""Direct form MRTR: attach one immutable plan to ``call_tool``."""

from __future__ import annotations

from typing import cast

import pytest

from examples.servers.modern_mrtr_server import ADDRESS_SCHEMA, build_server
from m3 import InProcessServer, MCPTestKit, expect_form
from m3.sync_api import ToolCallResult


@pytest.fixture
def example_server() -> tuple[InProcessServer, list[dict[str, object]]]:
    calls: list[dict[str, object]] = []
    return (
        InProcessServer(
            name="modern-mrtr-example",
            factory=lambda: build_server(calls),
        ),
        calls,
    )


def test_direct_form_mrtr_retries_with_keyed_current_response(
    example_server: tuple[InProcessServer, list[dict[str, object]]],
) -> None:
    server, calls = example_server
    plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept({"street": "1 Main Street", "city": "Pune", "postal_code": "411001"})
    arguments = {"weight_kg": 2, "zone": "local"}

    with MCPTestKit(env={}) as kit:
        with kit.direct(server, protocol="2026-07-28") as client:
            result = client.call_tool("book_shipment", arguments, elicitation=plan)

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content["status"] == "booked"
    assert len(calls) == 2
    assert len({call["request_id"] for call in calls}) == 2
    assert all(call["arguments"] == arguments for call in calls)
    assert calls[0]["request_state"] is None
    assert calls[1]["request_state"] == "shipping-address"
    response = cast(dict[str, object], calls[1]["input_responses"])
    assert set(response) == {"shipping_address"}
