# MCP Pal CLI runner

Use `mcp-pal test --env-file .env --harness opencode=opencode/big-pickle
--harness codex=gpt-5.6-sol --trials 2 -- tests/test_shipping.py`. Known provider
variable names are `OPENCODE_API_KEY`, `OPENAI_API_KEY`, and
`ANTHROPIC_API_KEY`; custom providers use `--credential-env TARGET=SOURCE` or
the scoped `KIND:TARGET=SOURCE` form. Only names belong in flags and code.

The project SDK and standalone CLI have different installation scopes:

| Component | Install scope | Provides |
|---|---|---|
| `mcp-pal[pytest]` | Project environment | Python SDK and pytest support |
| MCP Pal CLI | Machine-level isolated environment | `mcp-pal` command and bundled UI |

Adding the SDK with `uv add` or `pip install` does not install the CLI. The CLI
project is distributed as `mcp-pal-cli`, but install it from an exact release
with the release installer rather than adding it to the tested project.

## Install the CLI

The repository is private. Check GitHub CLI authentication first:

```bash
gh auth status
```

If authentication is missing, ask the user to complete `gh auth login`; do not
attempt to automate an interactive login. Determine the exact release tag from
the user, the project's required SDK version, or the repository's releases.
Do not guess a version, and do not silently replace an existing CLI with a
different release.

```bash
gh release list --repo mcppal/mcp-pal --limit 10
```

For a project that already pins MCP Pal, select the matching release. For a new
setup without a pinned version, use the most recent intended release from this
list; releases are currently prereleases, so do not rely on GitHub's implicit
`latest` release selection.

On macOS or Linux, replace `X.Y.Z` with that exact version:

```bash
gh release download vX.Y.Z --repo mcppal/mcp-pal \
  --pattern install.sh --output install.sh
sh install.sh
rm install.sh
```

On Windows PowerShell:

```powershell
gh release download vX.Y.Z --repo mcppal/mcp-pal `
  --pattern install.ps1 --output install.ps1
.\install.ps1
Remove-Item install.ps1
```

The installer keeps the CLI and bundled UI outside the project environment. It
prefers `uv tool install` and otherwise creates a dedicated virtual
environment.

## Prepare a project

From the project root, run:

```bash
mcp-pal init --project-name YOUR_REPOSITORY --suite mcp-behavior
mcp-pal setup
mcp-pal doctor
```

`init` creates a committed `mcp-pal.toml` identity and one skipped starter test
at `tests/test_mcp_pal_starter.py`; it does not install the SDK or create the
results database. Replace the method's TODO with a test grounded in the real
server contract and remove its skip before treating the run as a behavior
check. Both name flags suppress interactive questions for an agent. A person
running `mcp-pal init` without flags receives the questions one by one.
Repeating it on a complete project reports the existing identity and changes
nothing.

`mcp-pal setup` installs the exact matching `mcp-pal[pytest,storage]` SDK into
the selected project environment. It does not install the CLI there and does
not edit the project's dependency manifest or lockfile. The CLI and project
SDK versions must match; do not work around a mismatch by bypassing `doctor`.

Once the project is ready, run a narrow test with normal pytest feedback:

```bash
mcp-pal test -- tests/test_shipping.py
```

Select a named suite using the existing marker in each suite file:

```python
import pytest
pytestmark = pytest.mark.mcp_pal(suite_name="catalog")
```

```bash
mcp-pal test --suite=catalog -- tests
mcp-pal test --suite catalog -- tests/catalog_tools.py tests/catalog_prompts.py
```

Suite selection is intersected with paths, `-k`, `-m`, harnesses, and trials
before agent expansion. Add `--harness` and `--trials` to select agent
combinations when the marked tests provide the required agent configuration.
An unknown suite exits 5; blank suite input exits 2.
The printed run ID can be reused with `--baseline RUN_ID`.

`mcp-pal test` always adds both `-p mcp_pal.pytest_plugin` and
`--mcp-pal-results-db PATH` to the child pytest command. The plugin installs a
default `SQLiteExecutionStore` for `MCPTestKit` instances that did not receive
an explicit store. The default database is `.mcp-pal/executions.sqlite` below
the project root; select another with the CLI's `--results-db` option:

```bash
mcp-pal test --results-db /tmp/mcp-pal-runs.sqlite -- tests/test_shipping.py
```

Use `mcp-pal test --ui -- tests/test_shipping.py` only when the user wants the
local viewer. It stays open after pytest finishes until interrupted. Everything
after `--` is forwarded to pytest. Direct pytest remains valid when the
standalone CLI is not needed:

```bash
uv run pytest tests/test_shipping.py
```

That direct command uses in-memory SDK execution storage by default. A project
that wants saved history without the standalone CLI can either pass
`SQLiteExecutionStore` to its kit or invoke pytest with the storage plugin:

```bash
uv run pytest -p mcp_pal.pytest_plugin \
  --mcp-pal-results-db .mcp-pal/executions.sqlite tests/test_shipping.py
```

Direct SQLite use requires the `mcp-pal[storage]` extra; install
`mcp-pal[pytest,storage]` when both pytest and SQLite support are needed.

The SQLite history contains SDK executions, specifications, recorded events
and traces, sessions/turns, saved artifacts/evidence, and evaluations
explicitly attached to executions. Direct SDK evaluation persistence requires
`store=SQLiteExecutionStore(path)`; the CLI/plugin flag
`--mcp-pal-results-db PATH` selects the same saved store. Without either,
SDK storage is in memory. When the MCP Pal plugin is active, pytest item
outcomes are saved in internal run records and MCP Pal matcher checks are saved
as execution evaluations. Other Python assertion results and aggregate summary
rows are not saved; calculate trends from saved evaluations
with `store.aggregate_evaluations(...)`. Do not report a saved `completed`
execution as a saved passing result.
