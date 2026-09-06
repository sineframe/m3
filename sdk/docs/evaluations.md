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

An async LLM judge, when needed, belongs inside the evaluator callback and
should return an `EvaluationDecision` with redacted source details; there is no
separate post-evaluator layer. Chained direct calls share one client execution,
while agent evaluations may attach to an individual `turn_id`. Matrix trials
carry matrix, cell, and trial metadata for later comparison.

Use `MCPTestKit(store=SQLiteExecutionStore(path))` to save attached
evaluations, or run pytest with `--mcp-pal-results-db PATH` (the plugin's
default store). Reopen the SQLite store and query
`store.evaluations(execution_id, turn_id=...)`. Without an explicit SQLite
store, SDK execution and evaluation data are in memory only. Pytest item
verdicts, aggregate matrix/trial trends, and evaluator callbacks are not
persisted.

The execution request accepts an optional `run_id` through
`ExecutionSpec`. `ExecutionSpec.evaluations` is saved as metadata; it is not
run. An execution report includes saved turns and evaluations through
`PersistedExecutionReport`. There is not yet an API route to register or run
evaluator callbacks.
