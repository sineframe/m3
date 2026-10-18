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
The readiness check waits `cli.project_python_timeout_seconds` from
`pyproject.toml`; see [Configuration](reference-configuration.md).

On success, it installs or updates the `testing-with-m3` agent skill; see
[Install the M3 agent skill](https://m3.sineframe.com/docs/guides/agents/skill).

## `m3 doctor`

Check requested project capabilities. Repeat `--require KIND:TARGET` for
configuration, binaries, harnesses, protocol, transport, or storage. Use
`--json` for machine-readable output. `--project-root PATH` defaults to the
nearest `m3.toml` at or above the current directory, stopping at the Git root;
otherwise the current directory. Configuration checks load the project root
`.env` automatically; `--env-file PATH` selects a custom file instead.

`--m3-config PATH` reads `[tool.m3]` from PATH instead of discovering
`pyproject.toml`. Doctor reads `cli.project_python_timeout_seconds` on every
run, and a configuration error fails it even without a `config` requirement. See
[Configuration](reference-configuration.md).

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
| `--trials N` | Independent executions per selected combination. Only tests that request the `agent` fixture repeat; other tests run once, and the run warns if no test requests it. See [trials](reference-pytest.md#trials). |
| `-n`, `--num-processes N\|auto` | Run tests in N pytest-xdist worker processes; `auto` uses one per CPU. Off by default. Cannot be combined with `-n` after `--`. See [parallel runs](reference-pytest.md#parallel-runs). |
| `--suite NAME` | Select tests already carrying this suite name. |
| `--execution-timeout SECONDS` | Deadline for each selected agent execution. Defaults to 180. A `timeout=` passed to `agent.run()`, `agent.submit()`, or `agent.session()` takes precedence over this flag. |
| `--judge-max-requests N` | Judge request budget for the run. |
| `--credential-env [KIND:]TARGET=SOURCE` | Map a credential variable. Repeatable. |
| `--env-file PATH` | `.env` in the project root when present; otherwise none. Pass a path to load a custom dotenv file instead. |
| `--m3-config PATH` | Read `[tool.m3]` settings from PATH instead of discovering `pyproject.toml`. The path is forwarded to pytest as an absolute path; it cannot be passed after `--`. See [Configuration](reference-configuration.md). |
| `--upload` | Publish the run; see [Publishing](#publishing). Cannot be combined with `--ui`. |
| `--ui` | Open the bundled viewer after pytest. |
| `--port PORT` | Viewer port; `8000` by default. |

CLI server groups use `--server http --url URL --trust public` and
`--server stdio --command python --arg=-m --arg=MODULE`. Groups replace the
marker server list entirely, including trust settings. Cases are the product
of harnesses, servers, and trials, so 2 harnesses × 2 servers × 2 trials
creates 8 cases. Blank `--suite` input exits with status 2.

Set `M3_TIMINGS=1` to print a step-timing summary after the run; see
[Find slow steps in a test run](guides-results-timings.md).

## `m3 ci test`

Accepts the test options except viewer options. It applies CI marker selection.
`--ci-metadata PATH` supplies supported CI metadata overrides.
`M3_TIMINGS=1` also applies here and adds the timing tables to the GitHub job
summary; see [Find slow steps in a test run](guides-results-timings.md).

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
issued 60-day CLI credential in a supported operating-system credential
store. `status` validates the saved CLI credential with M3 and reports its
organization and expiry. It checks the format of an access token supplied as
`M3_ACCESS_TOKEN`, but does not validate that token with M3. `logout` removes
the saved CLI credential from this machine without contacting M3, so it does
not revoke it; revoke it on the **Access tokens** page in the M3 account
console, where it is listed as `M3 CLI (<hostname>)` with type CLI. It does not remove `M3_ACCESS_TOKEN`.

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

## `m3 lint`

Check one MCP server's metadata against shapes that Claude Code or Codex
mishandle. `m3 lint` reads only `server/discover`, `initialize`, `tools/list`,
`prompts/list`, `resources/list`, and `resources/templates/list`. It never
calls a tool and never contacts a model.

```sh
m3 lint https://example.com/mcp --header "Authorization: Bearer $TOKEN"
m3 lint -- python server.py
```

Pass a streamable HTTP URL, or a stdio command after `--`, but not both.

| Option | Default/effect |
| --- | --- |
| `--header NAME:VALUE` | HTTP header sent with every request. Repeatable; URL targets only. Values are never printed. |
| `--harness KIND[@VERSION]` | `claude_code` or `codex`. Repeatable. Default: both, at the newest version the rule data knows. |
| `--directory NAME` | `claude`, `openai`, `muse` or `all`: also check the directory's published listing rules. Repeatable. Without `--harness`, only directory checks run. See [Directory readiness](reference-directory-readiness.md). |
| `--server-name NAME` | The name your agent configuration gives the server, used to check `mcp__<server>__<tool>` lengths. Defaults to `serverInfo.name`. |
| `--fail-on error\|warning\|never` | `error`. |
| `--ignore RULE_ID` | Skip a rule. Repeatable. |
| `--timeout SECONDS` | `30`. Deadline for each request; at most 86400. The `server/discover` probe waits at most 3 seconds. |
| `--list-rules` | Print the rule catalogue and exit without connecting. |
| `--json` | Machine-readable report. |

Each finding names the rule, the location, and every affected harness with its
version range and impact. The impact sets the severity: `session` (every model
request fails), `server` (all of the server's tools are lost) and `tool` (that
tool is lost) are errors; `degraded` is a warning; `advisory` is a note.

If a listing fails or stops early (1000 pages, or a repeated cursor), the
report says so: the human output prints an `incomplete:` line, and the JSON
`collection.listings` entry for that method carries its `error` or
`truncated: true`, with `collection.complete: false`. Findings then cover
only what was read.

Exit status 0 means no finding at or above `--fail-on`, 1 means at least one,
and 2 means a usage error or a server that could not be read.


Invalid command/configuration is an operational error. Pytest failures retain
pytest's failure exit behavior; requested upload failures can turn an otherwise
passing CI invocation into an operational failure.
