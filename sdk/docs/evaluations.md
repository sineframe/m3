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
store, SDK execution and evaluation data are in memory only. When the MCP Pal
pytest plugin is active, pytest item verdicts are persisted in internal run
records and MCP Pal matcher checks are persisted as execution evaluations;
ordinary printed output remains diagnostics.
Use the store query below to calculate trends from saved raw rows.

`mcp-pal test` also writes `.mcp-pal/reports/<run-id>/feedback.json`. Pass
`--baseline RUN_ID` on a later run to include a deterministic comparison of the
observed interface, executions, and saved evaluations. The feedback endpoint
and CLI consume the same SDK projection.

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

## Evaluate a harness matrix over repeated trials

Use this pattern when the question is not only whether an MCP tool works, but
how reliably an agent harness chooses it and returns a useful answer. Keep the
MCP server deterministic, run each logical case as several independent trials,
evaluate each trial, and aggregate the saved decisions afterward.

The complete executable example is
[`test_math_harness_matrix.py`](../examples/nondeterministic/test_math_harness_matrix.py).
It runs ten logical math cases through OpenCode, keeps a commented Claude Code
configuration ready for environments that have it, and evaluates two trials
per case. Its local server is
[`math_mcp_server.py`](../examples/servers/math_mcp_server.py).

The math domain is intentionally simple. It makes two independent claims easy
to see: the answer contains the expected value, and finalized wire evidence
shows that the agent selected the expected tool. A trial may satisfy one claim
without satisfying the other.

### Define typed logical cases and harnesses

The executable example contains ten cases; a shortened definition looks like
this:

```python
from dataclasses import dataclass

from mcp_pal import HarnessCase, OpenCode

@dataclass(frozen=True, slots=True)
class MathTestCase:
    id: str
    prompt: str
    expected_value: int
    expected_tool: str

cases = (
    MathTestCase("add", "What is 2 + 2?", 4, "add_tool"),
    MathTestCase("multiply", "What is 6 * 7?", 42, "multiply_tool"),
)

harnesses = (
    HarnessCase(
        name="opencode",
        harness=OpenCode(
            model="opencode/big-pickle",
            executable="opencode",
        ),
    ),
)
```

Add more `HarnessCase` values to compare providers or models. Use distinct
harness-case names, and group by that name through evaluation metadata when
several configurations use the same underlying harness.

### Return one decision for each trial

Register one stable evaluator name and invoke it once for every execution. The
same callback can read case-specific expectations from its subject:

```python
from mcp_pal import EvaluationContext, EvaluationDecision, EvaluationStatus

def evaluate_math_case(context: EvaluationContext) -> EvaluationDecision:
    subject = context.subject
    answer_ok = str(subject["expected_value"]) in subject["answer"]
    tool_ok = subject["expected_tool"] in subject["used_tools"]
    passed = answer_ok and tool_ok
    return EvaluationDecision(
        status=(
            EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED
        ),
        score=1.0 if passed else 0.0,
        rationale=f"answer_ok={answer_ok}; tool_ok={tool_ok}",
        metrics={
            "answer_ok": float(answer_ok),
            "tool_ok": float(tool_ok),
        },
    )
```

The full example uses a number-boundary matcher and validates the evaluator
subject before reading it. Those details keep the example executable without
hiding the core decision rule.

### Repeat every case across harnesses

Build one matrix per logical case. `trials=2` creates two independent agent
executions for every harness in that matrix:

```python
matrix = HarnessMatrix.each_server(
    id=f"math-{test_case.id}",
    servers=(server_case,),
    harnesses=harnesses,
    trials=2,
)

for matrix_case in matrix.cases():
    with matrix_case.session(
        kit=kit,
        tool_policy=FullToolPolicy(acknowledge_risk=True),
    ) as session:
        turn = session.send(test_case.prompt, timeout=120)

    execution = session.result
    used_tools = tuple(
        call.tool.value
        for call in execution.trace_view.tool_calls
        if call.tool.value is not None
    )
    kit.evaluate(
        {
            "answer": turn.response.text if turn.response else "",
            "expected_value": test_case.expected_value,
            "expected_tool": test_case.expected_tool,
            "used_tools": used_tools,
        },
        "example.math-answer-and-tool.v1",
        execution_id=execution.snapshot.execution_id,
        turn_id=turn.turn_id,
        trace=execution.trace,
        metadata={"harness_config": matrix_case.harness.name},
    )
```

`FullToolPolicy` is deliberate here: the test binds only a dedicated safe math
server and asks which of its advertised tools the model selects. In a broader
environment, prefer `RestrictiveToolPolicy` with an explicit safe allowlist.
An empty restrictive allowlist denies every tool.

Use `session()` when the evaluator needs the completed `TurnResult`; after the
context closes, `session.result` provides the finalized execution and trace.
Do not infer tool usage from the assistant's prose.

### Calculate the final score

Filter by both evaluator and run so old records in the same database cannot
enter the score. Group by the harness-case label to compare configurations:

```python
from mcp_pal.aggregations import EvaluationQuery

report = store.aggregate_evaluations(EvaluationQuery(
    filters={
        "evaluator": ("example.math-answer-and-tool.v1",),
        "run_id": (kit_run_id,),
    },
    group_by=("metadata.harness_config",),
))

print(report.totals.pass_rate)
for group in report.groups:
    print(group.key, group.values.pass_rate)
```

Pass rate is calculated across measured trials, not logical cases:

```text
passed / (passed + failed)
```

Errors, inconclusive decisions, and not-run decisions remain visible in status
counts but are excluded from that denominator. Repeated trials are retained;
this is not retry-until-success. Ten cases across two harnesses with two trials
produce forty independently scored executions.

### Run the live example explicitly

This command calls OpenCode and may incur provider usage:

```bash
MCP_PAL_RUN_LIVE_MATH_MATRIX=1 \
  uv run --project sdk --all-extras pytest -s -q \
  sdk/examples/nondeterministic/test_math_harness_matrix.py
```

Without the opt-in environment variable this example is skipped.
