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

The default threshold is `0.8`. Abstention, refusal, malformed output, and
provider failure produce an error result. `required=True` persists the result
before enforcing the required-evaluation policy.
