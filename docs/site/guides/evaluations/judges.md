---
title: "Evaluate a response with an LLM judge"
description: "An LLM judge sends the selected subject text to its configured endpoint. Use it for criteria that cannot be expressed as deterministic assertions, and avoid sending private data unless that transfer is intended."
---

# Evaluate a response with an LLM judge

An LLM judge sends the selected subject text to its configured endpoint. Use it
for criteria that cannot be expressed as deterministic assertions, and avoid
sending private data unless that transfer is intended.

## Requirements

`m3 setup` installs judge support. Direct SDK installations need the `judge`
extra. Set `M3_JUDGE_API_KEY` in the process environment or an explicitly
selected environment file. M3 does not discover `.env` automatically.

Register a judge with `kit.register_evaluator(name, judge)` and use the subject
`{"input", "expected", "actual"}`.

Choose a judge model supported by your configured Chat Completions endpoint:

```python
import pytest

from m3.judges import LLMJudge
from m3.types import EvaluationStatus


@pytest.mark.m3(suite_name="answers")
def test_answer(m3_kit):
    judge = LLMJudge(model="YOUR_JUDGE_MODEL")
    result = m3_kit.judge_response(name="answer.correctness.v1", input="What is 2 + 3?",
        actual="The answer is 5.",
        expected="The answer is 5.",
        judge=judge,
        required=True,
    )
    assert result.status is EvaluationStatus.PASSED
```

Replace `YOUR_JUDGE_MODEL`, then run with a request cap:

```sh
m3 test --env-file .env --judge-max-requests 1 -- tests/test_answer.py
```

## Map a differently named judge key

If your environment or explicitly selected `.env` file stores the judge key as `MY_JUDGE_KEY`, keep `tests/test_answer.py` unchanged and replace the command above with:

```sh
m3 test --env-file .env \
  --credential-env judge:M3_JUDGE_API_KEY=MY_JUDGE_KEY \
  --judge-max-requests 1 -- tests/test_answer.py
```

The mapping reads `MY_JUDGE_KEY` and supplies `M3_JUDGE_API_KEY` for the judge. The `judge:` scope does not configure an agent credential. Keep upload credentials separate; `M3_ACCESS_TOKEN` is rejected as either mapping name. See the [credential reference](../../reference/credentials.md) for custom judge endpoints and authentication restrictions.

The default threshold is `0.8`. Abstention, refusal, malformed output, and
provider failure produce an error result. `required=True` persists the result
before enforcing the required-evaluation policy.

The judge receives JSON strings for input, expected, and actual, plus an
optional rubric. It returns exactly `score` (0–1 or null), `rationale`, and
`abstain`. `json_schema` mode, the default, is enforced by the provider;
`json_text` is validated locally. A score at or above the threshold maps to
`PASSED`; a lower score maps to `FAILED`.
