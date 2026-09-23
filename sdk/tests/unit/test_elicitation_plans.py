from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from m3.elicitation import (
    ElicitationPlan,
    ElicitationResponse,
    FormElicitationRequest,
    PendingElicitationRound,
    UrlElicitationRequest,
    expect_form,
    expect_url,
    maybe_form,
    maybe_url,
    one_of,
    optional,
    round_of,
    sequence,
)
from m3.errors import ElicitationExpectationError, ModelValidationError


def form_request(
    key: str,
    *,
    message: str = "Enter a value",
    schema: dict[str, object] | None = None,
) -> FormElicitationRequest:
    return FormElicitationRequest(
        request_key=key,
        message=message,
        requested_schema=schema or {"type": "object"},
    )


def url_request(key: str, url: str) -> UrlElicitationRequest:
    return UrlElicitationRequest(request_key=key, message="Continue", url=url)


def test_form_leaf_is_incomplete_until_bound_and_accept_is_deeply_immutable() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    content = {"name": "Ada", "nested": {"enabled": True}}
    leaf = expect_form("profile", message="Profile", schema=schema)

    assert leaf.is_complete is False
    bound = leaf.accept(content)
    schema["properties"]["name"] = {"type": "integer"}  # type: ignore[index]
    content["nested"]["enabled"] = False  # type: ignore[index]

    assert bound.is_complete is True
    assert bound.requested_schema["properties"]["name"] == {"type": "string"}
    assert bound.response is not None
    assert bound.response.content["nested"]["enabled"] is True
    with pytest.raises(TypeError):
        bound.response.content["nested"]["enabled"] = False  # type: ignore[index]


def test_url_leaf_supports_optional_url_assertion_and_actions() -> None:
    plan = expect_url("checkout", url="https://example.test/pay").accept()
    assert plan.mode == "url"
    assert plan.response == ElicitationResponse(action="accept")
    assert maybe_url("other").decline().optional_occurrence is True


def test_form_actions_validate_content_and_cannot_be_rebound() -> None:
    with pytest.raises(ModelValidationError):
        expect_form("x").accept(None)  # type: ignore[arg-type]
    with pytest.raises(ModelValidationError):
        expect_url("x").accept({"unexpected": True})  # type: ignore[arg-type]

    plan = expect_form("x").accept({"ok": True})
    with pytest.raises(ModelValidationError):
        plan.accept({"again": True})  # type: ignore[attr-defined]


def test_combinators_require_complete_children_and_validate_round_children() -> None:
    incomplete = expect_form("x")
    with pytest.raises(ModelValidationError):
        sequence(incomplete, expect_form("y").accept({}))
    with pytest.raises(ModelValidationError):
        sequence()
    with pytest.raises(ModelValidationError):
        optional(incomplete)
    with pytest.raises(ModelValidationError):
        one_of(expect_form("x").accept({}))
    with pytest.raises(ModelValidationError):
        round_of(expect_form("x").accept({}))
    with pytest.raises(ModelValidationError):
        round_of(
            expect_form("x").accept({}),
            optional(expect_form("y").accept({})),
        )
    with pytest.raises(ModelValidationError):
        round_of(
            expect_form("x").accept({}),
            expect_form("x").accept({}),
        )
    with pytest.raises(ModelValidationError):
        one_of(
            expect_form("x").accept({}),
            expect_form("x").accept({}),
        )


def test_plan_serialization_is_json_durable_and_server_is_normalized() -> None:
    class Server:
        name = "profiles"

    plan = expect_form("profile", server=Server()).accept({"ok": True})
    dumped = plan.model_dump(mode="json")
    assert dumped["request"]["server"] == "profiles"
    assert json.loads(plan.model_dump_json()) == dumped
    restored = ElicitationPlan.model_validate(dumped)
    assert restored == plan
    assert restored.canonical_json() == plan.canonical_json()

    url_plan = expect_url("checkout").accept()
    assert (
        ElicitationPlan.model_validate(url_plan.model_dump(mode="json")).mode == "url"
    )

    with pytest.raises(ModelValidationError):
        expect_form("incomplete").model_dump(mode="json")


def test_canonical_equality_uses_json_number_semantics() -> None:
    left = expect_form("x").accept({"value": 1})
    right = expect_form("x").accept({"value": 1.0})
    assert left.canonical_json() == right.canonical_json()
    assert left.canonical_identity() == right.canonical_identity()


