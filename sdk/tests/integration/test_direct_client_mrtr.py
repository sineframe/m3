"""Direct-client MRTR tests over the real MCP SDK 2.0 transport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import cast

import pytest
from fixtures.modern_mrtr_server import (
    ADDRESS_SCHEMA,
    build_modern_mrtr_server,
)
from mcp import types

from m3 import (
    Config,
    ElicitationExpectationError,
    ElicitationPlan,
    ElicitationRoundLimitError,
    InProcessServer,
    MCPTestKit,
    ModelValidationError,
    UnsupportedFeature,
    expect_form,
    expect_url,
    round_of,
    sequence,
)
from m3.async_api import AsyncMCPTestKit


def _modern_config() -> Config:
    return Config(protocol_revision="2026-07-28")


def _server_binding(
    observed_tool_calls: list[dict[str, object]] | None = None,
) -> InProcessServer:
    factory: Callable[[], object] = lambda: (
        build_modern_mrtr_server(observed_tool_calls)
        if observed_tool_calls is not None
        else build_modern_mrtr_server()
    )
    return InProcessServer(name="modern-mrtr", factory=factory)


def _address_plan(server: InProcessServer) -> ElicitationPlan:
    return expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server=server,
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept({"street": "1 Test Way", "city": "Pune", "postal_code": "411001"})


def test_sync_direct_form_mrtr_retries_with_current_round_only() -> None:
    server = _server_binding()
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool(
                "book_shipment",
                {"weight_kg": 2, "zone": "local"},
                elicitation=_address_plan(server),
            )

    assert result.is_error is False
    assert result.structured_content["status"] == "booked"


def test_sync_direct_server_qualifier_uses_configured_server_name() -> None:
    server = InProcessServer(
        name="shipping",
        factory=build_modern_mrtr_server,
    )
    plan = expect_form(
        "shipping_address",
        message="Enter the delivery address.",
        schema=ADDRESS_SCHEMA,
        server="shipping",
        operation_kind="tool",
        operation_name="book_shipment",
    ).accept({"street": "1 Test Way", "city": "Pune", "postal_code": "411001"})

    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool(
                "book_shipment",
                {"weight_kg": 2, "zone": "local"},
                elicitation=plan,
            )

    assert result.is_error is False
    assert result.structured_content["status"] == "booked"


def test_sync_direct_protocol_override_enables_form_mrtr() -> None:
    server = _server_binding()
    with MCPTestKit(env={}) as kit:
        with kit.direct(server, protocol="2026-07-28") as client:
            result = client.call_tool(
                "book_shipment",
                {"weight_kg": 2, "zone": "local"},
                elicitation=_address_plan(server),
            )

    assert result.is_error is False
    assert result.structured_content["status"] == "booked"


@pytest.mark.asyncio
async def test_async_direct_form_mrtr_retries_with_current_round_only() -> None:
    server = _server_binding()
    async with AsyncMCPTestKit(config=_modern_config(), env={}) as kit:
        async with kit.direct(server) as client:
            result = await client.call_tool(
                "book_shipment",
                {"weight_kg": 2, "zone": "local"},
                elicitation=_address_plan(server),
            )

    assert result.is_error is False
    assert result.structured_content["status"] == "booked"


@pytest.mark.asyncio
async def test_async_direct_protocol_override_enables_form_mrtr() -> None:
    server = _server_binding()
    async with AsyncMCPTestKit(env={}) as kit:
        async with kit.direct(server, protocol="2026-07-28") as client:
            result = await client.call_tool(
                "book_shipment",
                {"weight_kg": 2, "zone": "local"},
                elicitation=_address_plan(server),
            )

    assert result.is_error is False
    assert result.structured_content["status"] == "booked"


@pytest.mark.asyncio
async def test_async_direct_url_prompt_and_resource_mrtr() -> None:
    server = _server_binding()
    async with AsyncMCPTestKit(config=_modern_config(), env={}) as kit:
        async with kit.direct(server) as client:
            url_result = await client.call_tool(
                "checkout",
                {},
                elicitation=expect_url(
                    "checkout",
                    message="Continue checkout.",
                    url="https://example.test/checkout/123",
                    operation_kind="tool",
                    operation_name="checkout",
                ).accept(),
            )
            prompt = await client.get_prompt(
                "interactive-prompt",
                elicitation=expect_form("prompt_context").accept({}),
            )
            resource = await client.read_resource(
                "memory://interactive-document",
                elicitation=expect_form("resource_access").accept({}),
            )
    assert url_result.structured_content == {"status": "authorized"}
    assert prompt.messages[0]["content"]["text"] == "prompt contents"
    assert resource.text == "resource contents"


def test_url_plan_asserts_without_visiting_url() -> None:
    server = _server_binding()
    plan = expect_url(
        "checkout",
        message="Continue checkout.",
        url="https://example.test/checkout/123",
        server=server,
        operation_kind="tool",
        operation_name="checkout",
    ).accept()
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool("checkout", {}, elicitation=plan)
    assert result.structured_content == {"status": "authorized"}


def test_retry_preserves_omitted_tool_arguments() -> None:
    observed: list[dict[str, object]] = []
    server = _server_binding(observed)
    plan = expect_url(
        "checkout",
        message="Continue checkout.",
        url="https://example.test/checkout/123",
    ).accept()
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server, validate_schemas=True) as client:
            result = client.call_tool("checkout", elicitation=plan)
    assert result.structured_content == {"status": "authorized"}
    assert [entry["arguments"] for entry in observed] == [None, None]


def test_url_id_mismatch_fails_without_visiting_the_url() -> None:
    server = _server_binding()
    plan = expect_url(
        "checkout",
        message="Continue checkout.",
        url="https://example.test/checkout/123",
        elicitation_id="wrong-id",
        operation_kind="tool",
        operation_name="checkout",
    ).accept()
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ElicitationExpectationError):
                client.call_tool("checkout", {}, elicitation=plan)


def test_direct_retry_preserves_wire_invariants_and_uses_fresh_request_ids() -> None:
    observed: list[dict[str, object]] = []
    server = _server_binding(observed)
    plan = sequence(
        expect_form("address", message="Address").accept({}),
        expect_form("contact", message="Contact").accept({}),
    )
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool(
                "multi_round",
                {"shipment": "same"},
                meta={"request": "same"},
                elicitation=plan,
            )

    assert result.structured_content == {"status": "complete"}
    assert [entry["name"] for entry in observed] == [
        "multi_round",
        "multi_round",
        "multi_round",
    ]
    assert [entry["arguments"] for entry in observed] == [
        {"shipment": "same"},
        {"shipment": "same"},
        {"shipment": "same"},
    ]
    metas = [entry["meta"] for entry in observed]
    assert metas[0] == metas[1] == metas[2]
    assert isinstance(metas[0], dict)
    assert metas[0]["request"] == "same"
    assert [entry["request_state"] for entry in observed] == [
        None,
        "",
        "multi-contact",
    ]
    assert [
        set(cast(Mapping[str, object], entry["input_responses"])) for entry in observed
    ] == [
        set(),
        {"address"},
        {"contact"},
    ]
    request_ids = [entry["request_id"] for entry in observed]
    assert all(request_id is not None for request_id in request_ids)
    assert len(set(request_ids)) == len(request_ids)


def test_direct_input_required_without_plan_fails_but_manual_escape_is_untouched() -> (
    None
):
    server = _server_binding()
    with MCPTestKit(env={}) as kit:
        with kit.direct(server, protocol="2026-07-28") as client:
            with pytest.raises(ElicitationExpectationError):
                client.call_tool("book_shipment", {"weight_kg": 2, "zone": "local"})

            pending = client.call_tool(
                "book_shipment",
                {"weight_kg": 2, "zone": "local"},
                allow_input_required=True,
            )
            assert pending.request_state == "book-shipment-address"


def test_form_response_is_checked_against_actual_schema() -> None:
    server = _server_binding()
    invalid = expect_form(
        "shipping_address",
        schema=ADDRESS_SCHEMA,
    ).accept({"city": "Pune"})
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ElicitationExpectationError):
                client.call_tool(
                    "book_shipment",
                    {"weight_kg": 2, "zone": "local"},
                    elicitation=invalid,
                )


def test_round_limit_allows_ten_rounds_and_final_completion_attempt() -> None:
    server = _server_binding()
    plan = sequence(
        *[
            expect_form(f"round-{number}", message=f"round-{number}").accept({})
            for number in range(1, 11)
        ]
    )
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool(
                "ten_rounds", {}, elicitation=plan, elicitation_round_limit=10
            )
    assert result.structured_content == {"status": "complete"}


def test_round_limit_rejects_an_eleven_round() -> None:
    server = _server_binding()
    plan = sequence(
        *[
            expect_form(f"round-{number}", message=f"round-{number}").accept({})
            for number in range(1, 12)
        ]
    )
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ElicitationRoundLimitError):
                client.call_tool(
                    "ten_rounds",
                    {"rounds": 11},
                    elicitation=plan,
                    elicitation_round_limit=10,
                )


def test_round_limit_must_be_positive() -> None:
    server = _server_binding()
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ModelValidationError):
                client.call_tool(
                    "book_shipment",
                    {"weight_kg": 2, "zone": "local"},
                    elicitation=_address_plan(server),
                    elicitation_round_limit=0,
                )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_responses": {}},
        {"request_state": "state"},
        {"allow_input_required": True},
    ],
)
def test_plan_rejects_each_manual_input_control_before_wire_operation(
    kwargs: dict[str, object],
) -> None:
    observed: list[dict[str, object]] = []
    server = _server_binding(observed)
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ModelValidationError):
                client.call_tool(
                    "book_shipment",
                    {"weight_kg": 2, "zone": "local"},
                    elicitation=_address_plan(server),
                    **kwargs,
                )
    assert observed == []


def test_incomplete_plan_and_invalid_limit_fail_before_wire_operation() -> None:
    observed: list[dict[str, object]] = []
    server = _server_binding(observed)
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ModelValidationError):
                client.call_tool(
                    "book_shipment",
                    {"weight_kg": 2, "zone": "local"},
                    elicitation=expect_form("shipping_address"),
                )
            with pytest.raises(ModelValidationError):
                client.call_tool(
                    "book_shipment",
                    {"weight_kg": 2, "zone": "local"},
                    elicitation=_address_plan(server),
                    elicitation_round_limit=0,
                )
    assert observed == []


def test_prompt_and_resource_mrtr_use_the_same_plan_contract() -> None:
    server = _server_binding()
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            prompt = client.get_prompt(
                "interactive-prompt",
                elicitation=expect_form("prompt_context").accept({}),
            )
            resource = client.read_resource(
                "memory://interactive-document",
                elicitation=expect_form("resource_access").accept({}),
            )
    assert prompt.messages[0]["content"]["text"] == "prompt contents"
    assert resource.text == "resource contents"


def test_round_of_accepts_all_keyed_requests_from_one_input_required_result() -> None:
    server = _server_binding()
    plan = round_of(
        expect_form("address").accept({}),
        expect_form("contact").accept({}),
    )
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool("same_round", {}, elicitation=plan)
    assert result.structured_content == {"status": "complete"}


@pytest.mark.parametrize("action", ["decline", "cancel"])
def test_form_decline_and_cancel_are_sent_as_typed_responses(action: str) -> None:
    server = _server_binding()
    plan = getattr(expect_form("shipping_address"), action)()
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            result = client.call_tool(
                "book_shipment",
                {"weight_kg": 2, "zone": "local"},
                elicitation=plan,
            )
    assert result.structured_content["status"] == "booked"


def test_sampling_only_round_uses_the_configured_typed_callback() -> None:
    server = _server_binding()

    async def sampling(
        _context: object, _params: types.CreateMessageRequestParams
    ) -> types.CreateMessageResult:
        return types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(type="text", text="sampled"),
            model="test-model",
        )

    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server, sampling_callback=sampling) as client:
            result = client.call_tool("sampling_round", {}, elicitation=None)
    assert result.structured_content == {"status": "complete"}


def test_sampling_round_without_a_handler_fails_explicitly() -> None:
    server = _server_binding()
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(UnsupportedFeature):
                client.call_tool("sampling_round")


@pytest.mark.parametrize("tool_name", ["sampling_round", "roots_round", "mixed_round"])
def test_manual_escape_returns_first_input_required_without_dispatch(
    tool_name: str,
) -> None:
    server = _server_binding()

    async def fail_sampling(
        _context: object, _params: types.CreateMessageRequestParams
    ) -> types.CreateMessageResult:
        raise AssertionError("manual escape must not dispatch sampling")

    async def fail_roots(_context: object) -> types.ListRootsResult:
        raise AssertionError("manual escape must not dispatch roots")

    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(
            server,
            sampling_callback=fail_sampling,
            list_roots_callback=fail_roots,
        ) as client:
            pending = client.call_tool(tool_name, allow_input_required=True)
    assert pending.request_state is not None


def test_roots_only_round_uses_the_configured_typed_callback() -> None:
    server = _server_binding()

    async def roots(_context: object) -> types.ListRootsResult:
        return types.ListRootsResult(roots=[])

    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server, list_roots_callback=roots) as client:
            result = client.call_tool("roots_round", {}, elicitation=None)
    assert result.structured_content == {"status": "complete"}


def test_mixed_round_combines_plan_response_and_sampling_callback() -> None:
    server = _server_binding()

    async def sampling(
        _context: object, _params: types.CreateMessageRequestParams
    ) -> types.CreateMessageResult:
        return types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(type="text", text="sampled"),
            model="test-model",
        )

    plan = expect_form("approval").accept({})
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server, sampling_callback=sampling) as client:
            result = client.call_tool("mixed_round", {}, elicitation=plan)
    assert result.structured_content == {"status": "complete"}


def test_sync_client_remains_usable_after_expectation_failure() -> None:
    server = _server_binding()
    with MCPTestKit(config=_modern_config(), env={}) as kit:
        with kit.direct(server) as client:
            with pytest.raises(ElicitationExpectationError):
                client.call_tool(
                    "book_shipment", elicitation=expect_url("wrong").accept()
                )
            result = client.call_tool(
                "book_shipment",
                elicitation=_address_plan(server),
            )
    assert result.structured_content["status"] == "booked"


def test_async_manual_escape_remains_available() -> None:
    async def run() -> None:
        server = _server_binding()
        async with AsyncMCPTestKit(config=_modern_config(), env={}) as kit:
            async with kit.direct(server) as client:
                pending = await client.call_tool(
                    "book_shipment", allow_input_required=True
                )
                assert pending.request_state == "book-shipment-address"

    asyncio.run(run())
