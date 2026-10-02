<!-- Generated from docs/site/guides/results/baselines.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Compare a run with a baseline

`--baseline` compares a new feedback bundle with an earlier run in the same
results database. Capture the ID produced by your own first run; IDs printed in
documentation do not exist in your database.

Run these commands from the project in [Your first MCP test](getting-started.md).

## Create the baseline

Run the test once:

```sh
m3 test -- tests/test_m3_starter.py
```

The command prints `M3 run <id>` and the local report path. Copy the value after
`M3 run` into your shell.

For Bash or zsh:

```sh
printf 'Baseline run ID: '
IFS= read -r BASELINE_RUN_ID
```

For PowerShell:

```powershell
$BASELINE_RUN_ID = Read-Host "Baseline run ID"
```

Now make the behavior change you want to evaluate, then compare the next run.

```sh
m3 test --baseline "$BASELINE_RUN_ID" -- tests/test_m3_starter.py
```

PowerShell uses `$BASELINE_RUN_ID` without quotes around the variable name:

```powershell
m3 test --baseline $BASELINE_RUN_ID -- tests/test_m3_starter.py
```

The second command prints a different run ID. Capture it as `CURRENT_RUN_ID`
the same way, then inspect `.m3/reports/<CURRENT_RUN_ID>/feedback.json`. The
comparison reads the baseline; it does not modify it.

The `comparison` object has `baseline_run_id`, `current_run_id`, `coverage`,
`limitations`, `interface_changes[]`, `test_changes[]`, `failures[]`, and
`evaluation_changes[]`. Each `failures[]` entry has `source`, `kind`,
`node_id`, `verdict`, `evaluator`, and `status`. Each
`evaluation_changes[]` entry has `case_id`, `evaluator`, `configuration`,
`comparable`, `changed_fields`, `before`, `after`, and `delta`.

Each `interface_changes[]` entry carries `complete=true`. An empty list proves
no interface change only when both runs observed complete catalogs with
matched identity. A changed judge model, rubric, prompt version, or
configuration digest makes `evaluation_changes` unlike-for-like, as shown by
`comparable` and `changed_fields`.

`--baseline` needs a matching project identity. A fresh worktree or CI job
has no history because `.m3/` is ignored, so preserve a database and pass it
with `--results-db`.

If M3 reports that the baseline was not found, confirm that both commands used
the same project root and results database.
