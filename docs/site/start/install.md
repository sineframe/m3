# Install and update M3

Install the M3 CLI and the project SDK separately. The CLI runs `m3` and
includes the local results viewer. The SDK runs inside the Python project
being tested.

## Requirements

M3 supports Python 3.10 and newer. The commands below use [uv](https://docs.astral.sh/uv/).

## Install the CLI

Install the standalone command once for your user:

```sh
uv tool install sf-m3-cli
```

The CLI runs outside your project environment. To update it, use:

```sh
uv tool upgrade sf-m3-cli
```

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
support, into the selected project environment. It does not install the CLI in
that environment or edit dependency manifests and lockfiles. `m3 doctor` checks
the CLI and project environment separately.

Keep the CLI and project SDK on matching releases. If the environment is
recreated or synchronized, run `m3 setup` again. For a project that manages its
dependencies directly, add `sf-m3[pytest]` as a project dependency instead of
using CLI-managed setup; you still install the standalone CLI separately when
you want `m3 test`, saved history, or the bundled viewer.

With uv, add the SDK from the project root with:

```sh
uv add "sf-m3[pytest]"
```

This declares the SDK in the project manifest. The standalone CLI remains a
separate user-level installation.

Continue to [your first MCP test](/getting-started), or [test your own server](/start/your-server).
