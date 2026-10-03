<!-- Generated from docs/site/reference/cli/index.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# CLI reference

The standalone `m3` command selects the project Python and coordinates pytest,
saved history, the local viewer, managed harnesses, authentication, and report
publishing. Options after `--` are passed to pytest unchanged. `m3 --version`
prints the installed CLI version, and `m3 COMMAND --help` lists every option
of a command. There are no `m3 report` or `m3 compare` commands; use
`m3 test --baseline`.

## `m3 init`

Create `m3.toml`, `tests/test_m3_starter.py`, and `.env.example` when absent.

| Option | Meaning |
| --- | --- |
| `--project-root PATH` | Project to initialize; otherwise use the Git root or current directory. |
| `--project-name NAME` | Non-interactive project name. |
| `--suite NAME` | Non-interactive starter suite name. |
| `--no-skill` | Do not install or update the `testing-with-m3` agent skill. |

`.env.example` contains blank `OPENCODE_API_KEY`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `M3_JUDGE_API_KEY`, and `M3_ACCESS_TOKEN`. `m3 init`
does not install the SDK or create the results database.

A project is initialized when `m3.toml` holds a valid identity. Rerunning
`m3 init` then changes nothing except creating a missing `.env.example`. The
starter test is created only when absent, and you can move, rename, or delete
it. An invalid `m3.toml` stops `m3 init` with exit code 2. If `m3.toml` is
missing but `.m3/executions.sqlite` exists, `m3 init` creates a new
`project_id` and warns that earlier saved runs and uploads keep the previous
one; restore `m3.toml` from Git to keep that identity.