def test_candidate_matching_handles_optional_and_alternative_prefixes() -> None:
    a = expect_form("a", message="A").accept({"ok": True})
    b = expect_form("b", message="B").accept({"ok": True})

    matcher = sequence(optional(a), a).matcher()
    assert matcher.match_round({"a": form_request("a", message="A")}) == {
        "a": a.response
    }
    assert matcher.complete() == {"a": a.response}

    matcher = one_of(a, sequence(a, b)).matcher()
    matcher.match_round({"a": form_request("a", message="A")})
    assert matcher.complete() == {"a": a.response}


def test_sequence_requires_distinct_rounds_and_round_of_is_exhaustive() -> None:
    a = expect_form("a").accept({"a": 1})
    b = expect_form("b").accept({"b": 2})
    with pytest.raises(ElicitationExpectationError):
        sequence(a, b).matcher().match_round(
            {"a": form_request("a"), "b": form_request("b")}
        )

    plan = round_of(a, b)
    responses = plan.matcher().match_round(
        {"a": form_request("a"), "b": form_request("b")}
    )
    assert responses == {"a": a.response, "b": b.response}


def test_nested_sequence_preserves_unfinished_child_state() -> None:
    a = expect_form("a").accept({"a": 1})
    b = expect_form("b").accept({"b": 2})
    c = expect_form("c").accept({"c": 3})
    plan = sequence(sequence(a, b), c)

    matcher = plan.matcher()
    assert matcher.match_round({"a": form_request("a")}) == {"a": a.response}
    with pytest.raises(ElicitationExpectationError):
        matcher.match_round({"c": form_request("c")})

    matcher = plan.matcher()
    assert matcher.match_round({"a": form_request("a")}) == {"a": a.response}
    assert matcher.match_round({"b": form_request("b")}) == {"b": b.response}
    assert matcher.match_round({"c": form_request("c")}) == {"c": c.response}
    assert matcher.complete() == {"c": c.response}


def test_url_request_matches_without_visiting_url() -> None:
    plan = expect_url("checkout", url="https://example.test/pay").accept()
    responses = plan.matcher().match_round(
        {"checkout": url_request("checkout", "https://example.test/pay")}
    )
    assert responses["checkout"] == ElicitationResponse(action="accept")


def test_matcher_enforces_context_qualifiers_and_round_shape() -> None:
    plan = expect_form(
        "profile",
        message="Profile",
        server="profiles",
        operation_kind="tool",
        operation_name="create_profile",
    ).accept({"ok": True})
    matcher = plan.matcher()
    with pytest.raises(ElicitationExpectationError):
        matcher.match_round(
            {
                "profile": FormElicitationRequest(
                    request_key="profile",
                    message="Profile",
                    requested_schema={"type": "object"},
                    server="other",
                    operation_kind="tool",
                    operation_name="create_profile",
                )
            }
        )
    with pytest.raises(ElicitationExpectationError):
        plan.matcher().match_round({"other": form_request("other", message="Profile")})


def test_different_response_paths_are_ambiguous_but_equivalent_paths_are_not() -> None:
    left = expect_form("x").accept({"choice": "left"})
    right = expect_form("x").accept({"choice": "right"})
    with pytest.raises(ElicitationExpectationError):
        one_of(left, right).matcher().match_round({"x": form_request("x")})

    same_left = expect_form("x").accept({"choice": "same"})
    same_right = expect_form("x", operation_kind="tool").accept({"choice": "same"})
    matcher = one_of(same_left, same_right).matcher()
    assert matcher.match_round(
        {
            "x": FormElicitationRequest(
                request_key="x",
                message="Enter a value",
                requested_schema={"type": "object"},
                operation_kind="tool",
            )
        }
    ) == {"x": same_left.response}
    assert matcher.complete() == {"x": same_left.response}


def test_optional_leaf_can_finish_without_a_round_and_missing_required_fails() -> None:
    optional_plan = maybe_form("optional").accept({"enabled": True})
    assert optional_plan.matcher().complete() == {}
    matcher = sequence(
        optional_plan,
        expect_form("required").accept({"ok": True}),
    ).matcher()
    with pytest.raises(ElicitationExpectationError):
        matcher.complete()


def test_matcher_rejects_unexpected_and_extra_round_keys() -> None:
    plan = expect_form("expected").accept({"ok": True})
    with pytest.raises(ElicitationExpectationError):
        plan.matcher().match_round({"unexpected": form_request("unexpected")})
    with pytest.raises(ElicitationExpectationError):
        plan.matcher().match_round(
            {"expected": form_request("expected"), "extra": form_request("extra")}
        )


