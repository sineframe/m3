---
title: "Installation and project Python"
description: "Run m3 setup from the project root. It installs the SDK version matching the standalone CLI into the selected isolated environment. If M3 selected the wrong environment, pass --python PATH to setup and doctor."
---

# Installation and project Python

## The shell installer reports HTTP 403

`curl: (22) The requested URL returned error: 403` followed by `could not list
M3 releases through the GitHub API` means GitHub refused the installer's
request for the release list. GitHub limits requests without a token per IP
address, so this shows up on shared addresses such as CI runners and office
networks.

The installer then continues without the release list: it installs the latest
final release, or the exact build named by `--tag`, `--canary`, or `--pr`.
`--prerelease` needs the list and stops.

To avoid the limit, set `M3_GITHUB_TOKEN` to a GitHub token before running the
installer. It is sent only to `api.github.com`. Installing with
`uv tool install sf-m3-cli` does not use the GitHub API at all.

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
