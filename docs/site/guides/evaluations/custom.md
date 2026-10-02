---
title: "Write a custom evaluator"
description: "A custom evaluator turns an explicit subject into a named evaluation result. It does not run automatically because an execution completed."
---

# Write a custom evaluator

A custom evaluator turns an explicit subject into a named evaluation result.
It does not run automatically because an execution completed.

```python
import sys
from pathlib import Path

import pytest

from m3 import StdioServer
from m3.evaluations import EvaluationDecision
from m3.types import EvaluationStatus


def shipping_evaluator(context):
    amount = context.subject.get("amount")
    passed = amount == 9.0
    return EvaluationDecision(
        status=(EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED),
        score=1.0 if passed else 0.0,
        rationale=f"amount={amount!r}",
    )


@pytest.mark.m3(suite_name="shipping")
def test_shipping_evaluation(m3_kit):
    root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(root / "shipping_server.py"),),
        cwd=str(root),
    )
    m3_kit.register_evaluator("shipping.local-price.v1", shipping_evaluator)
    with m3_kit.direct(server) as client:
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})
    evaluation = m3_kit.evaluate(
        result.structured_content,
        "shipping.local-price.v1",
        execution_id=client.final_trace.execution_id,
    )
    assert evaluation.status is EvaluationStatus.PASSED
```

Run this in the first-test project:

```sh
m3 test -- tests/test_shipping_evaluation.py
```

Use a stable, versioned evaluator name. `required=True` makes a failed, error,
inconclusive, or missing required result block the run according to the
required-evaluation policy.

`required=False` records non-passing decisions without failing pytest.
`required=True` persists the result, then raises for failed, error,
inconclusive, or not-run decisions. Session finalization still blocks the run
if test code catches that exception.

An evaluator may return a bool: `True` maps to `PASSED` and `False` to
`FAILED`. For an agent result, use
`execution_id=result.snapshot.execution_id`.

Register the evaluator on the same kit. Close the client, then evaluate while
the kit is still open. A configured `LLMJudge` can be called inside a sync or
async evaluator. Chained direct calls share one client execution. Agent
evaluations can use `turn_id`. Matrix trials carry matrix, cell, and trial
metadata.

A user-LLM evaluator returns
`EvaluationDecision(provenance=EvaluationSource(kind="user_llm", ...))`.
SQLite persists the result and provenance, not the client, key, prompt, or
hidden LLM state.