On success, it installs or updates the `testing-with-m3` agent skill; see
[Install the M3 agent skill](https://m3.sineframe.com/docs/guides/agents/skill).

## `m3 setup`

Install the matching SDK with pytest, storage, and judge support into an
isolated project environment. It does not install the CLI there or update a
dependency manifest or lockfile.

Options: `--project-root PATH`, `--python PATH`, `--no-skill`. Without
`--project-root`, the project root is the nearest `m3.toml` at or above the
current directory, stopping at the Git root; otherwise the current directory.

On success, it installs or updates the `testing-with-m3` agent skill; see
[Install the M3 agent skill](https://m3.sineframe.com/docs/guides/agents/skill).

## `m3 doctor`

Check requested project capabilities. Repeat `--require KIND:TARGET` for
configuration, binaries, harnesses, protocol, transport, or storage. Use
`--json` for machine-readable output. `--project-root PATH` defaults to the
nearest `m3.toml` at or above the current directory, stopping at the Git root;
otherwise the current directory. Configuration checks load the project root
`.env` automatically; `--env-file PATH` selects a custom file instead.

## `m3 test`

Run pytest in the project environment and save M3 history.

| Option | Default/effect |
| --- | --- |
| `--project-root PATH` | Nearest `m3.toml` at or above the current directory, stopping at the Git root; otherwise the current directory. |
| `--python PATH` | Selected project interpreter. |
| `--results-db PATH` | `.m3/executions.sqlite` under the project root. |
| `--baseline RUN_ID` | Read an earlier run from the same database for comparison. |
| `--harness KIND[@VERSION]=MODEL[,MODEL...]` | Add a harness/model selection. Repeatable. |
| `--runtime system\|managed` | `system`. |
| `--harness-cache-dir PATH` | Managed harness cache root; otherwise use `M3_HARNESS_CACHE_DIR` or the OS default. |
| `--server …` | Add an HTTP or stdio server selection. Repeatable groups. |
| `--trials N` | Independent executions per selected combination. |
| `-n`, `--num-processes N\|auto` | Run tests in N pytest-xdist worker processes; `auto` uses one per CPU. Off by default. Cannot be combined with `-n` after `--`. See [parallel runs](reference-pytest.md#parallel-runs). |
| `--suite NAME` | Select tests already carrying this suite name. |
| `--execution-timeout SECONDS` | Deadline for each selected agent execution. Defaults to 180. A `timeout=` passed to `agent.run()`, `agent.submit()`, or `agent.session()` takes precedence over this flag. |
| `--judge-max-requests N` | Judge request budget for the run. |
| `--credential-env [KIND:]TARGET=SOURCE` | Map a credential variable. Repeatable. |
| `--env-file PATH` | `.env` in the project root when present; otherwise none. Pass a path to load a custom dotenv file instead. |
| `--upload` | Publish the run; see [Publishing](#publishing). Cannot be combined with `--ui`. |
| `--ui` | Open the bundled viewer after pytest. |
| `--port PORT` | Viewer port; `8000` by default. |

CLI server groups use `--server http --url URL --trust public` and
`--server stdio --command python --arg=-m --arg=MODULE`. Groups replace the
marker server list entirely, including trust settings. Cases are the product
of harnesses, servers, and trials, so 2 harnesses × 2 servers × 2 trials
creates 8 cases. Blank `--suite` input exits with status 2.

## `m3 ci test`

Accepts the test options except viewer options. It applies CI marker selection.
`--ci-metadata PATH` supplies supported CI metadata overrides. See
[Publishing](#publishing) for `--upload` and [exit codes](#exit-codes) for the
result of a failed or skipped upload.

## Publishing

`m3 test` and `m3 ci test` publish a run only when `--upload` is present.
Without it, nothing is sent and the run cannot be published later.

With `--upload`:

1. Before pytest starts, M3 loads the access credential: `M3_ACCESS_TOKEN`, or
   the credential saved by `m3 auth login`. When `CI`, `GITHUB_ACTIONS`, or
   `GITLAB_CI` has a non-empty value, only `M3_ACCESS_TOKEN` is used. A missing
   or malformed credential stops the command with status 2 before any test
   runs.
2. If pytest exits 0 or 1, M3 checks the run for credential values from the
   test environment. If none are found, it publishes the run. Other exit codes
   publish nothing.
3. If publishing fails, the message gives the reason and ends with the next
   step: `retry with m3 upload RUN_ID` when the M3 server was unreachable or
   temporarily unavailable, or `fix the cause and rerun tests` otherwise.

Neither command passes `M3_ACCESS_TOKEN` to pytest.

## `m3 upload RUN_ID`

Publish a run started with `--upload` that was not published, without
rerunning tests. Options: `--project-root`, `--results-db`, and `--env-file`.
Without `--project-root`, the project root is the nearest `m3.toml` at or above
the current directory, stopping at the Git root; otherwise the current
directory. Its `.env` is loaded automatically when `--env-file` is omitted, and
`--results-db` defaults to `.m3/executions.sqlite` under it.

The run must meet all of these conditions:

- It was started with `m3 test --upload` or `m3 ci test --upload`, pytest
  exited 0 or 1, and the credential check completed without finding credential
  values.
- `--project-root` and `--results-db` select the project and database that
  recorded it.
- Its report files have not changed since the run.
- `m3.toml` contains `project_id`.

`m3 upload` also needs an access credential, as described in
[Publishing](#publishing). A run without a completed credential check is
refused with:

```text
m3 upload: run RUN_ID cannot be uploaded: it was not started with --upload, pytest did not exit 0 or 1, or its credential scan failed; rerun the tests with --upload
```

## `m3 ui`

Open history from `.m3/executions.sqlite` under the project root without running
pytest. `--project-root PATH` selects the project; by default it is the nearest
`m3.toml` at or above the current directory, stopping at the Git root;
otherwise the current directory. `--port PORT` changes the loopback port. It
does not accept a custom database path.

## `m3 auth`

`login` starts device authorization in the M3 account console and saves the
issued 30-day CLI credential in a supported operating-system credential
store. `status` validates the saved CLI credential with M3 and reports its
organization and expiry. It checks the format of a CI token supplied as
`M3_ACCESS_TOKEN`, but does not validate that CI token with M3. `logout` asks
M3 to revoke the saved CLI credential, then removes it locally. It does not
revoke or remove `M3_ACCESS_TOKEN`.

## `m3 runtime cache`

`list` shows managed harness assets. `prune` removes unused assets. Both accept
`--cache-dir` (also `--harness-cache-dir`) and `--project-root`; the project
root defaults to the nearest `m3.toml` at or above the current directory,
stopping at the Git root, otherwise the current directory.

Use `list` to inspect downloaded versions and `prune` when you want to reclaim
disk space. Cleanup is optional; later tests download pruned runtimes again.
See [cache configuration and reuse](reference-managed-runtimes.md#cache-location-and-precedence)
and [inspection and pruning](reference-managed-runtimes.md#inspect-and-prune-cached-runtimes)
for details.

## Exit codes

`m3` exits with 0 on success. Code 2 is M3's operational error for every
command: an invalid option or configuration, an unavailable project Python, or
a failed operation. Argument errors print `m3 COMMAND: invalid command or
configuration` and do not repeat the rejected value.

| Command | Code | Meaning |
| --- | --- | --- |
| `m3 test`, `m3 ci test` | 0 | pytest passed and M3 saved the run. With `--upload`, publishing must also succeed; if it fails, the exit code is 2 (see [Exit codes with `--upload`](#exit-codes-with-upload)). |
| | 1 | A test failed, or pytest would have exited 0 but M3 fails the run: every selected test was skipped, M3 could not save run records or export `feedback.json`, a pytest-xdist worker failed, or a `required=True` evaluation did not pass. |
| | 2 | pytest was interrupted, for example by a collection error, or M3 hit an operational error, such as a missing `--env-file`, a missing or malformed upload credential, or, with `--upload`, a failed upload inspection or publish after tests passed. Most of these errors stop M3 before pytest starts; see [below](#telling-an-m3-error-from-a-pytest-interruption). |
| | 3 | pytest internal error. |
| | 4 | pytest usage error: an unknown pytest option, a test without a suite name, a negative `--judge-max-requests`, or a `judge:` credential source that is unset or empty. |
| | 5 | pytest collected no tests. |
| | 128 + N | Signal N stopped the run: 130 for Ctrl-C, 143 for SIGTERM. |
| `m3 test --ui` | pytest's code, or 2 | After pytest exits with a code below 128 other than 2, the viewer opens. Ctrl-C stops it and returns pytest's code. 2 means the viewer could not start or stopped unexpectedly. |
| `m3 test --upload --ui` | 2 | The options cannot be combined. M3 prints `m3 test: --upload cannot be combined with --ui` and runs no tests. |
| `m3 doctor` | 0 | Every requested capability is ready. |
| | 1 | At least one requested capability, or the project environment, is not ready. |
| | 2 | Invalid `--require` value or other option, a missing `--env-file`, invalid configuration, or a project Python that cannot be started. |
| `m3 init` | 0 | Project initialized, or already initialized. |
| | 2 | Invalid or unwritable project files, or `--project-name` or `--suite` missing without an interactive terminal. |
| | 130 | Ctrl-C or end of input at a prompt. |
| `m3 setup` | 0 | The project environment is ready. |
| | 2 | The environment could not be created, installed, or verified. |
| `m3 upload` | 0 | The run was published. |
| | 2 | Missing or malformed credential, or publication failed. Local results are unchanged. |
| `m3 ui` | 0 | You stopped the viewer with Ctrl-C. |
| | 2 | No valid history in `.m3/executions.sqlite`, an invalid port, or the viewer could not start or stopped unexpectedly. |
| `m3 auth login` | 0 | The CLI credential is saved. |
| | 2 | Authorization was denied or expired, M3 or the credential store failed, or revoking the replaced credential failed after the new one was saved. |
| | 130 | Ctrl-C. |
| `m3 auth status`, `m3 auth logout` | 0 | The command finished, including when no CLI credential is saved. |
| | 2 | A malformed `M3_ACCESS_TOKEN` (`status`), a failed M3 request, or a credential store error. |
| `m3 runtime cache list`, `m3 runtime cache prune` | 0 | The command finished. |
| | 2 | The cache could not be read or pruned, including entries still in use after `prune` waits 10 seconds. |

### Telling an M3 error from a pytest interruption

Both produce 2. M3 writes its own errors to stderr as a line that starts with
`m3 test:`, `m3 ci:`, or `m3:`. When pytest is interrupted, it writes a line
such as `Interrupted: 1 error during collection` to stdout.

Most M3 errors stop the command before pytest starts, so no tests run. Two kinds
of M3 error come after pytest has run, with M3's own line on stderr:

- With `--upload`, publishing fails or the run cannot be inspected for
  publishing after the tests pass (`m3 test:` or `m3 ci:`, see
  [Exit codes with `--upload`](#exit-codes-with-upload)).
- With `m3 test --ui`, the viewer server could not be started, did not become
  ready, or stopped unexpectedly (`m3: UI server readiness failed`,
  `m3: UI server stopped unexpectedly`, `m3: UI server could not be started`).

The prefix is not always the command you typed. Errors from the test run itself,
such as an invalid option, a missing `--baseline` run, or a project Python that
cannot be started, always start with `m3 test:`, including under `m3 ci test`.
Under `m3 ci test`, errors from the credential check, `--env-file`, and
`--ci-metadata` start with `m3 ci:`. To find an M3 error in stderr, match all
three prefixes.

### Exit codes with `--upload`

`m3 test --upload` and `m3 ci test --upload` run these steps; see
[Publishing](#publishing) for what each one does.

- **Credential check, before pytest.** M3 loads `M3_ACCESS_TOKEN`. Outside CI,
  it falls back to the credential saved by `m3 auth login`. A missing or
  malformed credential, or an invalid `M3_CONTROL_PLANE_URL`, exits 2 and no
  tests run. When `CI`, `GITHUB_ACTIONS`, or `GITLAB_CI` is set, only
  `M3_ACCESS_TOKEN` is used.
- **After pytest.** M3 publishes only when pytest exited 0 or 1. For 2, 3, 4, 5,
  and signal exits, it skips publishing without a message and returns pytest's
  code.
- **Publish failure.** M3 prints `PREFIX: publishing failed: REASON; retry with
  m3 upload RUN_ID` when a retry can succeed, or `PREFIX: publishing failed:
  REASON; fix the cause and rerun tests` when it cannot. `PREFIX` is `m3 test`
  under `m3 test --upload` and `m3 ci` under `m3 ci test --upload`. If no
  reason is safe to print, the message is `PREFIX: publishing failed; retry with
  m3 upload RUN_ID`.
- **Inspection failure.** Before publishing, M3 inspects the run for credential
  values from the test environment. If that fails, M3 prints `PREFIX: upload
  inspection unavailable; the run was not published. Rerun the tests with
  --upload`, or the `fix the cause and rerun tests` form above when it has a
  reason to give. A retry with `m3 upload` is not offered.

After a publish or inspection failure, M3 returns the test exit code if the
tests failed (1), or 2 if they passed.
