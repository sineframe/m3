<!-- Generated from docs/site/guides/evaluations/custom.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

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

## Make a judge verdict auditable

A judge-style evaluator, whether it calls an LLM or scores a rubric by hand,
records what it judged by returning `JudgeEvidence` on its decision. The hosted
viewer then shows the threshold, score, rubric, reference and candidate answer,
and input together:

```python
import pytest

from m3.evaluations import EvaluationDecision
from m3.types import EvaluationStatus, JudgeEvidence

THRESHOLD = 0.8
CLAIMS = ("The ticket is open", "The owner is Dana")


def claims_judge(context):
    question = context.subject["question"]
    answer = context.subject["answer"]
    supported = sum(1 for claim in CLAIMS if claim.lower() in answer.lower())
    score = supported / len(CLAIMS)
    return EvaluationDecision(
        status=(
            EvaluationStatus.PASSED if score >= THRESHOLD else EvaluationStatus.FAILED
        ),
        score=score,
        rationale=f"{supported} of {len(CLAIMS)} claims supported.",
        judge_evidence=JudgeEvidence(
            input=question,
            claims=CLAIMS,
            candidate=answer,
            rubric="The answer must support every claim.",
            threshold=THRESHOLD,
        ),
    )


@pytest.mark.m3(suite_name="judge-evidence")
def test_claims_judge_records_its_evidence(m3_kit):
    m3_kit.register_evaluator("ticket.claims.v1", claims_judge)
    evaluation = m3_kit.evaluate(
        {"question": "What is ticket 41?", "answer": "The ticket is open."},
        "ticket.claims.v1",
    )
    assert evaluation.status is EvaluationStatus.FAILED
    assert evaluation.judge_evidence.threshold == THRESHOLD
    assert evaluation.judge_evidence.candidate == "The ticket is open."
```

Every field is optional; leave out what you do not have. `reference` is a
reference answer and `claims` are atomic statements the answer must satisfy.
`rubric_digest` is computed from `rubric` when you omit it; pass `config_digest`
to identify your judge's configuration. Evidence is redacted and size-bounded
before it is stored, exactly as for `LLMJudge` (see
[Evaluate a response with an LLM judge](guides-evaluations-judges.md#audit-a-verdict-from-the-stored-evidence)).
Return the same threshold your code compares against: M3 records it, but does
not apply it.
