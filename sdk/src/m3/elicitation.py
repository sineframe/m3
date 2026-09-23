"""Immutable plans for MCP multi-round elicitation.

This module contains the value model and deterministic matcher only.  It does
not own a client, retry a request, or interact with a harness; those concerns
belong to the execution adapters.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, TypeAlias, cast

from pydantic import ConfigDict, Field, model_validator

from ._types.base import FrozenModel
from .errors import ElicitationExpectationError, ModelValidationError

OperationKind: TypeAlias = Literal["tool", "prompt", "resource"]
_PlanNode: TypeAlias = Literal["leaf", "sequence", "optional", "one_of", "round_of"]


def _number_key(value: int | float) -> tuple[str, str]:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ModelValidationError("JSON numbers must be finite") from error
    if not decimal.is_finite():
        raise ModelValidationError("JSON numbers must be finite")
    if decimal == 0:
        return ("number", "0")
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return ("number", rendered)


def _canonical(value: object) -> object:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return _number_key(value)
    if isinstance(value, float):
        return _number_key(value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, Mapping):
        return (
            "object",
            tuple(
                sorted(
                    ((str(key), _canonical(item)) for key, item in value.items()),
                    key=lambda item: item[0],
                )
            ),
        )
    if isinstance(value, (list, tuple)):
        return ("array", tuple(_canonical(item) for item in value))
    if isinstance(value, FrozenModel):
        return _canonical(value.model_dump(mode="json"))
    raise ModelValidationError("value is not JSON-serializable")


def _canonical_json(value: object) -> str:
    """Serialize a tagged canonical form without losing large numbers."""

    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"))


class FormElicitationRequest(FrozenModel):
    """A normalized form-mode elicitation request."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    request_key: str = Field(min_length=1)
    mode: Literal["form"] = "form"
    message: str
    requested_schema: Mapping[str, object]
    meta: Mapping[str, object] | None = None
    task: Mapping[str, object] | None = None
    server: str | None = None
    operation_kind: OperationKind | None = None
    operation_name: str | None = None


class UrlElicitationRequest(FrozenModel):
    """A normalized URL-mode elicitation request."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    request_key: str = Field(min_length=1)
    mode: Literal["url"] = "url"
    message: str
    url: str
    elicitation_id: str | None = None
    meta: Mapping[str, object] | None = None
    task: Mapping[str, object] | None = None
    server: str | None = None
    operation_kind: OperationKind | None = None
    operation_name: str | None = None


ElicitationRequest: TypeAlias = FormElicitationRequest | UrlElicitationRequest
_ObservedRequest: TypeAlias = Annotated[
    FormElicitationRequest | UrlElicitationRequest,
    Field(discriminator="mode"),
]


class _FormExpectation(FrozenModel):
    mode: Literal["form"] = "form"
    request_key: str = Field(min_length=1)
    message: str | None = None
    requested_schema: Mapping[str, object] | None = None
    meta: Mapping[str, object] | None = None
    task: Mapping[str, object] | None = None
    server: str | None = None
    operation_kind: OperationKind | None = None
    operation_name: str | None = None


class _UrlExpectation(FrozenModel):
    mode: Literal["url"] = "url"
    request_key: str = Field(min_length=1)
    message: str | None = None
    url: str | None = None
    elicitation_id: str | None = None
    meta: Mapping[str, object] | None = None
    task: Mapping[str, object] | None = None
    server: str | None = None
    operation_kind: OperationKind | None = None
    operation_name: str | None = None


_PlanRequest: TypeAlias = Annotated[
    _FormExpectation | _UrlExpectation,
    Field(discriminator="mode"),
]


class ElicitationResponse(FrozenModel):
    """The response that will be associated with one request key."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    action: Literal["accept", "decline", "cancel"]
    content: Mapping[str, object] | None = None
    meta: Mapping[str, object] | None = None

    @model_validator(mode="after")
    def _validate_content(self) -> ElicitationResponse:
        if self.action != "accept" and self.content is not None:
            raise ValueError("decline and cancel responses cannot contain content")
        return self


