# Explicit evaluations

## Response judges

`m3 setup` includes judge support. If installing the SDK directly, include
the `judge` extra (for example, `m3[pytest,judge]`). Put the judge key in `.env`
as `M3_JUDGE_API_KEY`. For an OpenCode agent, the same file can also hold
`OPENCODE_API_KEY`:

```dotenv
OPENCODE_API_KEY=agent-secret
M3_JUDGE_API_KEY=judge-secret
```

Load the file when running tests:

```sh
m3 test --env-file .env -- tests/test_answer.py
```

Use the judge in a test:

```python
from m3.judges import LLMJudge

judge = LLMJudge(model="your-judge-model")
result = m3_kit.judge_response(
    name="answer.correctness.v1", input=prompt,
    actual=turn.response.text if turn.response else "", expected="The answer is 5.",
    judge=judge, execution_id=session.result.snapshot.execution_id,
    turn_id=turn.turn_id, required=True,
)
```

`.env` is loaded only when passed with `--env-file`. Missing credentials,
malformed subjects, and provider failures are persisted as safe `ERROR`
evaluations. The async kit provides `await judge_response(...)` as well.

For advanced subjects, register the same callback with a stable evaluator name:

```python
judge = LLMJudge(model="your-judge-model")
m3_kit.register_evaluator("answer.correctness.v1", judge)
m3_kit.evaluate({"input": prompt, "expected": reference, "actual": answer},
                "answer.correctness.v1")
```

The judge sends selected test text to its configured endpoint, so avoid
including private data unless that transfer is intended.

Judges use the OpenAI Chat Completions API. The default endpoint uses
`M3_JUDGE_API_KEY` and defaults to `json_schema`. A custom endpoint must declare
both its credential environment variable and response mode:

```python
custom = LLMJudge(
    model="vendor-chat-model",
    base_url="https://judge.example.test/v1",
    api_key_env="M3_JUDGE_API_KEY",
    response_mode="json_text",  # or "json_schema"
)
```

For a local HTTP fixture, anonymous loopback access is allowed with no key or
`Authorization` header and reads no API key:

```python
local = LLMJudge(
    model="fixture-chat-model",
    base_url="http://127.0.0.1:8123/v1",
    auth="none",
    response_mode="json_text",
)
```

The selected model and endpoint must support the chosen Chat Completions
response mode.

