"""One agent action composes an address alternative with a later URL round."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from examples.servers.modern_mrtr_server import ADDRESS_SCHEMA
from m3 import expect, expect_form, expect_url, one_of, optional, round_of, sequence
from m3.types import ExecutionOutcome, StdioServer

pytest_plugins = ("examples.tests.pi_conftest",)

EXAMPLES_ROOT = Path(__file__).parents[1]
HOME_ADDRESS = {"street": "1 Home Street", "city": "Pune", "postal_code": "411001"}
BUSINESS_ADDRESS = {
    "street": "2 Business Street",
    "city": "Pune",
    "postal_code": "411002",
}


@pytest.fixture
def example_server(pi_fixture: Path) -> StdioServer:
    return StdioServer(
        name="modern-mrtr-example",
        command=sys.executable,
        args=(str(EXAMPLES_ROOT / "servers" / "modern_mrtr_server.py"),),
        cwd=str(EXAMPLES_ROOT),
        environment={"M3_MRTR_WIRE_MARKER": str(pi_fixture)},
    )


def _address(server: StdioServer, kind: str):
    return expect_form(
        f"{kind}_address",
        message=f"Enter the {kind} delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_verified_shipment",
    ).accept(HOME_ADDRESS if kind == "home" else BUSINESS_ADDRESS)


def _verification(server: StdioServer):
    return expect_url(
        "verification",
        message="Complete shipment verification.",
        url="https://example.test/verify/123",
        server=server,
        operation_kind="tool",
        operation_name="book_verified_shipment",
    ).accept()


@pytest.mark.parametrize("address_kind", ["home", "business"])
@pytest.mark.m3(agents=[{"harness": "pi", "models": ["fixture-model"]}])
def test_address_choice_then_url_in_one_tool_call(
    agent: Any, example_server: StdioServer, pi_fixture: Path, address_kind: str
) -> None:
    plan = sequence(
        one_of(_address(example_server, "home"), _address(example_server, "business")),
        _verification(example_server),
    )

    result = agent.run(
        f"Use m3-gate:book_verified_shipment for a {address_kind} shipment, "
        "then report whether it was booked.",
        server=example_server,
        elicitation=plan,
        timeout=120,
    )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "book_verified_shipment",
        server="modern-mrtr-example",
        status="success",
        count=1,
    )
    view = result.trace_view
    assert view is not None
    calls = [
        call for call in view.tool_calls if call.tool.value == "book_verified_shipment"
    ]
    assert len(calls) == 1
    assert len(calls[0].attempts) == 3
    assert [attempt.input_required for attempt in calls[0].attempts] == [
        True,
        True,
        False,
    ]
    assert [
        (entry.request_key, entry.mode, entry.action) for entry in view.elicitations
    ] == [
        (f"{address_kind}_address", "form", "accept"),
        ("verification", "url", "accept"),
    ]

    wire = [json.loads(line) for line in pi_fixture.read_text().splitlines()]
    assert len(wire) == 3
    assert len({attempt["id"] for attempt in wire}) == 3
    assert all(
        attempt["arguments"] == {"address_kind": address_kind} for attempt in wire
    )
    assert [attempt["requestState"] for attempt in wire] == [
        None,
        f"verified-address:{address_kind}",
        f"verified-url:{address_kind}",
    ]
    assert [set(attempt["inputResponses"]) for attempt in wire] == [
        set(),
        {f"{address_kind}_address"},
        {"verification"},
    ]


@pytest.mark.parametrize("address_kind", ["none", "home", "business"])
@pytest.mark.m3(agents=[{"harness": "pi", "models": ["fixture-model"]}])
def test_optional_address_choice_then_url(
    agent: Any, example_server: StdioServer, pi_fixture: Path, address_kind: str
) -> None:
    plan = sequence(
        optional(
            one_of(
                _address(example_server, "home"),
                _address(example_server, "business"),
            )
        ),
        _verification(example_server),
    )
    address_instruction = (
        "without an address"
        if address_kind == "none"
        else f"for a {address_kind} shipment"
    )
    result = agent.run(
        f"Use m3-gate:book_verified_shipment {address_instruction}, then report whether it was booked.",
        server=example_server,
        elicitation=plan,
        timeout=120,
    )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "book_verified_shipment",
        server="modern-mrtr-example",
        status="success",
        count=1,
    )
    view = result.trace_view
    assert view is not None
    expected_keys = (
        ["verification"]
        if address_kind == "none"
        else [f"{address_kind}_address", "verification"]
    )
    assert [entry.request_key for entry in view.elicitations] == expected_keys
    wire = [json.loads(line) for line in pi_fixture.read_text().splitlines()]
    expected_responses = (
        [set(), {"verification"}]
        if address_kind == "none"
        else [set(), {f"{address_kind}_address"}, {"verification"}]
    )
    assert [set(attempt["inputResponses"]) for attempt in wire] == expected_responses


@pytest.mark.m3(agents=[{"harness": "pi", "models": ["fixture-model"]}])
def test_two_addresses_in_one_round_then_url(
    agent: Any, example_server: StdioServer, pi_fixture: Path
) -> None:
    plan = sequence(
        round_of(
            _address(example_server, "home"), _address(example_server, "business")
        ),
        _verification(example_server),
    )
    result = agent.run(
        "Use m3-gate:book_verified_shipment with both addresses, then report whether it was booked.",
        server=example_server,
        elicitation=plan,
        timeout=120,
    )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "book_verified_shipment",
        server="modern-mrtr-example",
        status="success",
        count=1,
    )
    view = result.trace_view
    assert view is not None
    keys = [entry.request_key for entry in view.elicitations]
    assert set(keys[:2]) == {"home_address", "business_address"}
    assert keys[2:] == ["verification"]
    wire = [json.loads(line) for line in pi_fixture.read_text().splitlines()]
    assert [set(attempt["inputResponses"]) for attempt in wire] == [
        set(),
        {"home_address", "business_address"},
        {"verification"},
    ]
