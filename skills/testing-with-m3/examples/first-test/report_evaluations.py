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
