from elicitation_server import ADDRESS_SCHEMA, build_server

import pytest

from m3 import (
    Config,
    ElicitationExpectationError,
    InProcessServer,
    MCPTestKit,
    expect_form,
)
from m3.sync_api import ToolCallResult


def test_direct_tool_call_answers_a_form_request() -> None:
    server = InProcessServer(name="shipping", factory=build_server)
    plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server="shipping",
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept({"street": "1 Main Street", "city": "Pune"})

    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool(
                "book_shipment",
                {"weight_kg": 2},
                elicitation=plan,
            )

    assert isinstance(result, ToolCallResult)
    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "city": "Pune"}


def test_request_key_mismatch_and_invalid_content_fail_before_retry() -> None:
    server = InProcessServer(name="shipping", factory=build_server)
    wrong_key = expect_form("address").accept(
        {"street": "1 Main Street", "city": "Pune"}
    )
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ElicitationExpectationError, match="did not match"):
                client.call_tool(
                    "book_shipment", {"weight_kg": 2}, elicitation=wrong_key
                )

    invalid_content = expect_form("shipping_address", schema=ADDRESS_SCHEMA).accept(
        {"street": 3, "city": "Pune"}
    )
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ElicitationExpectationError, match="schema"):
                client.call_tool(
                    "book_shipment", {"weight_kg": 2}, elicitation=invalid_content
                )