def test_canonical_equality_distinguishes_booleans_and_array_order() -> None:
    first = expect_form("x").accept({"value": True, "items": [1, 2]})
    second = expect_form("x").accept({"value": 1, "items": [1, 2]})
    reversed_items = expect_form("x").accept({"value": True, "items": [2, 1]})
    assert first.canonical_identity() != second.canonical_identity()
    assert first.canonical_identity() != reversed_items.canonical_identity()


def test_meta_and_task_are_deeply_immutable_and_non_finite_values_fail() -> None:
    meta = {"nested": {"value": "original"}}
    task = {"metadata": {"attempt": 1}}
    request = FormElicitationRequest(
        request_key="x",
        message="Enter a value",
        requested_schema={"type": "object"},
        meta=meta,
        task=task,
    )
    meta["nested"]["value"] = "changed"  # type: ignore[index]
    task["metadata"]["attempt"] = 2  # type: ignore[index]
    assert request.meta["nested"]["value"] == "original"  # type: ignore[index]
    assert request.task["metadata"]["attempt"] == 1  # type: ignore[index]

    with pytest.raises(ValueError):
        FormElicitationRequest(
            request_key="x",
            message="Enter a value",
            requested_schema={"value": float("nan")},
        )


def test_pending_round_is_keyed_and_uses_discriminated_observed_requests() -> None:
    round_value = PendingElicitationRound(
        round_id="round-1",
        execution_id="execution-1",
        logical_operation_id="operation-1",
        server="profiles",
        operation_kind="tool",
        operation_name="create_profile",
        requests={
            "profile": form_request("profile"),
            "checkout": url_request("checkout", "https://example.test/pay"),
        },
        created_at=datetime.now(timezone.utc),
    )
    assert round_value.requests["checkout"].mode == "url"


@pytest.mark.parametrize(
    "plan_factory",
    [
        lambda: sequence(
            expect_form("first").accept({}), expect_form("second").accept({})
        ),
        lambda: one_of(
            expect_form("first").accept({}), expect_form("second").accept({})
        ),
        lambda: optional(expect_form("first").accept({})),
        lambda: round_of(
            expect_form("first").accept({}), expect_form("second").accept({})
        ),
    ],
)
def test_deserialization_rejects_incomplete_composite_children(plan_factory) -> None:
    payload = plan_factory().model_dump(mode="json")
    child = payload["children"][0]
    child["response"] = None
    with pytest.raises((ValidationError, ModelValidationError)):
        ElicitationPlan.model_validate(payload)


def test_deserialization_rejects_optional_composite_and_invalid_response_shapes() -> (
    None
):
    composite_payload = sequence(
        expect_form("first").accept({}), expect_form("second").accept({})
    ).model_dump(mode="json")
    composite_payload["optional_occurrence"] = True
    with pytest.raises((ValidationError, ModelValidationError)):
        ElicitationPlan.model_validate(composite_payload)

    form_payload = expect_form("form").accept({"ok": True}).model_dump(mode="json")
    form_payload["response"]["content"] = None
    with pytest.raises((ValidationError, ModelValidationError)):
        ElicitationPlan.model_validate(form_payload)

    url_payload = expect_url("url").accept().model_dump(mode="json")
    url_payload["response"]["content"] = {}
    with pytest.raises((ValidationError, ModelValidationError)):
        ElicitationPlan.model_validate(url_payload)


def test_pending_round_rejects_empty_and_mismatched_request_keys() -> None:
    common = {
        "round_id": "round-1",
        "execution_id": "execution-1",
        "logical_operation_id": "operation-1",
        "server": "profiles",
        "operation_kind": "tool",
        "operation_name": "create_profile",
        "created_at": datetime.now(timezone.utc),
    }
    with pytest.raises(ValidationError):
        PendingElicitationRound(requests={}, **common)
    with pytest.raises(ValidationError):
        PendingElicitationRound(
            requests={"different": form_request("profile")},
            **common,
        )


def test_large_integer_canonical_identity_does_not_use_float_rounding() -> None:
    value = 10**400
    same = expect_form("x").accept({"value": value})
    equal = expect_form("x").accept({"value": value})
    different = expect_form("x").accept({"value": value + 1})
    assert same.canonical_identity() == equal.canonical_identity()
    assert same.canonical_identity() != different.canonical_identity()
