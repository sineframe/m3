<!-- Generated from docs/site/start/install.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Install and update M3

Install the M3 CLI and the project SDK separately. The CLI runs `m3` and
includes the local results viewer. The SDK runs inside the Python project
being tested.

## Requirements

M3 supports Python 3.10 and newer. The commands below use
[uv](https://docs.astral.sh/uv/). The alternative shell installer supports
macOS and Linux and requires `curl` plus either Python 3 or uv.

## Install the CLI

Install the standalone command once for your user with uv:

```sh
uv tool install sf-m3-cli
```

The CLI runs outside your project environment. To update it, use:

```sh
uv tool upgrade sf-m3-cli
```

After `uv tool upgrade sf-m3-cli`, rerun `m3 setup` to upgrade the project SDK.

On macOS or Linux, you can use the shell installer instead:

```sh
curl -LsSf https://m3.sineframe.com/install.sh | sh
```

The installer selects the latest stable GitHub release, verifies its release
installer and checksums, and installs the CLI in isolated tool storage. It uses
uv when available and otherwise creates a dedicated virtual environment. To
update an installation made this way, rerun the same command.

## Install the project SDK

From a project directory, initialize M3 and prepare its Python environment:

```sh
m3 init
m3 setup
m3 doctor
```

`m3 init` creates the project identity and a skipped pytest starter. Replace
that starter with a real assertion before relying on test results. `m3 setup`
installs the SDK release matching the CLI, with pytest, storage, and judge
support, into the [selected project environment](#choose-the-project-environment). It does not install the CLI in
that environment or edit dependency manifests and lockfiles. `m3 doctor` checks
the CLI and project environment separately.

Keep the CLI and project SDK on matching releases. If the environment is
recreated or synchronized, run `m3 setup` again. For a project that manages its
dependencies directly, add `sf-m3[pytest,storage,judge]` as a project dependency instead of
using CLI-managed setup; you still install the standalone CLI separately when
you want `m3 test`, saved history, or the bundled viewer. `m3 test` checks that
the project environment can import pytest support, SQLite storage, and the
`openai` package used by `LLMJudge`; `sf-m3[pytest]` alone is enough only for
plain pytest without the CLI.

With uv, add the SDK from the project root with:

```sh
uv add "sf-m3[pytest,storage,judge]"
```

This declares the SDK in the project manifest. The standalone CLI remains a
separate user-level installation.

## Choose the project environment

The CLI is separate from the project environment, so `m3 setup`, `m3 doctor`,
and `m3 test` each pick a Python interpreter for the project. They check the
same sources in this order and use the first one that applies:

1. `--python PATH`, accepted by all three commands. A bare name such as
   `python3.12` is looked up on `PATH`.
2. The active `VIRTUAL_ENV`.
3. The active `CONDA_PREFIX`.
4. `.venv` in the project root.
5. Last resort, when nothing above applies:
   - `m3 setup` creates `.venv` in the project root, with uv when available
     and `python -m venv` otherwise.
   - `m3 test` falls back to `python3`, then `python`, on `PATH`.
   - `m3 doctor` has no fallback and reports `no project environment is
     configured; run m3 setup`.

Run `m3 doctor` to see the choice: it prints `project environment source` and
`project Python` for the interpreter it selected. To work in another
environment, activate it or pass `--python PATH` to each command:

```sh
m3 setup --python /path/to/env/bin/python
m3 doctor --python /path/to/env/bin/python
m3 test --python /path/to/env/bin/python
```

A few differences between the commands matter when the choice is not what you
expected:

- `m3 setup` installs only into an isolated environment. It refuses a system
  or global Python, Python older than 3.10, and an active Conda `base`
  environment (`CONDA_DEFAULT_ENV=base`). `m3 test` and `m3 doctor` do not
  apply those checks; they only verify that the interpreter can import M3, so
  they accept any interpreter that has the matching SDK installed.
- If `VIRTUAL_ENV` or `CONDA_PREFIX` points at an environment with no Python
  executable, `m3 setup` stops with `active VIRTUAL_ENV environment is
  unavailable`. `m3 test` and `m3 doctor` skip that variable and continue
  with the project `.venv`. When there is no `.venv`, `m3 test` continues to
  the system fallback above, and `m3 doctor` stops with `the active project
  environment is unavailable`.
- Because `m3 test` can fall back to the system Python on a project without
  `.venv`, it can report missing M3 packages for an interpreter you never
  chose, while `m3 doctor` reports that no environment is configured. Run
  `m3 setup` in both cases.

For the exact messages these checks produce, see
[Installation and project Python](troubleshooting-install.md).

Continue to [your first MCP test](getting-started.md), or [test your own server](start-your-server.md).
