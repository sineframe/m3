<!-- Generated from docs/site/reference/python/m3/elicitation.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Elicitation plans and direct request handling

An elicitation plan is an immutable description of the form or URL requests an
MCP action may make and the responses M3 should return. Attach a complete plan
to each action that can request input. M3 starts new matcher state for every
action, so reusing a plan does not share progress.

## Plan helpers

Import the public builders from `m3` or `m3.elicitation`:

```python
from collections.abc import Mapping
from typing import Literal

from m3 import (
    ElicitationPlan,
    ElicitationResponse,
    expect_form,
    expect_url,
    maybe_form,
    maybe_url,
    one_of,
    optional,
    round_of,
    sequence,
)

plan = sequence(
    expect_form("shipping_address").accept(
        {"street": "1 Main Street", "city": "Pune"}
    ),
    expect_url("identity_check").accept(),
)
```

The signatures are `expect_form(request_key: str, *, message: str | None =
None, schema: Mapping[str, object] | None = None, server: object | None =
None, operation_kind: Literal["tool", "prompt", "resource"] | None = None,
operation_name: str | None = None) -> ElicitationPlan`;
`maybe_form(...)` has the same signature. `expect_url(request_key: str, *,
message: str | None = None, url: str | None = None, elicitation_id: str |
None = None, server: object | None = None, operation_kind: Literal["tool",
"prompt", "resource"] | None = None, operation_name: str | None = None) ->
ElicitationPlan`; `maybe_url(...)` has the same signature.
`sequence(*children: ElicitationPlan) -> ElicitationPlan`,
`one_of(*children: ElicitationPlan) -> ElicitationPlan`,
`round_of(*children: ElicitationPlan) -> ElicitationPlan`, and
`optional(child: ElicitationPlan) -> ElicitationPlan` compose completed
expectations.

Pass `server` as a non-empty server name or as a server definition whose
`name`, `key`, or `server_name` attribute is non-empty. `request_key` is also
required and must be non-empty. The remaining qualifiers are optional:
`operation_kind`, `operation_name`, `message`, `schema`, `url`, and
`elicitation_id`. Omit a qualifier to accept any observed value, or supply one
to require an exact match. M3 compares a form `schema` structurally. Object key
order does not matter; array order and JSON value types do.

Each builder returns an unbound leaf. Bind its response with one of these
methods:

```text
ElicitationPlan.accept(
    self, content: Mapping[str, object] | None = None
) -> ElicitationPlan
ElicitationPlan.decline(self) -> ElicitationPlan
ElicitationPlan.cancel(self) -> ElicitationPlan
```

Form acceptance requires a mapping, including `{}` when the schema permits an
empty object. URL acceptance, decline, and cancel take no content. Bound
responses are immutable. To include response metadata, put the
`ElicitationResponse` in a complete serialized plan and pass it to
`ElicitationPlan.model_validate(...)`. Validation rechecks the plan invariants
without mutating an existing bound response.

## Composition and ordering

Every combinator requires complete children, and a complete leaf has a bound
response. `sequence(a, b)` consumes `a` in one protocol round and `b` in a
later round. It matches server order without issuing requests or changing that
order. `round_of(a, b)` requires both direct leaf requests in one round, with
unique request keys. `one_of(a, b)` selects one distinct, non-empty
alternative. When an observed round can select paths with different responses,
matching raises `ElicitationExpectationError` for ambiguity. `optional(a)`
allows its whole child to be skipped. `maybe_form` and `maybe_url` make one
leaf occurrence optional while still consuming it if it appears.

An optional leaf may be followed by a required leaf. The matcher considers
both choices and resolves from the observed request. A single non-empty round
must be consumed by the selected plan. Unknown request keys, extra keys,
wrong mode, wrong qualifiers, a required plan left unused, and a round shape
that does not match raise `ElicitationExpectationError`. Round overflow raises
`ElicitationRoundLimitError`. Composite plans must have at least two children
for `sequence`, `one_of`, and `round_of`; `optional` has exactly one child.
An alternative cannot be empty, and duplicate `one_of` alternatives are
rejected at construction.

## Public models

`ElicitationResponse` is a frozen model with `action: Literal["accept",
"decline", "cancel"]`, `content: Mapping[str, object] | None = None`, and
`meta: Mapping[str, object] | None = None`. Non-accept actions reject content.
Form acceptance requires mapping content; URL acceptance rejects content.

