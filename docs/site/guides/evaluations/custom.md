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
