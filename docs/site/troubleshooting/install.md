---
title: "Installation and project Python"
description: "Run m3 setup from the project root. It installs the SDK version matching the standalone CLI into the selected isolated environment. If M3 selected the wrong environment, pass --python PATH to setup and doctor."
---

# Installation and project Python

M3 picks the project environment from `--python`, `VIRTUAL_ENV`,
`CONDA_PREFIX`, then the project `.venv`, and the commands differ when nothing
applies.
[Choose the project environment](../start/install.md#choose-the-project-environment)
gives the full order. When the project environment is ready, `m3 doctor` prints
the interpreter it checked as `project Python`.

## `project m3 version ... does not match CLI SDK version ...; install matching versions`

Printed by `m3 test` and `m3 doctor`. Run `m3 setup` from the project root. It
installs the SDK version matching the standalone CLI into the selected isolated
environment. If M3 selected the wrong environment, pass `--python PATH` to
`setup` and `doctor`.

## `no project environment is configured; run m3 setup`

Printed by `m3 doctor` when there is no `--python`, no active `VIRTUAL_ENV` or
`CONDA_PREFIX`, and no `.venv` in the project root. Run `m3 setup` from the
project root, which creates `.venv`. `m3 test` does not stop here. It falls back
to `python3` on `PATH` and usually then fails with the next message. If no
`python3` or `python` is on `PATH`, `m3 test` prints `no project Python was
found; pass --python PATH`.

## `the project Python is missing required M3 packages`

The full message lists the missing names and ends with `run m3 setup in the
project`. The names can be `pytest`, `m3`, `m3.pytest_plugin`, `openai`, and
`SQLiteExecutionStore`, which correspond to the pytest, SDK, judge, and storage
support. Run `m3 setup`. A direct SDK installation needs the applicable extras,
such as `sf-m3[pytest,storage,judge]`.

If you never created an environment, `m3 test` may be checking a system Python.
See [Choose the project environment](../start/install.md#choose-the-project-environment).

## `active Conda base is not a project environment; create or activate a project environment`

Printed by `m3 setup` when there is no `--python`, no `VIRTUAL_ENV` is set,
`CONDA_PREFIX` is set, and `CONDA_DEFAULT_ENV` is `base` in any letter case
(`BASE` also matches). The check does not look at whether `CONDA_PREFIX` exists.
With a `VIRTUAL_ENV` set, setup never reaches this check: a usable one is
selected, and an unusable one stops with the `VIRTUAL_ENV` message below.
Activate a project environment, deactivate `base`, or pass `--python PATH`.

## `active VIRTUAL_ENV environment is unavailable`

Printed by `m3 setup` (and `active CONDA_PREFIX environment is unavailable` for
Conda) when the variable points at a directory with no Python executable, for
example a deleted environment in a shell that is still activated. Run
`deactivate`, or recreate the environment. `m3 test` skips such a variable and
moves to the next source, while `m3 doctor` falls back to a working `.venv` or
prints `the active project environment is unavailable` when there is none. See
[Choose the project environment](../start/install.md#choose-the-project-environment).

## `refusing to install into a system or global Python; use an isolated environment`

Create or activate an isolated environment, then rerun setup. M3 refuses to
modify a system/global Python. This check applies to `m3 setup` only, including
`m3 setup --python PATH`.

## `Python 3.10 or newer is required`

Printed by `m3 setup`. Pass `--python PATH` with a Python 3.10 or newer
environment, or activate one.

## `project .venv exists but its Python executable is unavailable`

Printed by `m3 setup` when `.venv` exists in the project root but has no Python
executable, for example after its `bin` directory was removed. Delete `.venv` and rerun
`m3 setup`, or pass `--python PATH`.

## `selected Python executable is unavailable`, `could not be started`, and `returned an invalid check result`

`m3 setup --python PATH` prints `selected Python executable is unavailable`
when `PATH` is not an existing file. `m3 test --python PATH` and `m3 doctor
--python PATH` have no such check: a missing path is reported as `the selected
project Python could not be started`.

`selected Python executable could not be started` (`m3 setup`) and `the
selected project Python could not be started` (`m3 test`, `m3 doctor`) mean the
program could not be launched (for example the file is not executable), exited
with a non-zero status, or did not answer in time.

A program that starts and exits successfully but is not Python, such as
`/bin/echo`, gives `selected Python executable returned an invalid check
result` from `m3 setup` and `the selected project Python returned an invalid
check result` from `m3 test` and `m3 doctor`.

In all cases, check the path by running it with `--version`.

## `could not install the matching M3 SDK from PyPI`

`m3 setup` runs `uv pip install` (or `python -m pip install` when uv is not
installed) for `sf-m3[pytest,storage,judge]==CLI_VERSION`, and hides the
installer output. The command fails when that exact version is not published
on the package index or the index is unreachable. Run the same command by hand
to see the installer's error:

```sh
uv pip install --python PATH "sf-m3[pytest,storage,judge]==CLI_VERSION"
```

Replace `CLI_VERSION` with the version printed by `m3 --version`. A `.venv` that
setup created for the failed run is removed, so the next `m3 setup` starts clean.

## `the bundled CLI and SDK versions do not match; reinstall sf-m3-cli`

Printed by `m3 setup`. `m3 doctor` reports the same problem as `M3 CLI VERSION:
not ready` with a `bundled SDK:` line and `Next: reinstall sf-m3-cli`. The CLI
was installed with an `sf-m3` distribution of a different version, for example
after installing another `sf-m3` into the CLI's own environment. Reinstall the
CLI:

```sh
uv tool install --reinstall sf-m3-cli
```

Then run `m3 setup` again. This differs from the project SDK mismatch above,
which `m3 setup` fixes.

## m3.toml is not a valid M3 project identity

`m3 init` found an `m3.toml` it cannot read as `schema_version = 1` with a UUID
`project_id` and a `project_name`. Restore the file from Git; creating a new
one gives the project a new identity, and earlier runs and uploads no longer
match it.