`FormElicitationRequest` fields are `request_key: str`, `mode: Literal["form"] =
"form"`, `message: str`, `requested_schema: Mapping[str, object]`, and
`meta`, `task`, `server`, `operation_kind`, and `operation_name`, each optional
and defaulting to `None`. `UrlElicitationRequest` has `request_key`,
`mode: Literal["url"] = "url"`, `message`, `url`, optional
`elicitation_id`, and the same optional context fields. Both are immutable,
deeply freeze nested values, and reject unknown fields. The
`ElicitationRequest` union is form or URL.

`ElicitationPlan` is immutable. Its serialized fields are `node` (default
`"leaf"`), `request` (default `None`), `response` (default `None`), `children`
(default empty tuple), and `optional_occurrence` (default `False`). Its
read-only properties are `is_complete`, `mode`, `requested_schema`, and
`optional`. Complete plans can be serialized using `model_dump()` or
`model_dump_json()`, restored with `ElicitationPlan.model_validate(...)`, and
compared by `canonical_identity()` or `canonical_json()`. Both canonical
identity methods return a stable JSON string; equivalent JSON numbers such as
`1` and `1.0` compare equally, while booleans and array order remain distinct.

`PendingElicitationRound` is an immutable managed-input view. Its fields are
`round_id`, `execution_id`, `logical_operation_id`, `server`,
`operation_kind`, `operation_name`, `requests`, and `created_at`, all required;
`request_state` and `deadline` default to `None`. `requests` must be non-empty,
and each mapping key must equal the contained request's `request_key`.
`request_state` is opaque protocol state that M3 returns unchanged on retry.

## Binding a plan to an action

The direct client methods `call_tool`, `get_prompt`, and `read_resource` accept
`elicitation: ElicitationPlan | None = None` and
`elicitation_round_limit: int = 10`. An operation cannot combine a plan with
`input_responses`, `request_state`, or `allow_input_required`. M3 replays the
same operation arguments with the opaque `request_state` and only the current
round's keyed responses, validating the response schema before retry. Neither
synchronous nor asynchronous direct SDK use needs an agent harness.

Agent methods `agent.run(...)`, `agent.submit(...)`, and `session.send(...)`
accept the same plan and default round limit. The `elicitation` field on a
submitted execution applies to its initial action. A
`session.send` plan applies to that turn only; create a fresh complete plan for
each later turn that can elicit. Do not attach a plan to long-lived session
creation. M3 tests action-bound elicitation with Codex CLI `0.156.1` and Pi
`0.85.1`; other agent harness paths are unverified. Managed-input
`elicitation_round_limit` on Pi must be from 1 through 1024; values above 1024
raise `ModelValidationError`.
Codex has no equivalent adapter-specific maximum in this path. See
[compatibility](reference-compatibility.md).

For an accepted URL response, M3 returns the configured `accept` action. It
does not visit the URL, authenticate, or assert that an external checkout or
consent flow completed.

## Direct request and response models

With `allow_input_required=True` and no automatic plan, direct operations may
return `InputRequiredResult` instead of the normal tool, prompt, or resource
result. `InputRequiredResult.input_requests` contains the requests and
`request_state` carries the server's opaque retry value. For tool requests,
pass the returned state and an `input_responses` mapping keyed by the request
keys on the next `call_tool`. Values use MCP `ElicitResult` instances. A
request result can also include other MCP input request types handled by
configured direct-client callbacks. The manual guide shows the full tool
round-trip: [Handle elicitation directly with the SDK](guides-elicitation-manual.md).

## Validation and limits

M3 validates accepted form content against the server's requested JSON Schema
using Draft 2020-12. Local references such as `#/$defs/address` work. M3 does
not fetch remote references, so an unresolved remote `$ref` fails validation.
The server still decides whether the returned content meets its application
rules. In the managed path, response maps must contain exactly the pending
keys, and schema validation and persistence happen in one store transaction.
The managed-input reference documents the persistent contract:
[Managed-input API](reference-python-m3-managed-input.md).

## Typed errors

`ElicitationExpectationError` reports missing plans, mismatched requests,
ambiguous alternatives, unresolved required leaves, and mismatched form
content. `ElicitationRoundLimitError` reports more input rounds than
`elicitation_round_limit`. `ModelValidationError` reports invalid or incomplete
plans and incompatible API arguments. Managed submissions use
`ManagedInputValidationError`, `ManagedInputConflict`,
`ManagedInputStateError`, and `ManagedInputRecoveryError` for invalid response
content or keys, stale or conflicting updates, invalid round state, and
unavailable safe recovery.

See also [Compose elicitation workflows](guides-elicitation-composed.md),
[Respond to elicitation requests](guides-elicitation-responses.md), and
the [Python API inventory](https://m3.sineframe.com/docs/reference/python/api).