Evaluations are explicit callbacks over a redacted execution subject. They run
when the test calls `kit.evaluate`; they are not inferred from an execution
specification. Provider setup is described in the [quick start](quick-start.md#response-judge).

```python
import pytest
from m3.evaluations import EvaluationDecision
from m3.types import EvaluationStatus


def answer_and_tool(context):
    subject = context.subject
    answer = str(subject.get("answer", ""))
    used_tools = subject.get("used_tools", ())
    answer_ok = "5" in answer
    tool_ok = "add_tool" in used_tools
    passed = answer_ok and tool_ok
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
        score=1.0 if passed else 0.0,
        rationale=f"answer_ok={answer_ok}; tool_ok={tool_ok}",
        metrics={"answer_ok": float(answer_ok), "tool_ok": float(tool_ok)},
    )


@pytest.mark.m3
def test_answer_and_tool(agent, m3_kit, math_server):
    m3_kit.register_evaluator("project.answer-and-tool.v1", answer_and_tool)
    with agent.session(server=math_server) as session:
        turn = session.send("What is 2 + 3? Use the math tool.")
    result = session.result
    subject = {
        "answer": turn.response.text if turn.response else "",
        "used_tools": [call.tool.value for call in result.trace_view.tool_calls],
    }
    m3_kit.evaluate(
        subject,
        "project.answer-and-tool.v1",
        execution_id=result.snapshot.execution_id,
        turn_id=turn.turn_id,
        trace=result.trace,
    )
```

The callback checks both the answer and captured tool evidence. A callback can
also be async. Built-in evaluators include
`m3.execution.completed.v1`, `m3.tool_call.succeeded.v1`, and
`m3.output.has_text.v1`.

Use `MCPTestKit(store=SQLiteExecutionStore(path))` to persist evaluations in a
plain Python program. Pytest runs persist them with
`--results-db PATH`. The CLI also writes feedback to
`.m3/reports/<run-id>/feedback.json`; use `--baseline RUN_ID` for a
comparison with an earlier run.

## Evaluate repeated agent trials

Keep the math server deterministic and vary the cases with ordinary pytest
parameters. The complete executable example is
[`test_math_harness_matrix.py`](../examples/nondeterministic/test_math_harness_matrix.py),
which contains ten logical cases and evaluates both answer and tool evidence.
The linked file runs two trials for each selected OpenCode configuration; the
command below shows two selections, producing 40 executions. Its local server is
[`math_mcp_server.py`](../examples/servers/math_mcp_server.py).
The marked test can use the public agent API:

```python
import pytest
from m3.evaluations import EvaluationDecision
from m3.types import EvaluationStatus
from m3 import expect

CASES = [
    ("add", "What is 2 + 3? Use the math tool.", 5, "add_tool"),
    ("multiply", "What is 6 * 7? Use the math tool.", 42, "multiply_tool"),
]


def evaluate_math(context):
    subject = context.subject
    answer_ok = str(subject["expected"]) in subject["answer"]
    tool_ok = subject["expected_tool"] in subject["used_tools"]
    passed = answer_ok and tool_ok
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
        score=1.0 if passed else 0.0,
        rationale=f"answer_ok={answer_ok}; tool_ok={tool_ok}",
    )


@pytest.mark.m3
@pytest.mark.parametrize("case_id,prompt,expected,expected_tool", CASES)
def test_math_agent(agent, m3_kit, math_server, case_id, prompt, expected, expected_tool):
    m3_kit.register_evaluator("example.math.v1", evaluate_math)
    with agent.session(server=math_server, case_id=f"math-{case_id}") as session:
        turn = session.send(prompt)
    result = session.result
    expect(result).to_have_tool_call(expected_tool, status="success")
    m3_kit.evaluate(
        {
            "answer": turn.response.text if turn.response else "",
            "expected": expected,
            "expected_tool": expected_tool,
            "used_tools": [call.tool.value for call in result.trace_view.tool_calls],
        },
        "example.math.v1",
        execution_id=result.snapshot.execution_id,
        turn_id=turn.turn_id,
        trace=result.trace,
    )
```

Run two independent trials of every case for each selected configuration. Follow the
[quick-start credential setup](quick-start.md#agent-behavior-tests) for process
environment or an explicit `.env` file; only variable names appear in this command:

```bash
m3 test --env-file .env \
  --harness opencode=opencode/big-pickle \
  --harness codex=gpt-5.6-sol --trials 2 -- tests/test_math_agent.py
```

Ten cases × two harness/model selections × two trials creates **40
independently scored executions**. Every trial remains in history, including
failures. A plain Python loop can use `kit.agents([...], trials=2)` and pass the
same `case_id` to each selected agent.

### Calculate the final score

Filter by both evaluator and run so older rows in the same database do not enter
the result. Group by the selected configuration:

```python
from m3.aggregations import EvaluationQuery

report = store.aggregate_evaluations(EvaluationQuery(
    filters={
        "evaluator": ("example.math.v1",),
        "run_id": (run_id,),
    },
    group_by=("metadata.harness_config",),
))
print(report.totals.pass_rate)
for group in report.groups:
    print(group.key, group.values.pass_rate, group.values.status_counts)
```

Pass rate is `passed / (passed + failed)`. Error, inconclusive, and not-run
results remain visible in `status_counts` but are excluded from that
denominator. The denominator counts measured trial decisions, so ten logical
cases across two configurations and two trials contribute 40 possible measured
results. The aggregate also reports score counts, average score, and health
summaries.

### Run the live example explicitly

The full live math example is opt-in and may use provider resources:

```bash
M3_RUN_LIVE_MATH_MATRIX=1 \
  uv run --project sdk --all-extras pytest -s -q \
  sdk/examples/nondeterministic/test_math_harness_matrix.py
```

Without the opt-in variable the example is skipped. The normal OpenCode live
selection is documented in [`test_live_agent_selection.py`](../examples/nondeterministic/test_live_agent_selection.py).

### Group by suite

Filter and group evaluation outcomes by suite with the `suite_name` label. Keep
an evaluator filter so unrelated evaluators do not share a denominator:

```python
report = store.aggregate_evaluations(EvaluationQuery(
    filters={"suite_name": ("catalog",), "evaluator": ("quality.v1",)},
    group_by=("time.day",),
))
```

To compare all suites, group without a suite filter:

```python
report = store.aggregate_evaluations(EvaluationQuery(
    filters={"evaluator": ("quality.v1",)},
    group_by=("suite_name",),
))
```

These pass rates describe evaluator outcomes. Pytest attempt outcomes remain in
test run records and are not included in evaluation rates.
