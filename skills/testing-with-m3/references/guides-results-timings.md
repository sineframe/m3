<!-- Generated from docs/site/guides/results/timings.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Find slow steps in a test run

Set `M3_TIMINGS=1` to see which steps of an `m3 test` or `pytest` run take the
time: server launch, MCP requests, storage, trace building, harness startup, and
your own fixtures. The run prints a summary table and writes `summary.json` and
a `trace.json` you can open in Perfetto. Nothing is recorded when the variable
is unset.

## Requirements

- A project with M3 tests, such as the one in [Your first MCP test](getting-started.md).
- Works with the `m3` CLI (`m3 test`, `m3 ci test`) and with plain `pytest` in a
  project where the M3 pytest plugin is installed. Other entry points do not
  collect timings.

## Run with timings

Run one of these from the project root. The variable accepts `1`, `true`, `yes`,
or `on`.

```sh
M3_TIMINGS=1 m3 test -- tests/
```

```sh
M3_TIMINGS=1 m3 ci test -- tests/
```

```sh
M3_TIMINGS=1 pytest -p m3.pytest_plugin tests/
```

For PowerShell, set the variable first with `$env:M3_TIMINGS = "1"`.

## Read the summary

After the pytest summary, the run prints `M3 timings:` with the directory it
wrote, then a step table, a counter table, and the slowest tests. This is the
output of `m3 test -- tests/` on the project from the first-test guide, with
elapsed times from one machine. Your numbers differ, and rows are trimmed where
marked.

```text
M3 timings: <project>/.m3/reports/<run-id>/timings

step                                    count    total      p50      p95      max
cli.run                                     1   10.01s   10.01s   10.01s   10.01s
cli.pytest                                  1    8.47s    8.47s    8.47s    8.47s
test                                        7    6.12s  942.5ms    1.19s    1.22s
test.call                                   7    6.09s  942.5ms    1.19s    1.22s
mcp.request[initialize]                     7    4.07s  692.8ms  692.8ms  703.1ms
cli.validate_python                         1    1.53s    1.50s    1.50s    1.53s
server.close                                7  887.2ms  137.6ms  170.2ms  170.2ms
mcp.request[tools/call]                     7  306.5ms   46.9ms   63.7ms   64.9ms
kit.open                                    7  301.9ms   0.92ms  288.0ms  288.0ms
portal.start                                6  298.6ms    1.8ms  288.9ms  288.9ms
pytest.finish                               1  244.5ms  235.9ms  235.9ms  244.5ms
finish.load_entries[run]                    1  154.4ms  148.6ms  148.6ms  154.4ms
trace.finalize                              7  148.2ms   21.7ms   25.2ms   25.2ms
pytest.configure                            1  148.1ms  148.1ms  148.1ms  148.1ms
configure.store_open                        1  134.1ms  134.1ms  134.1ms  134.1ms
server.launch                               7   48.4ms    7.4ms   11.7ms   12.0ms
store.open[SQLiteExecutionStore]            9   41.5ms    3.2ms   16.0ms   16.0ms
...
fixture.setup[m3_kit]                       1    8.3ms    8.3ms    8.3ms    8.3ms
...
... 1 more steps in summary.json

counters
counter                 count    total     p50     p95     max
store.connect             733  910.9ms   1.1ms   2.3ms   8.1ms
store.append               77  434.0ms   5.4ms   9.3ms  10.7ms
store.load_events         201  399.9ms   1.8ms   3.4ms   7.9ms
trace.emit                 14  211.2ms  13.7ms  20.1ms  20.8ms
trace.project               7   48.3ms   6.8ms   7.8ms   7.8ms
store.allocate_seq         14   30.7ms   1.8ms   7.8ms   7.8ms
pytest.persist_attempt      7   21.2ms   3.2ms   3.7ms   3.8ms
store.save_test_result      7   21.1ms   3.2ms   3.7ms   3.7ms
...

slowest tests
      1.22s  tests/test_m3_starter.py::test_shipping_quote
               mcp.request 702.1ms
               portal.start 288.9ms
               kit.open 288.0ms
      1.01s  tests/test_shipping_evaluation.py::test_shipping_evaluation
               mcp.request 751.4ms
               server.close 154.2ms
               store.connect 73.6ms
...

report built in 3.1ms; 0 records dropped
```

Each step row has the number of times the step ran, the total time across those
runs, the median (`p50`), the 95th percentile (`p95`), and the slowest single
run (`max`). Rows are sorted by total time, and the table shows the first 40;
the rest are in `summary.json`. Steps nest, so a parent's total includes its
children. `cli.run` contains `cli.pytest`, which contains every `test` row.
Add the totals of sibling rows, not of parent and child.

In this run, `mcp.request[initialize]` takes about 693 ms per test and is the
largest step inside `test.call`. The `slowest tests` block lists the five
slowest tests with their three biggest steps, so a test slowed by one step is
visible without opening the trace.

Rows whose key varies, such as `mcp.request[tools/call]`, `fixture.setup[m3_kit]`,
and `store.open[SQLiteExecutionStore]`, are separate rows per key.

