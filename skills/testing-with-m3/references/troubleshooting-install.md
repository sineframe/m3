<!-- Generated from docs/site/troubleshooting/install.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Installation and project Python

## `project Python ... does not match`

Run `m3 setup` from the project root. It installs the SDK version matching the
standalone CLI into the selected isolated environment. If M3 selected the wrong
environment, pass `--python PATH` to `setup` and `doctor`.

Setup selects an active `VIRTUAL_ENV` or `CONDA_PREFIX` before a project
`.venv`. An active conda `base` is rejected with `active Conda base is not a
project environment; create or activate a project environment`. Activate a
project environment, deactivate `base`, or pass `--python PATH`.

## Missing pytest, SQLite, or judge support

Run `m3 setup`. A direct SDK installation needs the applicable extras, such as
`sf-m3[pytest,storage,judge]`.

## Refusing to install into a global Python

Create or activate an isolated environment, then rerun setup. M3 refuses to
modify a system/global Python.

## m3.toml is not a valid M3 project identity

`m3 init` found an `m3.toml` it cannot read as `schema_version = 1` with a UUID
`project_id` and a `project_name`. Restore the file from Git; creating a new
one gives the project a new identity, and earlier runs and uploads no longer
match it.
