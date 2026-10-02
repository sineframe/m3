---
title: "m3.managed_input_api"
description: "Public Python API reference for m3.managed_input_api."
---

# `m3.managed_input_api`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `HumanInput`

```python
m3.managed_input_api.HumanInput(
    *args,
    **kwargs,
)
```

## `apply_human_input`

```python
m3.managed_input_api.apply_human_input(
    spec: object,
    value: object = 'fail',
) -> object
```

## `command_human_input`

```python
m3.managed_input_api.command_human_input(
    payload: Mapping[str, object],
) -> HumanInput
```

Read the durable policy, preserving fail as the old-envelope default.

## `managed_input_provider`

```python
m3.managed_input_api.managed_input_provider(
    store: object,
) -> ManagedInputProvider
```

## `managed_store`

```python
m3.managed_input_api.managed_store(
    store: object,
) -> ManagedInputStore
```

## `pending_elicitation`

```python
m3.managed_input_api.pending_elicitation(
    store: object,
    execution_id: object,
) -> PendingElicitationRound | None
```

## `respond_elicitation`

```python
m3.managed_input_api.respond_elicitation(
    store: object,
    execution_id: object,
    round_id: str,
    responses: Mapping[str, ElicitationResponse],
    *,
    idempotency_key: str,
) -> None
```

## `validate_human_input`

```python
m3.managed_input_api.validate_human_input(
    value: object,
) -> HumanInput
```
