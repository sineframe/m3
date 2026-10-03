<!-- Generated from docs/site/troubleshooting/results.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Missing traces or saved results

## No saved run

Use `m3 test`, or configure `SQLiteExecutionStore` explicitly in direct SDK
code. Plain pytest without the storage plugin keeps SDK executions in memory.

## `m3 ui` shows no history

Run it from the project or pass `--project-root PATH`; it reads
`.m3/executions.sqlite` under the project root. `m3 ui` does not accept the
custom path used by `m3 test --results-db`.

## Trace is not finalized

Close the direct client or agent session before requesting its final trace.
Use the immediate operation result while the context is open.
