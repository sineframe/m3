---
title: "m3.evaluations"
description: "Public Python API reference for m3.evaluations."
---

# `m3.evaluations`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `AsyncEvaluator`

```python
m3.evaluations.AsyncEvaluator(
    *args,
    **kwargs,
)
```

## `EvaluationDecision`

```python
m3.evaluations.EvaluationDecision(
    *,
    status: m3.types.EvaluationStatus,
    score: float | None = None,
    rationale: str | None = None,
    metrics: collections.abc.Mapping[str, float] = ...,
    provenance: m3.types.EvaluationSource | None = None,
    details: collections.abc.Mapping[str, Any] = ...,
    judge_evidence: m3.types.JudgeEvidence | None = None,
) -> None
```

Structured evaluator output, compatible with scalar verdicts.

Model fields:

| Field | Type | Required | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `status` | `m3.types.EvaluationStatus` | Yes | — | — | — |
| `score` | `float \| None` | No | `None` | — | — |
| `rationale` | `str \| None` | No | `None` | — | — |
| `metrics` | `collections.abc.Mapping[str, float]` | No | `factory builtins.dict()` | — | — |
| `provenance` | `m3.types.EvaluationSource \| None` | No | `None` | — | — |
| `details` | `collections.abc.Mapping[str, Any]` | No | `factory builtins.dict()` | — | — |
| `judge_evidence` | `m3.types.JudgeEvidence \| None` | No | `None` | — | — |

## `EvaluationRunner`

```python
m3.evaluations.EvaluationRunner(
    *,
    registry: EvaluatorRegistry | None = None,
    store: EvaluationStore | None = None,
    durable_store: _Any | None = None,
    redaction_config: _RedactionConfig | None = None,
    max_judge_requests: int | None = None,
    run_id: str | None = None,
) -> None
```

Run and persist deterministic evaluations in a separate store.


```python
register(
    self,
    name: str,
    evaluator: EvaluatorCallable | AsyncEvaluator,
) -> EvaluatorRegistration
```

```python
evaluate(
    self,
    subject: _Any,
    evaluator: str | EvaluatorCallable,
    *,
    required: bool = False,
    goal: str | None = None,
    trace: _TraceResult | None = None,
    artifacts: _Sequence[_ArtifactRef] = (),
    metadata: _Mapping[str, str | int | float | bool | None] | None = None,
    evaluation_id: _EvaluationId | str | None = None,
    execution_id: _ExecutionId | str | None = None,
    turn_id: _TurnId | str | None = None,
    case_id: str | None = None,
) -> _EvaluationResult
```

```python
evaluate_async(
    self,
    subject: _Any,
    evaluator: str | _Any,
    *,
    required: bool = False,
    goal: str | None = None,
    trace: _TraceResult | None = None,
    artifacts: _Sequence[_ArtifactRef] = (),
    metadata: _Mapping[str, str | int | float | bool | None] | None = None,
    evaluation_id: _EvaluationId | str | None = None,
    execution_id: _ExecutionId | str | None = None,
    turn_id: _TurnId | str | None = None,
    case_id: str | None = None,
) -> _EvaluationResult
```

```python
results(
    self,
) -> tuple[_EvaluationResult, ...]
```

## `EvaluationStore`

```python
m3.evaluations.EvaluationStore(
    *args,
    **kwargs,
)
```


```python
save(
    self,
    result: _EvaluationResult,
) -> None
```

```python
get(
    self,
    evaluation_id: _EvaluationId | str,
) -> _EvaluationResult | None
```

```python
all(
    self,
) -> tuple[_EvaluationResult, ...]
```

## `EvaluationVerdict`

`m3.evaluations.EvaluationVerdict`

## `Evaluator`

```python
m3.evaluations.Evaluator(
    *args,
    **kwargs,
)
```

An evaluator callback over an immutable context.

## `EvaluatorCallable`

```python
m3.evaluations.EvaluatorCallable(
    *args,
    **kwargs,
)
```

## `EvaluatorRegistry`

```python
m3.evaluations.EvaluatorRegistry(
) -> None
```

Explicit runtime registry for evaluator callables.


```python
register(
    self,
    name: str,
    evaluator: _Any,
) -> EvaluatorRegistration
```

```python
get(
    self,
    name: str,
) -> _Any
```

```python
names(
    self,
) -> tuple[str, ...]
```

## `EvaluatorRegistration`

```python
m3.evaluations.EvaluatorRegistration(
    name: str,
    evaluator: EvaluatorCallable | AsyncEvaluator,
) -> None
```

Runtime-only registration; only its stable name is serializable.

## `InMemoryEvaluationStore`

```python
m3.evaluations.InMemoryEvaluationStore(
) -> None
```

Small independent store; evaluations never rewrite execution state.


```python
save(
    self,
    result: _EvaluationResult,
) -> None
```

```python
get(
    self,
    evaluation_id: _EvaluationId | str,
) -> _EvaluationResult | None
```

```python
all(
    self,
) -> tuple[_EvaluationResult, ...]
```

## `RequiredEvaluationError`

```python
m3.evaluations.RequiredEvaluationError(
    result: _EvaluationResult,
) -> None
```

A required non-passing evaluation after its result was persisted.

## `register_builtin_evaluators`

```python
m3.evaluations.register_builtin_evaluators(
    registry: EvaluatorRegistry,
) -> None
```

Install the reserved deterministic evaluators on a runtime registry.
