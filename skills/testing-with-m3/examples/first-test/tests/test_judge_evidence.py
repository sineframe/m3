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
