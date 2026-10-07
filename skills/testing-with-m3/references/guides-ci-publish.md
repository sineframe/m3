<!-- Generated from docs/site/guides/ci/publish.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Publish or retry a run

Publishing is opt-in. A run is sent to M3 only when the command that starts it
includes `--upload`; without it, nothing is sent and the run cannot be
published later.

On your machine, log in once and add `--upload`:

```sh
m3 auth login
m3 test --upload -- tests/
```

In CI, store an access token as `M3_ACCESS_TOKEN` (see
[Manage M3 access](guides-ci-access.md)) and run:

```sh
m3 ci test --upload -- tests/
```

M3 loads the credential before pytest starts, so a missing credential stops the
command before any test runs. After pytest exits 0 or 1, M3 checks the run for
credential values from the test environment and publishes it. A run that ends
with any other exit code is not published.

## Retry a failed upload

If the M3 server was unreachable or temporarily unavailable, the failure
message ends with `retry with m3 upload RUN_ID`. The run stays in the local
database. Retry without rerunning tests from the same project and with the same
`--results-db`:

```sh
printf 'Run ID to retry: '
IFS= read -r RUN_ID
m3 upload "$RUN_ID"
```

PowerShell:

```powershell
$RUN_ID = Read-Host "Run ID to retry"
m3 upload $RUN_ID
```

Do not copy an ID from documentation; it must identify a run in your database.
If credentials came from a custom file, pass the same `--env-file` again; a
project root `.env` is loaded automatically.

If the message ends with `fix the cause and rerun tests`, retrying the same run
cannot succeed: for example, the server rejected the report or the run contains
a credential from the test environment. Fix the cause, then rerun the tests with
`--upload`. The
[`m3 upload` reference](reference-cli.md#m3-upload-run-id) lists
every condition a run must meet.
