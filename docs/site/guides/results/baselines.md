# Compare a run with a baseline

`--baseline` compares a new feedback bundle with an earlier run in the same
results database. Capture the ID produced by your own first run; IDs printed in
documentation do not exist in your database.

Run these commands from the project in [Your first MCP test](/getting-started).

## Create the baseline

Run the test once:

```sh
m3 test -- tests/test_m3_starter.py
```

The command prints `Run ID: …` and the local report path. Copy that value into
your shell without the `Run ID:` label.

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

If M3 reports that the baseline was not found, confirm that both commands used
the same project root and results database.
