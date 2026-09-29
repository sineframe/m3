---
title: "Test, execution, and evaluation outcomes"
description: "M3 keeps three judgments separate:"
---

# Test, execution, and evaluation outcomes

M3 keeps three judgments separate:

| Judgment | What it answers |
| --- | --- |
| Pytest outcome | Did the Python test and its fixtures pass? |
| Execution outcome | Did the MCP or agent execution complete, fail, time out, or get cancelled? |
| Evaluation result | Did one named evaluator accept the supplied subject? |

A completed execution does not prove that its response is correct. A failed
matcher normally fails pytest and can also be recorded as evaluation evidence
when the M3 plugin is active. An LLM judge can error even when the underlying
execution completed.

When reporting a pass rate, state which evaluation defines its numerator and
denominator. [Aggregate evaluations](../guides/evaluations/aggregate.md) shows how
required, missing, and error results affect the count.
