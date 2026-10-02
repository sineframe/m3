---
title: "m3.judges"
description: "Public Python API reference for m3.judges."
---

# `m3.judges`

Signatures use `...` for factory-backed or opaque defaults. Model field
tables show required status, defaults, constraints, and descriptions.

## `PROMPT_VERSION`

`m3.judges.PROMPT_VERSION`

## `LLMJudge`

```python
m3.judges.LLMJudge(
    model: str,
    base_url: str | None = None,
    api_key_env: str | None = None,
    auth: str = 'env',
    response_mode: str | None = None,
    threshold: float = 0.8,
    rubric: str | None = None,
    rubric_id: str | None = None,
    rubric_version: str | None = None,
    timeout_seconds: float = 30.0,
    max_retries: Literal[0, 1] = 1,
) -> None
```

- `config_digest` (property)

```python
evaluate_async(
    self,
    context: EvaluationContext,
) -> EvaluationDecision
```
