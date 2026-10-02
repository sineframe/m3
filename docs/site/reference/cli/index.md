---
title: "CLI reference"
description: "The standalone m3 command selects the project Python and coordinates pytest, saved history, the local viewer, managed harnesses, authentication, and report publishing. Options after -- are passed to pytest unchanged."
---

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
[Install the M3 agent skill](../../guides/agents/skill.md).

## `m3 setup`

Install the matching SDK with pytest, storage, and judge support into an
isolated project environment. It does not install the CLI there or update a
dependency manifest or lockfile.

Options: `--project-root PATH`, `--python PATH`, `--no-skill`.

On success, it installs or updates the `testing-with-m3` agent skill; see
[Install the M3 agent skill](../../guides/agents/skill.md).

## `m3 doctor`

Check requested project capabilities. Repeat `--require KIND:TARGET` for
configuration, binaries, harnesses, protocol, transport, or storage. Use
`--json` for machine-readable output. Configuration checks load the project
root `.env` automatically; `--env-file PATH` selects a custom file instead.

## `m3 test`

Run pytest in the project environment and save M3 history.

| Option | Default/effect |
| --- | --- |
| `--project-root PATH` | Git root or current project. |
| `--python PATH` | Selected project interpreter. |
| `--results-db PATH` | `.m3/executions.sqlite`. |
| `--baseline RUN_ID` | Read an earlier run from the same database for comparison. |
| `--harness KIND[@VERSION]=MODEL[,MODEL...]` | Add a harness/model selection. Repeatable. |
| `--runtime system\|managed` | `system`. |
| `--harness-cache-dir PATH` | Managed harness cache root; otherwise use `M3_HARNESS_CACHE_DIR` or the OS default. |
| `--server …` | Add an HTTP or stdio server selection. Repeatable groups. |
| `--trials N` | Independent executions per selected combination. |
| `--suite NAME` | Select tests already carrying this suite name. |
| `--execution-timeout SECONDS` | Deadline for each selected agent execution. Defaults to 180. A `timeout=` passed to `agent.run()`, `agent.submit()`, or `agent.session()` takes precedence over this flag. |
| `--judge-max-requests N` | Judge request budget for the run. |
| `--credential-env [KIND:]TARGET=SOURCE` | Map a credential variable. Repeatable. |
| `--env-file PATH` | Project root `.env` when present; otherwise none. Pass a path to load a custom dotenv file instead. |
| `--ui` | Open the bundled viewer after pytest. |
| `--port PORT` | Viewer port; `8000` by default. |

CLI server groups use `--server http --url URL --trust public` and
`--server stdio --command python --arg=-m --arg=MODULE`. Groups replace the
marker server list entirely, including trust settings. Cases are the product
of harnesses, servers, and trials, so 2 harnesses × 2 servers × 2 trials
creates 8 cases. Blank `--suite` input exits with status 2.

## `m3 ci test`

Accepts the test options except viewer options. It applies CI marker selection.
`--upload` publishes completed passing and failing reports;
`--ci-metadata PATH` supplies supported CI metadata overrides.

## `m3 upload RUN_ID`

Retry publication of a saved run without rerunning tests. Options:
`--project-root`, `--results-db`, and `--env-file`. The project root `.env` is
loaded automatically when `--env-file` is omitted.

## `m3 ui`

Open history from the default database in the current project without running
pytest. `--port PORT` changes the loopback port. It does not accept a custom
database path.

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
`--cache-dir` (also `--harness-cache-dir`) and `--project-root`.

Use `list` to inspect downloaded versions and `prune` when you want to reclaim
disk space. Cleanup is optional; later tests download pruned runtimes again.
See [cache configuration and reuse](../managed-runtimes.md#cache-location-and-precedence)
and [inspection and pruning](../managed-runtimes.md#inspect-and-prune-cached-runtimes)
for details.

Invalid command/configuration is an operational error. Pytest failures retain
pytest's failure exit behavior; requested upload failures can turn an otherwise
passing CI invocation into an operational failure.
