# Explicit evaluations

Evaluations are explicit runtime callbacks over a redacted subject. They are
not automatically run from `ExecutionSpec.evaluations`.

```python
from mcp_pal import EvaluationDecision, EvaluationStatus, MCPTestKit

def answer_quality(context):
    blocks = context.subject["direct_result"].get("content", ())
    text = "".join(
        block.get("text", "")
        for block in blocks
        if block.get("type") == "text"
    )
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if text.strip() else EvaluationStatus.FAILED,
        score=1.0 if text.strip() else 0.0,
        rationale="The direct result contains text.",
    )

with MCPTestKit() as kit:
    kit.register_evaluator("project.answer-quality.v1", answer_quality)
    execution = kit.run(spec)
    decision = kit.evaluate(execution, "project.answer-quality.v1")
```

The built-ins `mcp_pal.execution.completed.v1`,
`mcp_pal.tool_call.succeeded.v1`, and `mcp_pal.output.has_text.v1` are
available without registration.

MCP Pal does not provide a built-in LLM judge. If needed, an SDK user can
write a sync or async evaluator callback that calls an LLM and returns an
`EvaluationDecision` with redacted source details. There is no separate
post-evaluator layer. Chained direct calls share one client execution, while
agent evaluations may attach to an individual `turn_id`. Matrix trials carry
matrix, cell, and trial metadata for later comparison.

Use `MCPTestKit(store=SQLiteExecutionStore(path))` to save attached
evaluations, or run pytest with `--mcp-pal-results-db PATH` (the plugin's
default store). Reopen the SQLite store and query
`store.evaluations(execution_id, turn_id=...)`. Without an explicit SQLite
store, SDK execution and evaluation data are in memory only. Pytest item
verdicts and summary rows are not persisted; use the store query below to
calculate trends from saved raw rows.

The execution request accepts optional `run_id` and `case_id` through
`ExecutionSpec`. A case is the logical thing being tested; each execution is
one trial. Matrix helpers fill the case from matrix and cell, so repeated trials
share a case while keeping separate trial IDs. `ExecutionSpec.evaluations` is
saved as metadata; it is not run.

```python
from mcp_pal import EvaluationQuery

report = store.aggregate_evaluations(EvaluationQuery(
    group_by=("time.day", "evaluator"),
    filters={"evaluator": "project.answer-quality.v1"},
))
print(report.groups[0].values.pass_rate)
```

Use `run_id` for one point per run, `time.day` or `time.week` for calendar
charts, `case_id` for matrix-cell comparison, and `trial_id` for trace links.
The result also includes status counts, score averages, tool-call health, and
latency summaries. Pass rate is `passed / (passed + failed)`; inconclusive,
error, and not-run results are visible but are not in that denominator.
Summaries are calculated on request, not stored as extra rows.

An SDK user-supplied LLM evaluator is another evaluator. The user's callback
calls the LLM and returns `EvaluationDecision`; query its evaluator and judge
labels separately from deterministic evaluators. API v2 does not register or
run callbacks. It only reads evaluations and provenance that the SDK saved to
the same SQLite database.
