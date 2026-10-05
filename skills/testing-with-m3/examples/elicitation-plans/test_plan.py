import pytest
from elicitation_server import build_server

from m3 import (
    Config,
    ElicitationExpectationError,
    ElicitationPlan,
    InProcessServer,
    MCPTestKit,
    expect_form,
)

ADDRESS = {"street": "1 Main Street", "city": "Pune"}


def book_shipment(plan: ElicitationPlan):
    server = InProcessServer(name="shipping", factory=build_server)
    config = Config(protocol_revision="2026-07-28")
    with MCPTestKit(config=config, env={}) as kit, kit.direct(server) as client:
        return client.call_tool("book_shipment", {"weight_kg": 2}, elicitation=plan)


def test_plan_answers_the_address_form() -> None:
    plan = expect_form("shipping_address").accept(ADDRESS)

    result = book_shipment(plan)

    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "city": "Pune"}


def test_wrong_request_key_fails() -> None:
    plan = expect_form("billing_address").accept(ADDRESS)

    with pytest.raises(ElicitationExpectationError, match="did not match"):
        book_shipment(plan)


def test_content_must_match_the_server_schema() -> None:
    plan = expect_form("shipping_address").accept({"street": 3, "city": "Pune"})

    with pytest.raises(ElicitationExpectationError, match="schema"):
        book_shipment(plan)
