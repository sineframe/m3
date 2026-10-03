<!-- Generated from docs/site/concepts/outcomes.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Test, execution, and evaluation outcomes

M3 keeps three judgments separate:

| Judgment | What it answers |
| --- | --- |
| Pytest outcome | Did the Python test and its fixtures pass? |
| Execution outcome | Did the MCP or agent execution complete, fail, time out, get cancelled, or get interrupted? |
| Evaluation result | Did one named evaluator accept the supplied subject? |
A plain pytest assertion without an M3 client or agent operation records no
execution.

## Pytest outcome values

A test's `outcome`, `verdict`, and `effective_verdict` in the feedback bundle
can be `xfailed`. It means pytest ran a test marked `@pytest.mark.xfail`
(bare or with a reason) and it failed as expected. `skipped` means the test did
not run. An xfailed test is not a failure and does not need attention, and its
reason is saved as `xfail_reason`.

## Execution outcome values

A finished execution records exactly one `ExecutionOutcome`, saved as
`executions[].outcome` in the feedback bundle. Agent turns use `TurnOutcome`,
which has the same five values.

| Value | When it occurs |
| --- | --- |
| `completed` | The operation or agent session finished without an error. |
| `failed` | The operation ended with an error that is not a timeout or cancellation, including a failed harness turn. |
| `timed_out` | A client operation timeout, or an agent turn or session timeout, expired. |
| `cancelled` | The operation or agent session was cancelled, whether by an explicit cancellation request or by task cancellation. |
| `interrupted` | A SQLite-backed worker owned the execution but stopped renewing its lease, for example because it crashed or its heartbeat failed, so the store closed the execution. No one requested a stop and the operation did not report its own error. |

`interrupted` differs from `cancelled`: cancellation is a requested stop,
while an interrupted execution is one whose worker disappeared. Matching
`completed` describes lifecycle only; it does not say the response was
correct.

A completed execution does not prove that its response is correct. A failed
matcher normally fails pytest and can also be recorded as evaluation evidence
when the M3 plugin is active. An LLM judge can error even when the underlying
execution completed.

When reporting a pass rate, state which evaluation defines its numerator and
denominator. [Aggregate evaluations](guides-evaluations-aggregate.md) shows how
required, missing, and error results affect the count.
