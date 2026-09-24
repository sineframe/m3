from __future__ import annotations

import pytest

from m3.elicitation import (
    ElicitationResponse,
    FormElicitationRequest,
    UrlElicitationRequest,
)
from m3.errors import ElicitationExpectationError
from m3.harness._codex_mrtr import CodexPromptAssociator


def _form(
    key: str,
    message: str,
    *,
    server: str = "orders",
    schema: dict[str, object] | None = None,
    meta: dict[str, object] | None = None,
) -> FormElicitationRequest:
    return FormElicitationRequest(
        request_key=key,
        mode="form",
        message=message,
        requested_schema=schema
        or {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        server=server,
        meta=meta,
    )


def _accept(city: str) -> ElicitationResponse:
    return ElicitationResponse(action="accept", content={"city": city})


def test_distinct_messages_associate_even_when_form_schema_is_identical() -> None:
    home = _form("home", "Enter the home address.")
    business = _form("business", "Enter the business address.")
    associator = CodexPromptAssociator(
        {"home": home, "business": business},
        {"home": _accept("Pune"), "business": _accept("Mumbai")},
    )

    assert associator.response_for(
        {
            "mode": "form",
            "message": "Enter the business address.",
            "requestedSchema": dict(business.requested_schema),
        },
        server_name="orders",
    ) == _accept("Mumbai")
    assert associator.response_for(
        {
            "mode": "form",
            "message": "Enter the home address.",
            "requestedSchema": dict(home.requested_schema),
        },
        server_name="orders",
    ) == _accept("Pune")
    associator.complete()


def test_schema_object_key_order_does_not_change_request_identity() -> None:
    request = _form(
        "address",
        "Enter the address.",
        schema={
            "type": "object",
            "properties": {"city": {"type": "string"}, "pin": {"type": "string"}},
            "required": ["city", "pin"],
        },
    )
    associator = CodexPromptAssociator(
        {"address": request}, {"address": _accept("Pune")}
    )
    assert associator.response_for(
        {
            "mode": "form",
            "message": "Enter the address.",
            "requestedSchema": {
                "required": ["city", "pin"],
                "properties": {"pin": {"type": "string"}, "city": {"type": "string"}},
                "type": "object",
            },
        },
        server_name="orders",
    ) == _accept("Pune")
    associator.complete()


def test_codex_null_meta_matches_absent_mcp_meta() -> None:
    request = _form("address", "Enter the address.")
    associator = CodexPromptAssociator(
        {"address": request}, {"address": _accept("Pune")}
    )
    assert associator.response_for(
        {
            "mode": "form",
            "message": "Enter the address.",
            "requestedSchema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "_meta": None,
        },
        server_name="orders",
    ) == _accept("Pune")
    associator.complete()


def test_schema_value_difference_does_not_match() -> None:
    request = _form("address", "Enter the address.")
    associator = CodexPromptAssociator(
        {"address": request}, {"address": _accept("Pune")}
    )
    with pytest.raises(ElicitationExpectationError) as error:
        associator.response_for(
            {
                "mode": "form",
                "message": "Enter the address.",
                "requestedSchema": {"type": "object", "properties": {}},
            },
            server_name="orders",
        )
    assert error.value.details["reason"] == "unmatched_native_prompt"


def test_identical_prompts_with_equal_answers_are_safe_as_a_multiset() -> None:
    first = _form("first", "Enter the same address.")
    second = _form("second", "Enter the same address.")
    associator = CodexPromptAssociator(
        {"first": first, "second": second},
        {"first": _accept("Pune"), "second": _accept("Pune")},
    )
    prompt = {
        "mode": "form",
        "message": "Enter the same address.",
        "requestedSchema": dict(first.requested_schema),
    }
    assert associator.response_for(prompt, server_name="orders") == _accept("Pune")
    assert associator.response_for(prompt, server_name="orders") == _accept("Pune")
    associator.complete()


def test_identical_prompts_with_different_answers_fail_before_any_answer() -> None:
    associator = CodexPromptAssociator(
        {
            "first": _form("first", "Enter the same address."),
            "second": _form("second", "Enter the same address."),
        },
        {"first": _accept("Pune"), "second": _accept("Mumbai")},
    )
    with pytest.raises(ElicitationExpectationError) as error:
        associator.response_for(
            {
                "mode": "form",
                "message": "Enter the same address.",
                "requestedSchema": dict(
                    _form("candidate", "Enter the same address.").requested_schema
                ),
            },
            server_name="orders",
        )
    assert error.value.details["reason"] == "ambiguous_native_prompt"


def test_optional_metadata_is_compared_when_native_prompt_exposes_it() -> None:
    first = _form("first", "Enter the address.", meta={"source": "home"})
    second = _form("second", "Enter the address.", meta={"source": "business"})
    associator = CodexPromptAssociator(
        {"first": first, "second": second},
        {"first": _accept("Pune"), "second": _accept("Mumbai")},
    )
    with pytest.raises(ElicitationExpectationError) as error:
        associator.response_for(
            {
                "mode": "form",
                "message": "Enter the address.",
                "requestedSchema": dict(first.requested_schema),
            },
            server_name="orders",
        )
    assert error.value.details["reason"] == "ambiguous_native_prompt"

    # When Codex exposes the metadata, it can disambiguate the two requests.
    # Construct a fresh associator because a failed association is terminal.
    associator = CodexPromptAssociator(
        {"first": first, "second": second},
        {"first": _accept("Pune"), "second": _accept("Mumbai")},
    )
    assert associator.response_for(
        {
            "mode": "form",
            "message": "Enter the address.",
            "requestedSchema": dict(first.requested_schema),
            "_meta": {"source": "business"},
        },
        server_name="orders",
    ) == _accept("Mumbai")
    assert associator.response_for(
        {
            "mode": "form",
            "message": "Enter the address.",
            "requestedSchema": dict(first.requested_schema),
            "_meta": {"source": "home"},
        },
        server_name="orders",
    ) == _accept("Pune")
    associator.complete()


def test_url_prompts_match_url_and_exposed_elicitation_id() -> None:
    request = UrlElicitationRequest(
        request_key="verify",
        mode="url",
        message="Verify the booking.",
        url="https://verify.example.test/token",
        elicitation_id="verify-1",
        server="orders",
    )
    response = ElicitationResponse(action="accept")
    associator = CodexPromptAssociator({"verify": request}, {"verify": response})
    assert (
        associator.response_for(
            {
                "mode": "url",
                "message": "Verify the booking.",
                "url": "https://verify.example.test/token",
                "elicitationId": "verify-1",
            },
            server_name="orders",
        )
        == response
    )
    associator.complete()


def test_native_server_identity_is_part_of_prompt_match() -> None:
    associator = CodexPromptAssociator(
        {"address": _form("address", "Enter the address.")},
        {"address": _accept("Pune")},
    )
    with pytest.raises(ElicitationExpectationError) as error:
        associator.response_for(
            {
                "mode": "form",
                "message": "Enter the address.",
                "requestedSchema": dict(
                    _form("address", "Enter the address.").requested_schema
                ),
            },
            server_name="other-server",
        )
    assert error.value.details["reason"] == "unmatched_native_prompt"


def test_missing_or_repeated_native_prompts_do_not_complete_a_round() -> None:
    first = _form("first", "First address.")
    second = _form("second", "Second address.")
    associator = CodexPromptAssociator(
        {"first": first, "second": second},
        {"first": _accept("Pune"), "second": _accept("Mumbai")},
    )
    first_prompt = {
        "mode": "form",
        "message": "First address.",
        "requestedSchema": dict(first.requested_schema),
    }
    associator.response_for(first_prompt, server_name="orders")
    with pytest.raises(ElicitationExpectationError) as error:
        associator.complete()
    assert error.value.details["reason"] == "missing_native_prompt"

    associator = CodexPromptAssociator(
        {"first": first, "second": second},
        {"first": _accept("Pune"), "second": _accept("Mumbai")},
    )
    associator.response_for(first_prompt, server_name="orders")
    associator.response_for(first_prompt, server_name="orders")
    with pytest.raises(ElicitationExpectationError) as error:
        associator.complete()
    assert error.value.details["reason"] == "duplicate_or_missing_native_prompt"


def test_prompt_requires_standard_content_and_preserves_json_type_identity() -> None:
    request = _form(
        "count",
        "Enter quantity.",
        schema={"type": "object", "properties": {"count": {"type": "integer"}}},
    )
    associator = CodexPromptAssociator(
        {"count": request},
        {"count": ElicitationResponse(action="accept", content={"count": 1})},
    )
    with pytest.raises(ElicitationExpectationError) as error:
        associator.response_for(
            {
                "mode": "form",
                "message": "Enter quantity.",
                "requestedSchema": {
                    "type": "object",
                    "properties": {"count": {"type": "boolean"}},
                },
            },
            server_name="orders",
        )
    assert error.value.details["reason"] == "unmatched_native_prompt"
