---
title: "Aggregate saved evaluations"
description: "Aggregate records from one reader-created run so older rows in the same database do not alter the result."
---

# Aggregate saved evaluations

Aggregate records from one reader-created run so older rows in the same
database do not alter the result.

First run an evaluated test and copy the printed run ID:

```sh
m3 test -- tests/test_shipping_evaluation.py
printf 'Run ID: '
IFS= read -r RUN_ID
export RUN_ID
```

Save this as `report_evaluations.py`:

```python
import os

from m3.aggregations import EvaluationQuery
from m3.storage import SQLiteExecutionStore

run_id = os.environ["RUN_ID"]
store = SQLiteExecutionStore(".m3/executions.sqlite")
try:
    report = store.aggregate_evaluations(
        EvaluationQuery(
            filters={
                "run_id": (run_id,),
                "evaluator": ("shipping.local-price.v1",),
            },
            group_by=("suite_name",),
        )
    )
finally:
    store.close()
print(report.totals.pass_rate)
for group in report.groups:
    print(dict(group.key), group.values.pass_rate, dict(group.values.status_counts))
```

Export the ID captured from your run and execute the report:

```sh
uv run python report_evaluations.py
```

Pass rate uses passed evaluations over expected evaluations. Terminal missing
requirements and error-like statuses affect the denominator; a pending
requirement from a still-running attempt does not enter it yet. Pytest outcomes
remain separate.