The `counters` table covers hot paths that run many times, such as storage
calls. Counters report the same columns but have no entry in `trace.json`.

The last line shows how long the report took to build and how many timing
records were dropped. See [Limitations](#limitations).

## Open the files

The run writes these files under `.m3/reports/<run-id>/timings/`:

| File | Contents |
| --- | --- |
| `summary.json` | Every step and counter row with count, total, p50, p95, and max, plus the slowest tests. |
| `trace.json` | One Chrome trace event per recorded span, with one track per process. |
| `controller-<pid>.jsonl`, `worker-<worker-id>-<pid>.jsonl`, `cli-<pid>.jsonl` | Raw records, one file per process. The report is built from these. |

To see the timeline, open [ui.perfetto.dev](https://ui.perfetto.dev), choose
**Open trace file**, and select `trace.json`. The file also loads in
`chrome://tracing`. With `-n`, the controller and each xdist worker appear as
separate processes, so parallel tests show as overlapping bars.

Running the report again on an existing directory rebuilds `summary.json` and
`trace.json` from the raw files; it does not duplicate spans.

## Use timings in CI

Set the variable on the `m3 ci test` step:

```yaml
      - run: m3 ci test --python .venv/bin/python -- tests -q
        env:
          M3_TIMINGS: "1"
```

When `GITHUB_STEP_SUMMARY` is set, the run appends the same tables as Markdown to
the job summary, so the slow steps show on the workflow run page. The timing
files live under `.m3/reports/`, so an artifact upload of that directory includes
them. See [Run M3 in GitHub Actions](guides-ci-github-actions.md).

## Step catalog

A span records one line per use with its start, duration, and parent, and
appears in `trace.json`. A counter only feeds the count, total, and percentile
columns; it never appears in the trace. Names with brackets show the key in
brackets, such as `mcp.request[tools/call]`.

### CLI

| Step | Kind | Covers |
| --- | --- | --- |
| `cli.run` | span | The whole `m3 test` or `m3 ci test` invocation. |
| `cli.validate_python` | span | Checking the project interpreter before starting pytest. |
| `cli.pytest` | span | The pytest subprocess. |
| `cli.ui.start` | span | Starting the viewer for `--ui`. |
| `cli.history_scan[before]`, `cli.history_scan[after]` | span | Reading saved runs before and after the test run. |
| `cli.baseline_check` | span | Checking `--baseline` against the results database. |
| `cli.credentials` | span | Resolving upload credentials. |
| `cli.upload.inspect`, `cli.upload.publish` | span | Inspecting and publishing a run for `--upload` or `m3 upload`. |
| `upload.post` | counter | Each HTTP request to the control plane during upload. |

### pytest

| Step | Kind | Covers |
| --- | --- | --- |
| `pytest.configure` | span | The M3 plugin's setup. |
| `configure.store_open` | span | Opening the default results store. |
| `configure.manifest_init` | span | Starting the test-run manifest. |
| `pytest.collection` | span | pytest collection. |
| `collection.select` | span | Applying the M3 selection and marker rules. |
| `collection.manifest` | span | Recording collected tests in the manifest. |
| `pytest.generate` | counter | Generating parametrized cases for each test. |
| `test` | span | One test, including setup, call, and teardown. Its key is the node ID, with parameter values replaced by their positions, such as `test_x.py::test_y[0-1]`, which is why `test` has one row per test. |
| `test.setup`, `test.call`, `test.teardown` | span | The three pytest phases of each test. |
| `fixture.setup[<name>]` | span | Setting up one fixture, including your own. |
| `pytest.persist_attempt` | counter | Saving one test attempt. |
| `pytest.manifest_write` | counter | Each manifest write. |
| `pytest.finish` | span | The M3 plugin's end-of-run work. |
| `finish.timeout_scan` | span | Looking for timed-out executions. |
| `finish.load_entries[run]`, `finish.load_entries[baseline]` | span | Loading the run's entries and the baseline's. |
| `finish.manifest` | span | Finalizing the manifest. |
| `finish.required_evaluations` | span | Checking `required=True` evaluations. |
| `feedback.build[counters]`, `feedback.build[final]` | span | Building `feedback.json`, in two passes. |
| `feedback.export` | span | Writing `feedback.json`. |

### Session and harness

| Step | Kind | Covers |
| --- | --- | --- |
| `session.start[<harness>]` | span | Starting an agent session. |
| `session.turn[<harness>]` | span | One turn of an agent session. |
| `session.wire_replay` | span | Replaying a recorded wire exchange. |
| `session.close` | span | Closing the session. |
| `harness.preflight` | span | Checks before opening the harness. |
| `harness.open`, `harness.close` | span | Opening and closing the harness connection. |
| `harness.probe_help` | span | Asking the harness binary for its capabilities. |
| `harness.spawn` | span | Starting the harness process. |
| `harness.terminate`, `harness.terminate_group` | span | Stopping the harness process and its process group. |
| `execution.run` | span | One agent execution. |
| `execution.result_wait` | span | Waiting for an execution's result. |
| `execution.finalize` | span | Closing out an execution. |
| `execution.cancel_poll` | counter | Each poll for cancellation. |
| `interaction.command` | span | Running a command for an interaction handler. |
| `elicitation.setup`, `elicitation.commit`, `elicitation.wait`, `elicitation.resolve` | span | The stages of an elicitation round trip. |

### Servers and MCP

| Step | Kind | Covers |
| --- | --- | --- |
| `server.launch`, `server.close` | span | Starting and stopping one server process or connection. |
| `servers.start`, `servers.close` | span | Starting and closing the whole server group. |
| `servers.loopback` | span | Setting up loopback endpoints for servers. |
| `servers.http_proxy` | span | Starting the HTTP proxy for a server. |
| `servers.instrument` | span | Wiring up capture for a server. |
| `proxy.forward` | counter | Each message the HTTP proxy forwards. |
| `capture.write`, `capture.read` | counter | Writing and reading captured MCP traffic. |
| `capture.close` | span | Closing the capture proxy. |
| `mcp.request[<operation>]` | span | One MCP request from a direct client, keyed by operation, such as `initialize`, `tools/list`, or `tools/call`. Time includes the server's work. |
| `judge.request` | span | One request to a judge model. |
| `matcher.record` | counter | Recording one matcher evaluation. |

### Runtime

| Step | Kind | Covers |
| --- | --- | --- |
| `runtime.resolve` | span | Resolving a managed harness runtime. |
| `runtime.acquire` | span | Getting the runtime ready for use. |
| `runtime.download`, `runtime.install` | span | Downloading and installing a managed runtime. |
| `runtime.verify` | span | Verifying the installed runtime. |
| `runtime.smoke` | span | The smoke check after install. |
| `runtime.release` | span | Releasing the runtime when the session ends. |
| `kit.open`, `kit.close` | span | Opening and closing a kit. |
| `portal.start`, `portal.close` | span | Starting and closing the background loop that backs the synchronous API. |

### Workspace

| Step | Kind | Covers |
| --- | --- | --- |
| `workspace.create` | span | Creating an agent workspace. |
| `workspace.worktree` | span | Creating a Git worktree workspace. |
| `workspace.copy` | span | Copying files into a workspace. |
| `workspace.hash` | span | Hashing workspace contents. |
| `workspace.capture` | span | Capturing workspace changes after a run. |
| `workspace.cleanup` | span | Removing the workspace. |

### Storage and trace

| Step | Kind | Covers |
| --- | --- | --- |
| `store.open[<store>]` | span | Opening a store, such as `SQLiteExecutionStore`. |
| `store.connect` | counter | Each SQLite connection. |
| `store.append` | counter | Appending execution events. |
| `store.load_events` | counter | Loading events. |
| `store.allocate_seq` | counter | Allocating event sequence numbers. |
| `store.save_test_result`, `store.save_snapshot`, `store.save_turn`, `store.save_evaluation` | counter | Saving one record of that kind. |
| `blob.put` | counter | Writing one blob. |
| `evidence.prepare` | counter | Preparing evidence for saving. |
| `trace.emit` | counter | Emitting a trace event. |
| `trace.project` | counter | Projecting stored events into a trace view. |
| `trace.finalize` | span | Finishing the trace for an execution. |

### Evaluations

| Step | Kind | Covers |
| --- | --- | --- |
| `eval.run` | span | Running one evaluation. |
| `eval.persist` | span | Saving the evaluation result. |

## Limitations

- Time inside a harness appears as the surrounding `session.turn` and
  `harness.open` rows. M3 does not break down what the agent or model does
  during a turn.
- Collection starts only under pytest or the `m3` CLI. Code that uses the SDK
  from a script or notebook records nothing.
- Under plain `pytest` there are no `cli.*` rows. `cli.run` and its children
  come from `m3 test` and `m3 ci test`.
- `p50` and `p95` are approximate. Durations go into logarithmic histogram
  buckets about 8% wide, so a percentile can be off by about that much. `count`,
  `total`, and `max` are exact.
- A full recording queue drops records instead of slowing the run. The last
  line reports `N records dropped`; when `N` is above zero, totals are low.
- A process that was killed or did not finish writing leaves a file without its
  closing record. The last line then adds `N process file incomplete`, and that
  process's rows may be missing or low.
- The summary lists at most 40 steps and 40 counters. Each step name keeps at
  most 200 distinct keys; further keys are grouped under `(other)`.
- If the timings directory cannot be written, the run warns once and stops
  recording. The test run is unaffected.
- When `M3_TIMINGS` is unset, nothing is written and nothing is printed. The
  variable is read at process start, so set it before launching the command.

Step names, keys, and counts carry names and numbers only. They do not include
prompts, tool arguments, file contents, or credentials. Node IDs and fixture
names are recorded. Parameterized node IDs keep only the parameter positions,
never the values, so a secret used as a test parameter is not recorded.

Next: [Save and reopen executions](guides-results-persistence.md) for the rest of `.m3/reports/<run-id>/`.
