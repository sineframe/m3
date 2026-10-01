---
title: "CLI reference"
description: "The standalone m3 command selects the project Python and coordinates pytest, saved history, the local viewer, managed harnesses, authentication, and report publishing. Options after -- are passed to pytest unchanged."
---

# CLI reference

The standalone `m3` command selects the project Python and coordinates pytest,
saved history, the local viewer, managed harnesses, authentication, and report
publishing. Options after `--` are passed to pytest unchanged.

## `m3 init`

Create `m3.toml`, `tests/test_m3_starter.py`, and `.env.example` when absent.

| Option | Meaning |
| --- | --- |
| `--project-root PATH` | Project to initialize; otherwise use the Git root or current directory. |
| `--project-name NAME` | Non-interactive project name. |
| `--suite NAME` | Non-interactive starter suite name. |

Existing complete initialization is left unchanged. Partial initialization is
reported for manual repair.

## `m3 setup`

Install the matching SDK with pytest, storage, and judge support into an
isolated project environment. It does not install the CLI there or update a
dependency manifest or lockfile.

Options: `--project-root PATH`, `--python PATH`.

## `m3 doctor`

Check requested project capabilities. Repeat `--require KIND:TARGET` for
configuration, binaries, harnesses, protocol, transport, or storage. Use
`--json` for machine-readable output. `--env-file PATH` is explicit; M3 never
searches for a current-directory `.env`.

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
| `--server …` | Add an HTTP or stdio server selection. Repeatable groups. |
| `--trials N` | Independent executions per selected combination. |
| `--suite NAME` | Select tests already carrying this suite name. |
| `--execution-timeout SECONDS` | Deadline for each selected agent execution. |
| `--judge-max-requests N` | Judge request budget for the run. |
| `--credential-env [KIND:]TARGET=SOURCE` | Map a credential variable. Repeatable. |
| `--env-file PATH` | Load one explicit dotenv file. |
| `--ui` | Open the bundled viewer after pytest. |
| `--port PORT` | Viewer port; `8000` by default. |

For repeatable native harness selections across one pytest test, see the
[multiple-harness guide](../../guides/agents/multiple-harnesses.md).

## `m3 ci test`

Accepts the test options except viewer options. It applies CI marker selection.
`--upload` publishes completed passing and failing reports;
`--ci-metadata PATH` supplies supported CI metadata overrides.

## `m3 upload RUN_ID`

Retry publication of a saved run without rerunning tests. Options:
`--project-root`, `--results-db`, and `--env-file`.

## `m3 ui`

Open history from the default database in the current project without running
pytest. `--port PORT` changes the loopback port. It does not accept a custom
database path.

## `m3 auth`

`login` manages access and may save a developer token. `status` reports
the local credential. `logout` removes that local copy; it does not revoke the
token.

## `m3 runtime cache`

`list` shows managed harness assets. `prune` removes unused assets. Both accept
`--cache-dir` (also `--harness-cache-dir`) and `--project-root`.

Invalid command/configuration is an operational error. Pytest failures retain
pytest's failure exit behavior; requested upload failures can turn an otherwise
passing CI invocation into an operational failure.