class PendingElicitationRound(FrozenModel):
    """A persisted, keyed set of elicitation requests awaiting responses."""

    round_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    logical_operation_id: str = Field(min_length=1)
    server: str = Field(min_length=1)
    operation_kind: OperationKind
    operation_name: str = Field(min_length=1)
    request_state: str | None = None
    requests: Mapping[str, _ObservedRequest]
    created_at: datetime
    deadline: datetime | None = None

    @model_validator(mode="after")
    def _validate_requests(self) -> PendingElicitationRound:
        if not self.requests:
            raise ValueError("pending elicitation round requires requests")
        if any(key != request.request_key for key, request in self.requests.items()):
            raise ValueError("pending request keys must match their request_key")
        return self


class ElicitationPlan(FrozenModel):
    """An immutable, serializable elicitation expectation tree."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    node: _PlanNode = "leaf"
    request: _PlanRequest | None = None
    response: ElicitationResponse | None = None
    children: tuple[ElicitationPlan, ...] = ()
    optional_occurrence: bool = False

    @model_validator(mode="after")
    def _validate_shape(self) -> ElicitationPlan:
        is_leaf = self.node == "leaf"
        if is_leaf != (self.request is not None):
            raise ModelValidationError("leaf plans require exactly one request")
        if not is_leaf and self.optional_occurrence:
            raise ModelValidationError("composite plans cannot be optional leaves")
        if is_leaf and self.children:
            raise ModelValidationError("leaf plans cannot contain children")
        if not is_leaf and self.response is not None:
            raise ModelValidationError("composite plans cannot contain a response")
        if not is_leaf and any(not child.is_complete for child in self.children):
            raise ModelValidationError("composite plans require complete children")
        if is_leaf and self.optional_occurrence and self.request is None:
            raise ModelValidationError("optional plan must contain a request")
        if self.node == "optional" and len(self.children) != 1:
            raise ModelValidationError("optional requires one child")
        if self.node in {"sequence", "one_of", "round_of"} and len(self.children) < 2:
            raise ModelValidationError(f"{self.node} requires at least two children")
        if self.node == "round_of":
            keys: set[str] = set()
            for child in self.children:
                if child.node != "leaf" or child.optional_occurrence:
                    raise ModelValidationError(
                        "round_of children must be required direct leaves"
                    )
                if child.request is None or child.request.request_key in keys:
                    raise ModelValidationError("round_of request keys must be unique")
                keys.add(child.request.request_key)
        if self.node == "one_of":
            if any(_can_empty(child) for child in self.children):
                raise ModelValidationError("one_of children must consume a round")
            identities = [_plan_identity(child) for child in self.children]
            if len(set(identities)) != len(identities):
                raise ModelValidationError("one_of alternatives must be distinct")
        if self.node == "optional" and not self.children[0].is_complete:
            raise ModelValidationError("optional requires a complete child")
        if is_leaf and self.response is not None:
            if isinstance(self.request, _FormExpectation):
                if self.response.action == "accept" and self.response.content is None:
                    raise ModelValidationError(
                        "form acceptance requires mapping content"
                    )
            elif self.response.action == "accept" and self.response.content is not None:
                raise ModelValidationError("URL acceptance cannot contain content")
        return self

    @property
    def is_complete(self) -> bool:
        if self.node == "leaf":
            return self.response is not None
        return all(child.is_complete for child in self.children)

    @property
    def mode(self) -> Literal["form", "url"] | None:
        if isinstance(self.request, _FormExpectation):
            return "form"
        if isinstance(self.request, _UrlExpectation):
            return "url"
        return None

    @property
    def requested_schema(self) -> Mapping[str, object] | None:
        return (
            self.request.requested_schema
            if isinstance(self.request, _FormExpectation)
            else None
        )

    @property
    def optional(self) -> bool:
        return self.optional_occurrence or self.node == "optional"

    def accept(self, content: Mapping[str, object] | None = None) -> ElicitationPlan:
        if self.node != "leaf" or self.response is not None:
            raise ModelValidationError("only an unbound leaf can bind a response")
        if isinstance(self.request, _UrlExpectation):
            if content is not None:
                raise ModelValidationError("URL acceptance cannot contain form content")
        elif not isinstance(content, Mapping):
            raise ModelValidationError("form acceptance requires mapping content")
        values = super().model_dump(mode="python")
        values["response"] = ElicitationResponse(action="accept", content=content)
        return type(self).model_validate(values)

    def decline(self) -> ElicitationPlan:
        return self._bind_action("decline")

    def cancel(self) -> ElicitationPlan:
        return self._bind_action("cancel")

    def _bind_action(self, action: Literal["decline", "cancel"]) -> ElicitationPlan:
        if self.node != "leaf" or self.response is not None:
            raise ModelValidationError("only an unbound leaf can bind a response")
        values = super().model_dump(mode="python")
        values["response"] = ElicitationResponse(action=action)
        return type(self).model_validate(values)

    def canonical_identity(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))

    def canonical_json(self) -> str:
        return self.canonical_identity()

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if not self.is_complete:
            raise ModelValidationError("an elicitation plan must be complete")
        return super().model_dump(*args, **kwargs)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        if not self.is_complete:
            raise ModelValidationError("an elicitation plan must be complete")
        return super().model_dump_json(*args, **kwargs)

    def matcher(self) -> PlanMatcher:
        if not self.is_complete:
            raise ModelValidationError("an elicitation plan must be complete")
        return PlanMatcher(self)


class _Done:
    pass


_DONE = _Done()


class _Transition:
    __slots__ = ("consumed", "node", "responses")

    def __init__(
        self,
        node: ElicitationPlan | _Done,
        responses: dict[str, ElicitationResponse],
        consumed: bool,
    ) -> None:
        self.node = node
        self.responses = responses
        self.consumed = consumed


class _Candidate:
    __slots__ = ("history", "last_responses", "node")

    def __init__(
        self,
        node: ElicitationPlan | _Done,
        history: tuple[tuple[int, tuple[str, ...], str], ...],
        last_responses: dict[str, ElicitationResponse] | None = None,
    ) -> None:
        self.node = node
        self.history = history
        self.last_responses = last_responses or {}


def _plan_identity(plan: ElicitationPlan) -> str:
    # This bypasses the public complete-plan serialization guard only for
    # construction-time duplicate detection, where children are already bound.
    return _canonical_json(plan.__class__.model_dump(plan, mode="json"))


def _can_empty(plan: ElicitationPlan) -> bool:
    if plan.node == "optional":
        return True
    if plan.node == "leaf":
        return plan.optional_occurrence
    if plan.node == "sequence":
        return all(_can_empty(child) for child in plan.children)
    if plan.node == "one_of":
        return any(_can_empty(child) for child in plan.children)
    return False


def _normalize_server(server: object | None) -> str | None:
    if server is None:
        return None
    if isinstance(server, str):
        if not server:
            raise ModelValidationError("server name must not be empty")
        return server
    for attribute in ("name", "key", "server_name"):
        value = getattr(server, attribute, None)
        if isinstance(value, str) and value:
            return value
    raise ModelValidationError("server must be a name or named server definition")


def _leaf(
    request: _PlanRequest,
    *,
    optional_occurrence: bool = False,
) -> ElicitationPlan:
    return ElicitationPlan(
        node="leaf",
        request=request,
        optional_occurrence=optional_occurrence,
    )


def expect_form(
    request_key: str,
    *,
    message: str | None = None,
    schema: Mapping[str, object] | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan:
    return _leaf(
        _FormExpectation(
            request_key=request_key,
            message=message,
            requested_schema=schema,
            server=_normalize_server(server),
            operation_kind=operation_kind,
            operation_name=operation_name,
        )
    )


def maybe_form(
    request_key: str,
    *,
    message: str | None = None,
    schema: Mapping[str, object] | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan:
    return _leaf(
        _FormExpectation(
            request_key=request_key,
            message=message,
            requested_schema=schema,
            server=_normalize_server(server),
            operation_kind=operation_kind,
            operation_name=operation_name,
        ),
        optional_occurrence=True,
    )


def expect_url(
    request_key: str,
    *,
    message: str | None = None,
    url: str | None = None,
    elicitation_id: str | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan:
    return _leaf(
        _UrlExpectation(
            request_key=request_key,
            message=message,
            url=url,
            elicitation_id=elicitation_id,
            server=_normalize_server(server),
            operation_kind=operation_kind,
            operation_name=operation_name,
        )
    )


def maybe_url(
    request_key: str,
    *,
    message: str | None = None,
    url: str | None = None,
    elicitation_id: str | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan:
    return _leaf(
        _UrlExpectation(
            request_key=request_key,
            message=message,
            url=url,
            elicitation_id=elicitation_id,
            server=_normalize_server(server),
            operation_kind=operation_kind,
            operation_name=operation_name,
        ),
        optional_occurrence=True,
    )


def _complete_children(children: tuple[ElicitationPlan, ...]) -> None:
    if not children or any(not child.is_complete for child in children):
        raise ModelValidationError("combinators require complete children")


def sequence(*children: ElicitationPlan) -> ElicitationPlan:
    values = tuple(children)
    _complete_children(values)
    return ElicitationPlan(node="sequence", children=values)


def optional(child: ElicitationPlan) -> ElicitationPlan:
    _complete_children((child,))
    return ElicitationPlan(node="optional", children=(child,))


def one_of(*children: ElicitationPlan) -> ElicitationPlan:
    values = tuple(children)
    _complete_children(values)
    return ElicitationPlan(node="one_of", children=values)


def round_of(*children: ElicitationPlan) -> ElicitationPlan:
    values = tuple(children)
    _complete_children(values)
    return ElicitationPlan(node="round_of", children=values)


def _request_matches(
    expected: _PlanRequest,
    actual: ElicitationRequest,
) -> bool:
    if isinstance(expected, _FormExpectation) != isinstance(
        actual, FormElicitationRequest
    ):
        return False
    if expected.request_key != actual.request_key:
        return False
    if expected.message is not None and expected.message != actual.message:
        return False
    if expected.server is not None and expected.server != actual.server:
        return False
    if (
        expected.operation_kind is not None
        and expected.operation_kind != actual.operation_kind
    ):
        return False
    if (
        expected.operation_name is not None
        and expected.operation_name != actual.operation_name
    ):
        return False
    if isinstance(expected, _FormExpectation):
        if not isinstance(actual, FormElicitationRequest):
            return False
        return expected.requested_schema is None or _canonical(
            expected.requested_schema
        ) == _canonical(actual.requested_schema)
    if not isinstance(actual, UrlElicitationRequest):
        return False
    expected_url = expected
    actual_url = actual
    return (expected_url.url is None or expected_url.url == actual_url.url) and (
        expected_url.elicitation_id is None
        or expected_url.elicitation_id == actual_url.elicitation_id
    )


def _leaf_transition(
    plan: ElicitationPlan,
    actual: Mapping[str, ElicitationRequest],
) -> list[_Transition]:
    assert plan.request is not None
    if len(actual) != 1 or plan.request.request_key not in actual:
        return []
    if not _request_matches(plan.request, actual[plan.request.request_key]):
        return []
    assert plan.response is not None
    return [_Transition(_DONE, {plan.request.request_key: plan.response}, True)]


def _transitions(
    plan: ElicitationPlan | _Done,
    actual: Mapping[str, ElicitationRequest],
) -> list[_Transition]:
    if not isinstance(plan, ElicitationPlan):
        return []
    if plan.node == "leaf":
        transitions = _leaf_transition(plan, actual)
        if plan.optional_occurrence:
            transitions.append(_Transition(_DONE, {}, False))
        return transitions
    if plan.node == "optional":
        child = plan.children[0]
        return [*_transitions(child, actual), _Transition(_DONE, {}, False)]
    if plan.node == "one_of":
        values: list[_Transition] = []
        for child in plan.children:
            values.extend(_transitions(child, actual))
        return values
    if plan.node == "round_of":
        expected = {
            child.request.request_key: child
            for child in plan.children
            if child.request is not None
        }
        if set(actual) != set(expected):
            return []
        responses: dict[str, ElicitationResponse] = {}
        for key, child in expected.items():
            child_transitions = _leaf_transition(child, {key: actual[key]})
            if not child_transitions:
                return []
            responses.update(child_transitions[0].responses)
        return [_Transition(_DONE, responses, True)]
    assert plan.node == "sequence"
    first, *rest = plan.children
    first_transitions = _transitions(first, actual)
    values = []
    for transition in first_transitions:
        if transition.consumed:
            remainder: ElicitationPlan | _Done
            if transition.node is not _DONE:
                unfinished = cast(ElicitationPlan, transition.node)
                if rest:
                    remainder = sequence(unfinished, *rest)
                else:
                    remainder = unfinished
            elif len(rest) > 1:
                remainder = sequence(*rest)
            elif rest:
                remainder = rest[0]
            else:
                remainder = _DONE
            values.append(_Transition(remainder, transition.responses, True))
        elif rest:
            next_plan = rest[0] if len(rest) == 1 else sequence(*rest)
            for next_transition in _transitions(next_plan, actual):
                values.append(
                    _Transition(
                        next_transition.node,
                        next_transition.responses,
                        next_transition.consumed,
                    )
                )
        else:
            values.append(transition)
    return values


class PlanMatcher:
    """Internal candidate-state matcher for a completed elicitation plan."""

    def __init__(self, plan: ElicitationPlan) -> None:
        self._candidates = [_Candidate(plan, ())]

    def match_round(
        self,
        requests: Mapping[str, ElicitationRequest],
    ) -> dict[str, ElicitationResponse]:
        if not requests:
            raise ElicitationExpectationError("elicitation round is empty")
        actual = dict(requests)
        next_candidates: list[_Candidate] = []
        response_options: dict[str, str] = {}
        for candidate in self._candidates:
            for transition in _transitions(candidate.node, actual):
                # A complete non-empty round must be consumed.  Epsilon
                # transitions are used internally to skip optional nodes, but
                # cannot make an unrelated actual round disappear.
                if not transition.consumed:
                    continue
                history = candidate.history
                if transition.consumed:
                    history = (
                        *history,
                        (
                            len(history),
                            tuple(sorted(actual)),
                            _canonical_json(transition.responses),
                        ),
                    )
                node = transition.node
                next_candidates.append(
                    _Candidate(node, history, dict(transition.responses))
                )
                response_options[_canonical_json(transition.responses)] = ""
        if not next_candidates:
            raise ElicitationExpectationError("elicitation round did not match plan")
        if len(response_options) > 1:
            raise ElicitationExpectationError(
                "elicitation round has ambiguous response paths"
            )
        deduped: dict[tuple[str, str], _Candidate] = {}
        for candidate in next_candidates:
            node_identity = (
                "done"
                if candidate.node is _DONE
                else _plan_identity(cast(ElicitationPlan, candidate.node))
            )
            deduped[(node_identity, repr(candidate.history))] = candidate
        self._candidates = list(deduped.values())
        return dict(next_candidates[0].last_responses)

    def complete(self) -> dict[str, ElicitationResponse]:
        completed = list(self._candidates)
        for candidate in tuple(self._candidates):
            if candidate.node is not _DONE:
                for transition in _transitions(candidate.node, {}):
                    if not transition.consumed and transition.node is _DONE:
                        completed.append(
                            _Candidate(
                                _DONE,
                                candidate.history,
                                candidate.last_responses,
                            )
                        )
        completed = [candidate for candidate in completed if candidate.node is _DONE]
        identities = {candidate.history for candidate in completed}
        if len(identities) != 1:
            raise ElicitationExpectationError(
                "elicitation plan did not resolve to one completed candidate"
            )
        return dict(completed[0].last_responses) if completed else {}


__all__ = [
    "ElicitationPlan",
    "ElicitationRequest",
    "ElicitationResponse",
    "FormElicitationRequest",
    "PendingElicitationRound",
    "UrlElicitationRequest",
    "expect_form",
    "expect_url",
    "maybe_form",
    "maybe_url",
    "one_of",
    "optional",
    "round_of",
    "sequence",
]
