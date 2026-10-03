---
title: "CLI output and trace limitations"
description: "What each M3 line printed after a pytest run means, and which trace limitations are routine."
---

# CLI output and trace limitations

## Lines printed after a run

In an interactive terminal, `m3 test` starts at the top of the screen (earlier output stays in scrollback) with the M3 banner, then shows a live block under the log: a matrix with one cell per test and a progress line. Each file gets a summary line when it finishes, and each failing test is listed with its assertion as it fails. The `M3 run`, `M3 feedback`, `M3 verdicts`, and `M3 observations` lines are shown as a panel titled with the run ID, with `verdicts`, `observations`, and `feedback` rows, a `vs baseline` row when `--baseline` is set, and a `slowest` row when a test took 0.5 seconds or more. With `--ui`, the report opens in the browser automatically; while the UI server runs, `o` reopens it, `c` copies its link, `b` opens it compared with the `--baseline` run or else the previous run (the key is hidden when there is no earlier run), and `q` stops the server. When output is piped or redirected, under `m3 ci test`, or with `TERM=dumb`, M3 prints the plain lines below. Set `NO_COLOR` to keep the interactive layout without colour. With `-n`, the live block follows all workers and the per-file lines are left out, since files run interleaved. Pytest's own output takes over the live block under `-v`, `-s`, or live logging.

| Line | Meaning | What to do |
|---|---|---|
| `M3 run` | The run ID created by this invocation. | Use it with `--baseline`. If a run started with `--upload` was not published, use it with `m3 upload`. |
| `M3 feedback` | Path of this run's `feedback.json`. | Read selected fields from this path. |
| `M3 verdicts` | Count of test cases per verdict. | Informational. |
| `M3 observations` | Tool-error results and completed executions in this run. | A tool error is a server result with `is_error=True`, not a test failure by itself. |
| `M3: no tests executed; skipped-only runs fail` | Every selected test was skipped or deselected, and the run fails. | Unskip or select a test. |
| `M3 execution timeout` | One execution reached a deadline. It is printed once per timed-out execution: `id`, `stage`, `elapsed`, `feedback`. | Informational. The run's result is the pytest outcome, so a test that expects a timeout can still pass. Inspect the execution's diagnostics when the timeout was not expected. |
| `M3 test manifest persistence was incomplete` | Pytest results were not fully saved. | Saved history for this run is incomplete; do not use it as a baseline. |
| `M3 required evaluations blocked finalization` | A `required=True` evaluation did not pass. | The run is not successful even if test code caught the exception. |
| `M3 timings` | Only with `M3_TIMINGS=1`: the timings directory, followed by step and counter tables, the slowest tests, and a `report built in ...; N records dropped` line. | Read the tables to find slow steps; see [Find slow steps in a test run](../guides/results/timings.md). |
| `M3 CI excluded` | `m3 ci test` only: the number of tests excluded by markers with `ci=False`. | Informational. |
| `M3 feedback export failed` | `feedback.json` was not written. | The run's database records remain; rerun to get a feedback file. |

## Trace limitations

A trace's `limitations` list says which evidence M3 could not fully observe or save. Check it before treating a missing value as evidence of absence.

| Code | Where it appears | Routine? |
|---|---|---|
| `capture_incomplete` | Direct traces: always present. Every finalized direct trace records it, with `completeness` `partial` (`sdk/src/m3/direct_trace.py:604-605`). Agent traces: added when harness output was invalid, truncated, timed out, or duplicated. | Routine on direct traces. On agent traces, read the trace diagnostics before concluding that a call did not happen. |
| `capture_disabled` | Provider message and reasoning observations are dropped when provider-message capture is disabled (`harness/observation_sink.py:85-89`). Raw evidence is also not stored when raw capture is disabled (`:123-126`). | Routine when the corresponding capture is off. Other normalized harness observations are still recorded. |
| `partial_trace` | An ACP turn failed after recording only part of its evidence (`harness/acp.py:1730`). | No. The turn's evidence is incomplete. |
| `cleanup_failed` | A server process or harness could not be shut down cleanly. | No. Results stand, but check for leftover processes. |
| `persistence_failed` | Some observations could not be saved (`harness/observation_sink.py:120,403`). | No. The saved trace lacks evidence; do not treat a missing value as absence. |
