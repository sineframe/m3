<!-- Generated from docs/site/troubleshooting/install.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Installation and project Python

M3 picks the project environment from `--python`, `VIRTUAL_ENV`,
`CONDA_PREFIX`, then the project `.venv`, and the commands differ when nothing
applies.
[Choose the project environment](start-install.md#choose-the-project-environment)
gives the full order. When the project environment is ready, `m3 doctor` prints
the interpreter it checked as `project Python`.

## `project m3 version ... does not match CLI SDK version ...; install matching versions`

Printed by `m3 test` and `m3 doctor`. Run `m3 setup` from the project root. It
installs the SDK version matching the standalone CLI into the selected isolated
environment. If M3 selected the wrong environment, pass `--python PATH` to
`setup`, `test`, and `doctor`. A Conda environment is the exception for `setup`:
activate it instead, because `m3 setup --python PATH` refuses it (see
[the global Python entry](#refusing-to-install-into-a-system-or-global-python-use-an-isolated-environment)).

## `no project environment is configured; run m3 setup`

Printed by `m3 doctor` when there is no `--python`, no active `VIRTUAL_ENV` or
`CONDA_PREFIX`, and no `.venv` in the project root. Run `m3 setup` from the
project root, which creates `.venv`. `m3 test` does not stop here. It falls back
to `python3` on `PATH` and usually then fails with the next message. If no
`python3` or `python` is on `PATH`, `m3 test` prints `no project Python was
found; pass --python PATH`.

## `the project Python is missing required M3 packages`

The full message lists the missing names and ends with `run m3 setup in the
project`. `m3 test` and `m3 doctor` try to import each name in the project
Python, and the missing ones are listed. What each name needs:

- `pytest`: pytest itself, from the `pytest` extra.
- `m3`: the SDK package `sf-m3`.
- `m3.pytest_plugin`: M3's pytest plugin, a module of `sf-m3` that imports
  pytest. It needs `sf-m3` with the `pytest` extra.
- `openai`: the `openai` package behind `LLMJudge`, from the `judge` extra.
- `SQLiteExecutionStore`: M3's SQLite storage, imported from `m3.storage`; the
  `storage` extra supplies its SQLAlchemy dependency.

Run `m3 setup`. A direct SDK installation needs the applicable extras, such as
`sf-m3[pytest,storage,judge]`.

If you never created an environment, `m3 test` may be checking a system Python.
See [Choose the project environment](start-install.md#choose-the-project-environment).

## `the project m3 distribution version could not be determined`

Printed by `m3 test` and `m3 doctor`. The project Python can import `m3` and
the other packages, but `importlib.metadata.version("sf-m3")` returns no
version there, for example because the installed `sf-m3` metadata has no
`Version` field. `m3 doctor` shows it as the reason for `project environment:
not ready`. A project Python with no `sf-m3` distribution at all fails earlier,
with the missing-packages message above, because `import m3` itself reads the
version. Delete the environment and run `m3 setup`, or reinstall the SDK in it
with the commands under
[`could not install the matching M3 SDK from PyPI`](#could-not-install-the-matching-m3-sdk-from-pypi),
adding `--reinstall` to the uv command or `--force-reinstall` to the pip
command.

## `active Conda base is not a project environment; create or activate a project environment`

Printed by `m3 setup` when there is no `--python`, no `VIRTUAL_ENV` is set,
`CONDA_PREFIX` is set, and `CONDA_DEFAULT_ENV` is `base` in any letter case
(`BASE` also matches). The check does not look at whether `CONDA_PREFIX` exists.
With a `VIRTUAL_ENV` set, setup never reaches this check: a usable one is
selected, and an unusable one stops with the `VIRTUAL_ENV` message below.
Activate a venv or a Conda environment other than `base`, or run `conda
deactivate`. `--python PATH` works only for an interpreter of a venv, not for a
Conda environment (see
[the global Python entry](#refusing-to-install-into-a-system-or-global-python-use-an-isolated-environment)).

## `active VIRTUAL_ENV environment is unavailable`

Printed by `m3 setup` (and `active CONDA_PREFIX environment is unavailable` for
Conda) when the variable points at a directory with no Python executable, for
example a deleted environment in a shell that is still activated. Run
`deactivate`, or recreate the environment. `m3 test` skips such a variable and
moves to the next source, while `m3 doctor` falls back to a working `.venv` or
prints `the active project environment is unavailable` when there is none. See
[Choose the project environment](start-install.md#choose-the-project-environment).

## `refusing to install into a system or global Python; use an isolated environment`

Create or activate an isolated environment, then rerun setup. M3 refuses to
modify a system/global Python. This check applies to `m3 setup` only, including
`m3 setup --python PATH`.

The same message appears when `--python` points at a Conda environment's
interpreter. A Conda environment has `sys.prefix` equal to `sys.base_prefix`,
like a global Python. `m3 setup` trusts it only when it is the active
`CONDA_PREFIX` and setup picked it without `--python`, so
`m3 setup --python ~/miniconda3/envs/proj/bin/python` is refused, even with
`proj` active. Run `conda activate proj` and then `m3 setup` without
`--python`. `m3 doctor --python` and `m3 test --python` accept the same
interpreter.

## `Python 3.10 or newer is required`

Printed by `m3 setup`. Pass `--python PATH` with the interpreter of a Python
3.10 or newer venv, or activate a 3.10 or newer environment.

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
to see the installer's error. Use the first form when uv is installed and the
second form otherwise:

```sh
uv pip install --python /path/to/env/bin/python "sf-m3[pytest,storage,judge]==CLI_VERSION"
/path/to/env/bin/python -m pip install "sf-m3[pytest,storage,judge]==CLI_VERSION"
```

Replace `/path/to/env/bin/python` with the Python of the environment that setup
selected, and `CLI_VERSION` with the version printed by `m3 --version`. A
`.venv` that setup created for the failed run is removed, so the next `m3 setup`
starts clean.

## `project environment did not pass the M3 SDK checks`

Printed by `m3 setup` after the `[3/3] Verifying project environment` step. The
installer exited successfully, but the environment still fails the import and
version checks that `m3 test` and `m3 doctor` apply: `pytest`, `m3`,
`m3.pytest_plugin`, `openai`, and `SQLiteExecutionStore` must import, and the
installed `sf-m3` version must equal the CLI's. Setup does not say which check
failed. Run `m3 doctor --python /path/to/env/bin/python` with the Python of the
environment shown on the `Project environment:` line; its `reason:` line names
the missing packages or the version mismatch. A `.venv` that setup created for
this run is removed. Setup does not remove an environment you selected.

## `could not create the project environment`

Printed by `m3 setup` after the `[1/3] Creating project environment` step, when
no environment is selected and setup has to create `.venv` in the project root.
Setup hides the tool output. The message covers a `.venv` parent directory that
cannot be created, and a `uv venv` (when uv is installed) or `python -m venv`
(run with the interpreter that runs the CLI) command that cannot be started,
exits with an error, or runs longer than 120 seconds. Run the command by hand
in the project root to see the error:

```sh
uv venv .venv
python3 -m venv .venv
```

Then run `m3 setup` again. It selects the `.venv` you created. You can also
pass `--python PATH` to an existing venv.

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

## `the CLI installation is incomplete; reinstall sf-m3-cli`

Printed by `m3 doctor` when the CLI's own environment has no installed metadata
for `sf-m3-cli` or `sf-m3`, for example when the CLI runs from copied source
files instead of an installed package. `m3 setup` prints `the bundled M3 SDK
version is unavailable; reinstall sf-m3-cli` for the same condition. This is
about the CLI environment, not the project environment. Reinstall the CLI:

```sh
uv tool install --reinstall sf-m3-cli
```

## m3.toml is not a valid M3 project identity

`m3 init` found an `m3.toml` it cannot read as `schema_version = 1` with a UUID
`project_id` and a `project_name`. Restore the file from Git; creating a new
one gives the project a new identity, and earlier runs and uploads no longer
match it.
