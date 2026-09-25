# Feedback and iteration

Use this reference when a task needs saved run history, trace diagnosis, repeated
trials, or evidence that a server edit helped. The standalone CLI already writes
a JSON bundle; do not print a whole trace or feedback file into the terminal.

## One run: find the verdict and evidence

From the target project root, run the same narrow selection used by the test:

```sh
m3 test --suite catalog -- tests/test_catalog.py
```

The output prints `M3 run RUN_ID` and
`M3 feedback: .m3/reports/RUN_ID/feedback.json`. Use that exact path; do not
guess the ID from a timestamp. The default SQLite database is
`.m3/executions.sqlite`. If you used `--results-db PATH`, keep using the same
path for later comparisons.

To browse saved test runs without executing a new test, run `m3 ui` from the
directory containing `.m3/executions.sqlite` and open the printed `/reports`
link. It refuses missing or invalid history instead of initializing a new
database. `m3 ui` does not accept `--results-db` or `--project-root`; an
alternate database used by `m3 test --results-db` is not selected by this
command. Use `m3 test --ui` only when a new test run is intended.

```sh
report=.m3/reports/RUN_ID/feedback.json
jq '{summary, limitations, evaluation_stats}' "$report"
jq '.tests[] | {node_id, outcome, verdict, tool_result, execution_ids}' "$report"
jq '.failures[] | {kind, node_id, verdict, evaluator, status, execution_id}' "$report"
```

For the first deterministic direct test, check the specific pytest node ID.
A green pytest result alone does not show that M3 ran a server operation:

```sh
node=tests/test_shipping.py::test_shipping_quote_contract
jq -e --arg node "$node" '
  (.summary.executions > 0) and
  any(.tests[];
    .node_id == $node and .outcome == "passed" and
    ((.execution_ids // []) | length > 0))
' "$report"
```

If this returns false, make the test invoke the direct client or an agent,
then rerun it. For a direct tool contract, inspect the linked trace to confirm
the expected tool call and result. A linked execution alone does not prove the
tool was called or that the assertion was useful.

`summary.failures` counts failed or errored pytest cases and collection errors.
`tests[].outcome` is pytest's case outcome; `tests[].verdict` distinguishes
assertion, protocol, setup, teardown, and other errors. A tool result can have
`is_error=True` even when the execution lifecycle is completed.
`executions[].outcome=completed` does not mean that a pytest assertion or
evaluator passed. `evaluation_stats` groups explicit saved evaluations by
name; a print or log line is not a score. `tests[].effective_verdict` applies
the required-evaluation policy without relabeling `tests[].outcome`; catching a
`required=True` exception does not make the final run successful. Review both
pytest outcomes and evaluation evidence.

The bundle maps execution IDs to supporting files. Read one referenced trace
rather than the whole bundle:

```sh
jq -r '.trace_files | to_entries[] | "\(.key)\t\(.value)"' "$report"
execution_id=EXECUTION_ID
trace_rel=$(jq -r --arg id "$execution_id" '.trace_files[$id] // empty' "$report")
bundle=${report%/feedback.json}
test -n "$trace_rel" && jq '
  {summary, limitations,
   tool_calls: [.timeline[] | select(.kind == "tool_call") |
     {server_binding, status, tool, arguments, result, provenance}],
   diagnostics: [.timeline[] | select(.kind == "diagnostic") |
     {code, status, stage, operation, elapsed_seconds, timeout_seconds,
      message, limitations}]}
' "$bundle/$trace_rel"
```

Other maps are `execution_files`, `spec_files`, `catalog_files`,
`diagnostic_files`, `test_run_files`, and `test_result_files`. Paths are
relative to the report's directory. A missing reference may be listed in
`unavailable_references`. Trace observations have a `state` and sometimes a
`reason`; inspect these before treating `value` as observed.

If `jq` is unavailable, read selected fields with Python:

```sh
python - "$report" <<'PY'
import json
import sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text())
for key in ("summary", "limitations", "evaluation_stats"):
    print(key, json.dumps(data.get(key), indent=2))
for case in data.get("tests", []):
    print({key: case.get(key) for key in ("node_id", "verdict", "execution_ids")})
PY
```

For SDK-only debugging, serialize the finalized public view to a local,
ignored file instead of stdout:

```python
import json
from pathlib import Path

path = Path(".m3/debug/trace.json")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(view.model_dump(mode="json"), indent=2))
```

The view may contain prompts, arguments, and results. Keep the file local and
read only fields relevant to the failure. `.m3/` should be ignored by Git.

## Compare a focused server edit

Before editing, establish a baseline with meaningful contract and selection
tests. Record the run ID and the selection, including pytest paths/filters,
`--suite`, harness/model, trial count, prompt cases, expected values, tool
policy, CLI/SDK release, evaluator names and versions, and judge model/rubric
where used. Pin the release for both sides of the comparison; a new prerelease
can appear between runs.

```sh
m3 test --suite catalog --harness codex=gpt-5.6-sol --trials 2 -- tests/test_catalog.py
```

Change one tool name, description, schema, or behavior at a time. For a
description experiment, keep the name, arguments, implementation, prompts,
policies, selected model, evaluator, and trial count fixed. Run the identical
selection against the same database:

```sh
m3 test --baseline BASELINE_RUN_ID --suite catalog \
  --harness codex=gpt-5.6-sol --trials 2 -- tests/test_catalog.py
```

The new run's `feedback.json` has a `comparison` object. Read it in slices:

```sh
report=.m3/reports/NEW_RUN_ID/feedback.json
jq '.comparison | {baseline_run_id, current_run_id, coverage, limitations}' "$report"
jq '.comparison.interface_changes[]' "$report"
jq '.comparison.test_changes[]' "$report"
jq '.comparison.evaluation_changes[] |
    {case_id, evaluator, configuration, comparable, changed_fields, before, after, delta}' "$report"
jq '.comparison.failures[] | {source, kind, node_id, verdict, evaluator, status}' "$report"
```

Check in order:

1. Both runs have the intended cases and executions in `comparison.coverage`.
   Check top-level and comparison `limitations`, skipped/not-run cases, and
   `unavailable_references`. Missing evidence is not a pass.
2. Confirm `interface_changes` contains the intended before/after tool field
   with `complete=true`. If it is empty, first verify that both runs observed
   complete catalogs with matched case/configuration/server identity; an empty
   list alone does not prove the interface stayed the same.
3. Review `test_changes` for newly failed or errored cases. Keep deterministic
   contract tests green even when the edit targets agent choice.
4. Review `evaluation_changes` only where `comparable=true` and
   `changed_fields` does not invalidate the comparison. A changed judge model,
   rubric, prompt version, or configuration digest makes a quality-rate delta
   unlike-for-like. Inspect failing case execution IDs and their selected
   traces; do not infer tool use from final prose.
5. Report pass/fail/error/inconclusive counts and the number of trials for each
   evaluator/configuration. `pass_rate` is passed evaluations divided by
   expected evaluations. Expected includes every latest saved identity plus
   terminal missing required expectations; live pending requirements are
   reported separately and excluded until terminal.
   Two trials are a smoke check, not a reliable population estimate.

Keep all attempts, including failures. If the comparison is incomplete or
mixed, describe the observed cases and limitations before another focused
edit. Do not retry only failures until a favorable result appears.

`--baseline` requires the baseline run in the same selected SQLite database
and matching project identity. A fresh worktree or CI job normally has no
history because `.m3/` is ignored; pass a preserved database with
`--results-db PATH` when cross-checkout comparison is intentional. The CLI
does not provide a separate `report` or `compare` command.
