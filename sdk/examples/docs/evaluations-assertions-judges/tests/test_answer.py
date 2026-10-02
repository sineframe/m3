import pytest

from m3.judges import LLMJudge
from m3.types import EvaluationStatus


@pytest.mark.m3(suite_name="answers")
def test_answer(m3_kit):
    judge = LLMJudge(model="YOUR_JUDGE_MODEL")
    result = m3_kit.judge_response(
        name="answer.correctness.v1",
        input="What is 2 + 3?",
        actual="The answer is 5.",
        expected="The answer is 5.",
        judge=judge,
        required=True,
    )
    assert result.status is EvaluationStatus.PASSED
