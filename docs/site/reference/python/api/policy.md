---
title: "m3.policy"
description: "Public Python API reference for m3.policy."
---

# `m3.policy`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `ConfirmationHook`

```python
m3.policy.ConfirmationHook(
    *args,
    **kwargs,
)
```

## `ToolDescriptor`

```python
m3.policy.ToolDescriptor(
    *,
    server: str,
    name: str,
    destructive: bool = False,
) -> None
```

A qualified advertised tool identity used during preflight.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `server` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `name` | `str` | Yes | — | `min_length=1, max_length=256` | — |
| `destructive` | `bool` | No | `False` | — | — |
- `qualified_name` (property)

## `ToolPolicyDecision`

```python
m3.policy.ToolPolicyDecision(
    *,
    allowed: bool,
    requires_confirmation: bool = False,
    reason: str,
    evidence: m3.policy.ToolPolicyEvidence,
) -> None
```

Decision for one qualified tool call.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `allowed` | `bool` | Yes | — | — | — |
| `requires_confirmation` | `bool` | No | `False` | — | — |
| `reason` | `str` | Yes | — | — | — |
| `evidence` | `m3.policy.ToolPolicyEvidence` | Yes | — | — | — |

## `ToolPolicyEvidence`

```python
m3.policy.ToolPolicyEvidence(
    *,
    requested: str,
    enforced: str | None = None,
    observed: str | None = None,
    unavailable: tuple[str, ...] = (),
    portable: bool = True,
    nonportable_reason: str | None = None,
) -> None
```

Truthful requested/enforced/observed policy status.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `requested` | `str` | Yes | — | — | — |
| `enforced` | `str \| None` | No | `None` | — | — |
| `observed` | `str \| None` | No | `None` | — | — |
| `unavailable` | `tuple[str, ...]` | No | `()` | — | — |
| `portable` | `bool` | No | `True` | — | — |
| `nonportable_reason` | `str \| None` | No | `None` | — | — |

## `ToolPolicyEvaluator`

```python
m3.policy.ToolPolicyEvaluator(
    tools: _Iterable[ToolDescriptor],
) -> None
```

Evaluate portable and explicitly native tool policies.

- `tools` (property)

```python
preflight(
    self,
    policy: _ToolPolicy,
    *,
    harness_name: str,
    supports_enforcement: bool,
) -> ToolPolicyEvidence
```

```python
decide(
    self,
    policy: _ToolPolicy,
    descriptor: ToolDescriptor,
    *,
    harness_name: str,
    supports_enforcement: bool,
    confirm: ConfirmationHook | None = None,
) -> ToolPolicyDecision
```

## `evaluate_tool_policy`

```python
m3.policy.evaluate_tool_policy(
    policy: _ToolPolicy,
    tools: _Iterable[ToolDescriptor],
    *,
    harness_name: str,
    supports_enforcement: bool,
) -> ToolPolicyEvidence
```

Run policy preflight and return only truthful usage evidence.
