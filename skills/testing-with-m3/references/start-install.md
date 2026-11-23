<!-- Generated from docs/site/start/install.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Install and update M3

Install the M3 CLI and the project SDK separately. The CLI runs `m3` and
includes the local results viewer. The SDK runs inside the Python project
being tested.

## Requirements

M3 supports Python 3.10 and newer. The commands below use
[uv](https://docs.astral.sh/uv/). If `uv --version` fails, install uv first. On
macOS or Linux:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal afterwards so `uv` is on your `PATH`. The
[uv installation page](https://docs.astral.sh/uv/getting-started/installation/)
lists Windows and package-manager installs.

The alternative shell installer supports macOS and Linux and requires `curl`
plus either uv or a Python 3.10 or newer that can create virtual environments.

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
uv when available and otherwise creates a dedicated virtual environment with
`python3 -m venv`. A stock Ubuntu or Debian system does not include that
module, so without uv the install stops with `m3 install failed: could not
create the dedicated virtual environment`. Install uv and rerun the installer,
or install your distribution's `python3-venv` package, which needs root. To
update an installation made this way, rerun the same command, then rerun
`m3 setup` in each project to upgrade its SDK.

## Install the project SDK

From a project directory, initialize M3 and prepare its Python environment:

```sh
m3 init
m3 setup
m3 doctor
```

`m3 init` asks for a project name and a suite name. When a script, CI job, or
coding agent runs it, pass both: `m3 init --project-name NAME --suite NAME`.

`m3 init` creates the project identity in `m3.toml`, a skipped pytest starter
at `tests/test_m3_starter.py`, and `.env.example`. It also installs the
`testing-with-m3` agent skill under `.agents/` and `.claude/` and records it in
`skills-lock.json`; pass `--no-skill` to `m3 init` and `m3 setup` to leave
those out. See [install the M3 agent skill](https://m3.sineframe.com/docs/guides/agents/skill).

Replace the starter with a real assertion before relying on test results. `m3 setup`
installs the SDK release matching the CLI, with pytest, storage, and judge
support, into the selected project environment. It does not install the CLI in
that environment or edit dependency manifests and lockfiles. It installs only
the SDK: a Python MCP server you want to test must be installed into the same
environment separately, as [test your own server](start-your-server.md) shows.
`m3 doctor` checks the CLI and project environment separately.

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

To upgrade the SDK in a project that manages its dependencies directly, run
`uv lock --upgrade-package sf-m3` (or the equivalent for your tool), then sync
the environment. You do not need `m3 setup` for this setup.

## Try a canary build

Canary builds let you try unreleased changes before they ship. They are
published as GitHub prereleases, never to PyPI, and are not supported releases.
`canary-main` follows the `main` branch, and `canary-pr-N` follows pull request
N while it has the `canary` label.

```sh
# Latest build of main
curl -LsSf https://m3.sineframe.com/install.sh | sh -s -- --canary

# A pull request
curl -LsSf https://m3.sineframe.com/install.sh | sh -s -- --pr 123
```

Each canary's release page, such as
[canary-main](https://github.com/sineframe/m3/releases/tag/canary-main), shows
its version and the exact install commands, including a pinned `install.sh`
link and a `uv tool install` command. `m3 --version` and `m3 doctor` name the
installed canary and its commit. `m3 setup`
installs the matching SDK from the same canary release. To return to the latest
stable release, rerun the installer without options.

Continue to [your first MCP test](getting-started.md), or [test your own server](start-your-server.md).
