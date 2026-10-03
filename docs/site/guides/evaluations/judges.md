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
extra. Set `M3_JUDGE_API_KEY` in the process environment or in `.env` at the
project root, which M3 loads automatically. Use `--env-file PATH` to load a
custom file instead.

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
m3 test --judge-max-requests 1 -- tests/test_answer.py
```

## Map a differently named judge key

If your environment or project root `.env` file stores the judge key as `MY_JUDGE_KEY`, keep `tests/test_answer.py` unchanged and replace the command above with:

```sh
m3 test \
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

## Audit a verdict from the stored evidence

Every `LLMJudge` result stores a judge evidence bundle next to the score and
rationale, so a failed verdict can be audited without re-running the test:

| Field | Meaning |
| --- | --- |
| `input` | The question or task the answer responds to. |
| `reference` | The reference answer (`expected`). |
| `candidate` | The answer that was judged (`actual`). |
| `rubric` | The rubric text, when the judge has one. |
| `threshold` | The pass boundary: a score at or above it passes. |
| `rubric_digest`, `config_digest` | SHA-256 digests that identify the rubric and the full judge configuration. |
| `truncated` | Names of fields shortened to fit the size bound. |

The bundle is also recorded when the judge returns an error, for example when
the credential is missing. The hosted viewer shows it beside the score and
rationale. Runs recorded before the bundle existed show "not captured".

The bundle is stored in the local run store and uploaded with the run, so it
is not a digest of the subject: it is the subject. It goes through the same
redaction as other persisted evaluation data: configured secret values and
credential-shaped fields become `[REDACTED]` before anything is written or
uploaded. Each text field is capped at 32 KiB, and claims at 64 entries of
2 KiB. `subject_digest` on the evaluation record is unchanged.

Avoid putting private data in judge subjects unless that data may be stored and
uploaded; the judge already sends the same text to its endpoint.
