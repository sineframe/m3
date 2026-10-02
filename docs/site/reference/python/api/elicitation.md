---
title: "m3.elicitation"
description: "Public Python API reference for m3.elicitation."
---

# `m3.elicitation`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `ElicitationPlan`

```python
m3.elicitation.ElicitationPlan(
    *,
    node: Literal['leaf', 'sequence', 'optional', 'one_of', 'round_of'] = 'leaf',
    request: m3.elicitation._FormExpectation | m3.elicitation._UrlExpectation | None = None,
    response: m3.elicitation.ElicitationResponse | None = None,
    children: tuple[m3.elicitation.ElicitationPlan, ...] = (),
    optional_occurrence: bool = False,
) -> None
```

An immutable, serializable elicitation expectation tree.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `node` | `Literal['leaf', 'sequence', 'optional', 'one_of', 'round_of']` | No | `'leaf'` | — | — |
| `request` | `m3.elicitation._FormExpectation \| m3.elicitation._UrlExpectation \| None` | No | `None` | `variant 1: discriminator='mode'` | — |
| `response` | `m3.elicitation.ElicitationResponse \| None` | No | `None` | — | — |
| `children` | `tuple[m3.elicitation.ElicitationPlan, ...]` | No | `()` | — | — |
| `optional_occurrence` | `bool` | No | `False` | — | — |
- `is_complete` (property)
- `mode` (property)
- `requested_schema` (property)
- `optional` (property)

```python
accept(
    self,
    content: Mapping[str, object] | None = None,
) -> ElicitationPlan
```

```python
decline(
    self,
) -> ElicitationPlan
```

```python
cancel(
    self,
) -> ElicitationPlan
```

```python
canonical_identity(
    self,
) -> str
```

```python
canonical_json(
    self,
) -> str
```

```python
model_dump(
    self,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]
```

```python
model_dump_json(
    self,
    *args: Any,
    **kwargs: Any,
) -> str
```

```python
matcher(
    self,
) -> PlanMatcher
```

## `ElicitationRequest`

`m3.elicitation.ElicitationRequest`

## `ElicitationResponse`

```python
m3.elicitation.ElicitationResponse(
    *,
    action: Literal['accept', 'decline', 'cancel'],
    content: collections.abc.Mapping[str, object] | None = None,
    meta: collections.abc.Mapping[str, object] | None = None,
) -> None
```

The response that will be associated with one request key.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `action` | `Literal['accept', 'decline', 'cancel']` | Yes | — | — | — |
| `content` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |
| `meta` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |

## `FormElicitationRequest`

```python
m3.elicitation.FormElicitationRequest(
    *,
    request_key: str,
    mode: Literal['form'] = 'form',
    message: str,
    requested_schema: collections.abc.Mapping[str, object],
    meta: collections.abc.Mapping[str, object] | None = None,
    task: collections.abc.Mapping[str, object] | None = None,
    server: str | None = None,
    operation_kind: Literal['tool', 'prompt', 'resource'] | None = None,
    operation_name: str | None = None,
) -> None
```

A normalized form-mode elicitation request.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `request_key` | `str` | Yes | — | `min_length=1` | — |
| `mode` | `Literal['form']` | No | `'form'` | — | — |
| `message` | `str` | Yes | — | — | — |
| `requested_schema` | `collections.abc.Mapping[str, object]` | Yes | — | — | — |
| `meta` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |
| `task` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |
| `server` | `str \| None` | No | `None` | — | — |
| `operation_kind` | `Literal['tool', 'prompt', 'resource'] \| None` | No | `None` | — | — |
| `operation_name` | `str \| None` | No | `None` | — | — |

## `PendingElicitationRound`

```python
m3.elicitation.PendingElicitationRound(
    *,
    round_id: str,
    execution_id: str,
    logical_operation_id: str,
    server: str,
    operation_kind: Literal['tool', 'prompt', 'resource'],
    operation_name: str,
    request_state: str | None = None,
    requests: collections.abc.Mapping[str, m3.elicitation.FormElicitationRequest | m3.elicitation.UrlElicitationRequest],
    created_at: datetime.datetime,
    deadline: datetime.datetime | None = None,
) -> None
```

A persisted, keyed set of elicitation requests awaiting responses.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `round_id` | `str` | Yes | — | `min_length=1` | — |
| `execution_id` | `str` | Yes | — | `min_length=1` | — |
| `logical_operation_id` | `str` | Yes | — | `min_length=1` | — |
| `server` | `str` | Yes | — | `min_length=1` | — |
| `operation_kind` | `Literal['tool', 'prompt', 'resource']` | Yes | — | — | — |
| `operation_name` | `str` | Yes | — | `min_length=1` | — |
| `request_state` | `str \| None` | No | `None` | — | — |
| `requests` | `collections.abc.Mapping[str, m3.elicitation.FormElicitationRequest \| m3.elicitation.UrlElicitationRequest]` | Yes | — | `type argument 2: discriminator='mode'` | — |
| `created_at` | `datetime.datetime` | Yes | — | — | — |
| `deadline` | `datetime.datetime \| None` | No | `None` | — | — |

## `UrlElicitationRequest`

```python
m3.elicitation.UrlElicitationRequest(
    *,
    request_key: str,
    mode: Literal['url'] = 'url',
    message: str,
    url: str,
    elicitation_id: str | None = None,
    meta: collections.abc.Mapping[str, object] | None = None,
    task: collections.abc.Mapping[str, object] | None = None,
    server: str | None = None,
    operation_kind: Literal['tool', 'prompt', 'resource'] | None = None,
    operation_name: str | None = None,
) -> None
```

A normalized URL-mode elicitation request.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `request_key` | `str` | Yes | — | `min_length=1` | — |
| `mode` | `Literal['url']` | No | `'url'` | — | — |
| `message` | `str` | Yes | — | — | — |
| `url` | `str` | Yes | — | — | — |
| `elicitation_id` | `str \| None` | No | `None` | — | — |
| `meta` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |
| `task` | `collections.abc.Mapping[str, object] \| None` | No | `None` | — | — |
| `server` | `str \| None` | No | `None` | — | — |
| `operation_kind` | `Literal['tool', 'prompt', 'resource'] \| None` | No | `None` | — | — |
| `operation_name` | `str \| None` | No | `None` | — | — |

## `expect_form`

```python
m3.elicitation.expect_form(
    request_key: str,
    *,
    message: str | None = None,
    schema: Mapping[str, object] | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan
```

## `expect_url`

```python
m3.elicitation.expect_url(
    request_key: str,
    *,
    message: str | None = None,
    url: str | None = None,
    elicitation_id: str | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan
```

## `maybe_form`

```python
m3.elicitation.maybe_form(
    request_key: str,
    *,
    message: str | None = None,
    schema: Mapping[str, object] | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan
```

## `maybe_url`

```python
m3.elicitation.maybe_url(
    request_key: str,
    *,
    message: str | None = None,
    url: str | None = None,
    elicitation_id: str | None = None,
    server: object | None = None,
    operation_kind: OperationKind | None = None,
    operation_name: str | None = None,
) -> ElicitationPlan
```

## `one_of`

```python
m3.elicitation.one_of(
    *children: ElicitationPlan,
) -> ElicitationPlan
```

## `optional`

```python
m3.elicitation.optional(
    child: ElicitationPlan,
) -> ElicitationPlan
```

## `round_of`

```python
m3.elicitation.round_of(
    *children: ElicitationPlan,
) -> ElicitationPlan
```

## `sequence`

```python
m3.elicitation.sequence(
    *children: ElicitationPlan,
) -> ElicitationPlan
```
