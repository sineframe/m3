from __future__ import annotations

from shipping_server import build_server

from m3 import (
    Config,
    ElicitationPlan,
    InProcessServer,
    MCPTestKit,
    expect_form,
    expect_url,
    one_of,
    optional,
    round_of,
    sequence,
)

HOME = expect_form("home_address").accept({"street": "1 Home St", "city": "Pune"})
BUSINESS = expect_form("business_address").accept(
    {"street": "2 Business St", "city": "Pune"}
)
VERIFY = expect_url("verification").accept()


def book(address_kind: str, plan: ElicitationPlan) -> None:
    server = InProcessServer(name="shipping", factory=build_server)
    config = Config(protocol_revision="2026-07-28")
    with MCPTestKit(config=config, env={}) as kit, kit.direct(server) as client:
        result = client.call_tool(
            "book_verified_shipment", {"address_kind": address_kind}, elicitation=plan
        )
    assert result.is_error is False
    assert result.structured_content == {
        "status": "booked",
        "address_kind": address_kind,
    }


def test_one_of_answers_whichever_address_is_requested() -> None:
    book("business", sequence(one_of(HOME, BUSINESS), VERIFY))


def test_optional_step_can_be_skipped() -> None:
    book("none", sequence(optional(one_of(HOME, BUSINESS)), VERIFY))


def test_round_of_answers_two_requests_in_one_round() -> None:
    book("both", sequence(round_of(HOME, BUSINESS), VERIFY))
