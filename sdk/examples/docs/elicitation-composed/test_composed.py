from __future__ import annotations

from typing import Any

from m3 import (
    Config,
    InProcessServer,
    MCPTestKit,
    expect_form,
    expect_url,
    one_of,
    optional,
    round_of,
    sequence,
)

from shipping_server import ADDRESS_SCHEMA, ADDRESSES, build_server


def form(server: InProcessServer, key: str):
    return expect_form(
        key,
        message=f"Enter the {key.removesuffix('_address')} address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_verified_shipment",
    ).accept(ADDRESSES[key])


def verification(server: InProcessServer):
    return expect_url(
        "verification",
        message="Complete shipment verification.",
        url="https://example.test/verify/123",
        server=server,
        operation_kind="tool",
        operation_name="book_verified_shipment",
    ).accept()


def run(kind: str, plan_factory: Any):
    calls: list[dict[str, object]] = []
    server = InProcessServer(name="shipping", factory=lambda: build_server(calls))
    with MCPTestKit(config=Config(protocol_revision="2026-07-28"), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool(
                "book_verified_shipment",
                {"address_kind": kind},
                elicitation=plan_factory(server),
            )
    assert result.is_error is False
    assert result.structured_content == {"status": "booked", "address_kind": kind}
    return calls


def test_address_alternative_then_url_uses_later_round() -> None:
    calls = run(
        "business",
        lambda server: sequence(
            one_of(form(server, "home_address"), form(server, "business_address")),
            verification(server),
        ),
    )
    assert [call["response_keys"] for call in calls] == [
        (),
        ("business_address",),
        ("verification",),
    ]
    assert [call["request_state"] for call in calls] == [
        None,
        "addresses",
        "verification",
    ]
    assert (
        calls[1]["responses"]["business_address"]["content"]
        == ADDRESSES["business_address"]
    )


def test_optional_address_can_be_skipped_before_url() -> None:
    calls = run(
        "none",
        lambda server: sequence(
            optional(
                one_of(form(server, "home_address"), form(server, "business_address"))
            ),
            verification(server),
        ),
    )
    assert [call["response_keys"] for call in calls] == [(), ("verification",)]
    assert [call["request_state"] for call in calls] == [None, "verification"]


def test_two_forms_are_expected_in_the_same_round() -> None:
    calls = run(
        "both",
        lambda server: sequence(
            round_of(form(server, "home_address"), form(server, "business_address")),
            verification(server),
        ),
    )
    assert [set(call["response_keys"]) for call in calls] == [
        set(),
        {"home_address", "business_address"},
        {"verification"},
    ]
    assert calls[1]["responses"]["home_address"]["content"] == ADDRESSES["home_address"]
    assert (
        calls[1]["responses"]["business_address"]["content"]
        == ADDRESSES["business_address"]
    )
