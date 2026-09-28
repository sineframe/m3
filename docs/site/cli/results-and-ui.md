# Results and UI

Add `--ui` to keep a local viewer open after pytest finishes:

```sh
m3 test --ui
```

### `ui` (saved history only)

From the directory where earlier `m3 test` commands saved their results, run:

```sh
m3 ui
# If port 8000 is busy:
m3 ui --port 8123
```

This does not run tests, resolve the project Python, or create a results
database. It requires an existing, readable M3 database at
`.m3/executions.sqlite` **in the current directory**. It does not search
parent directories or accept `--project-root` or `--results-db`; if you run
it from `~`, `/`, or another directory without M3 history, it exits with a
message asking you to change to the correct directory. A valid database
with no runs opens an empty runs index.

After the server is ready, `m3 ui` waits one second and opens the saved-runs
index in your default browser. It also prints a tokenized
`http://127.0.0.1:8000/reports#m3_token=...` link in case the browser cannot
be opened automatically. It does not select the latest run. Press Ctrl+C to
stop serving.
`m3 test --results-db PATH` can write to another database, but `m3 ui`
currently views only the default database. Opening the UI itself does not
start an execution, although the full UI still offers controls that can
start one later.

### UI server and security

This starts one FastAPI server and one loopback port. The production UI is
bundled inside the CLI wheel; Node.js, npm, and Vite are not run at runtime.
`m3 test --ui` waits one second after the server is ready, then opens the
newest newly stored report in your default browser. It prints a link for each
newly stored report like this:

```text
Run label: Run #42
Run: http://127.0.0.1:8000/reports/runs/<runId>#m3_token=<token>
```

The `<runId>` in the direct link is the pytest run ID stored in the test-run
manifest and returned by `/api/v2/feedback/<runId>`. The adjacent label is the
human-facing name; the `Run:` URL line remains unchanged for tools that parse it.
The CLI stays open so the
browser can load results; press Ctrl+C to stop it. A normal pytest failure
still opens the UI and keeps that pytest exit code. Collection/configuration
errors, interruption, server startup errors, and invalid configuration return
an operational failure.

If the test run saves no results, the browser opens the saved-runs index and
the CLI prints `No new stored runs.` and its index link. If opening the browser
fails (for example, on a headless machine), use a printed link from the current
CLI process to authorize the browser. The browser removes the token fragment
from its address bar and keeps the token in the current tab's session storage
for API requests. A new CLI launch uses a new token, so old links stop working.
Treat the printed links as credentials while the server is running.

When stdout is an interactive terminal, the SDK plugin shows a compact test
progress bar. It is disabled for non-TTY output and for verbose pytest modes,
where pytest's normal output remains available.

The server binds only to `127.0.0.1` and requires the launch token for API
requests. It is intended for local use; do not expose it through a public
interface or reverse proxy without suitable network controls.
