# M3 CLI runner

The public CLI includes `m3 ui`. Use `m3 --help`
and `m3 COMMAND --help` for exact options; `m3 --version` and separate
`m3 report`/`m3 compare` commands are unavailable.

| Command | Use |
|---|---|
| `m3 init` | Create `m3.toml`, a skipped pytest starter, and `.env.example` |
| `m3 setup` | Install the matching SDK into the selected project Python |
| `m3 doctor` | Check project setup, optionally with `--json` or `--require` |
| `m3 test` | Run pytest, save feedback, and optionally compare with `--baseline` or open `--ui` |
| `m3 ui` | Open saved test runs from the current directory without running pytest |

Run `init` before expecting a complete project result from `doctor`. If a
shell already has `VIRTUAL_ENV` or `CONDA_PREFIX` set, `setup` selects that
environment before a project `.venv`. Check `m3 doctor --json` after setup;
use `--python PATH` for an intentional override. For a reproducible project,
declare the SDK release in its dependency manifest too: `m3 setup` does not
edit the manifest or lockfile.

Use `m3 test --env-file .env --harness opencode=opencode/big-pickle
--harness codex=gpt-5.6-sol --trials 2 -- tests/test_shipping.py`. Known provider
variable names are `OPENCODE_API_KEY`, `OPENAI_API_KEY`, and
`ANTHROPIC_API_KEY`; custom providers use `--credential-env TARGET=SOURCE` or
the scoped `KIND:TARGET=SOURCE` form. Only names belong in flags and code.
For a judge key under a different name, use
`--credential-env judge:M3_JUDGE_API_KEY=SOURCE`.

Choose server cases with the marker's `servers=[...]` or grouped CLI flags:

```bash
m3 test --env-file .env \
  --harness opencode=opencode/big-pickle \
  --harness codex=gpt-5.6-sol \
  --server http --url https://shipping.example.com/mcp --trust public \
  --server stdio --command python --arg=-m --arg=shipping_mcp \
  --trials 2 -- tests/test_shipping.py
```

That selects eight cases. CLI server groups replace the whole marker server
list, including any marker trust settings. A nonlocal HTTP agent case needs
explicit `--trust public` or `--trust trusted_private`; the default
`untrusted` label cannot be exposed to an agent. A `localhost` or literal
loopback URL without explicit trust is allowed only if every resolved address
is loopback.

The project SDK and standalone CLI have different installation scopes.

To compare harness releases, use `m3 test --runtime=managed
--harness opencode@1.18.30=opencode/big-pickle
--harness opencode@1.18.31=opencode/big-pickle -- tests/test_shipping.py`.
The CLI reports cache hits or download progress before each selected test
starts. An unversioned managed selection resolves `latest` once per run.
The default system runtime uses the existing machine installation. Use
`--harness-cache-dir PATH` or `M3_HARNESS_CACHE_DIR` for a different cache root.

`m3 setup` installs the project SDK with judge support. Rerun setup to upgrade
an environment created by an older CLI.

The CLI itself does not provide the project's Python imports. Keep
`OPENCODE_API_KEY` for the agent and `M3_JUDGE_API_KEY` for the judge; pass both
with `m3 test --env-file .env`.

| Component | Install scope | Provides |
|---|---|---|
| M3 SDK | Project environment via `m3 setup` | Python SDK, pytest, SQLite, and judge support |
| M3 CLI | Machine-level isolated environment | `m3` command and bundled UI |

Adding the SDK with `uv add` or `pip install` does not install the CLI. Install
the CLI from PyPI:

```bash
uv tool install sf-m3-cli
```

Or use the shell installer on macOS or Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/sineframe/m3/main/scripts/install-latest.sh | sh
```

The installer keeps the CLI and bundled UI outside the project environment.

## Prepare a project

From the project root, run:

```bash
m3 init --project-name YOUR_REPOSITORY --suite mcp-behavior
m3 setup
m3 doctor
```

`init` creates a committed `m3.toml` identity, one skipped starter test at
`tests/test_m3_starter.py`, and a credential-name template in `.env.example`.
Copy the template to `.env` if it is absent; otherwise add only the needed
keys to the existing `.env`. Add `.env` to `.gitignore` if needed, and pass
`--env-file .env` to `m3 test`. The template contains blank `OPENCODE_API_KEY`,
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `M3_JUDGE_API_KEY` entries. It does
not install the SDK or create the results database. Replace the method's TODO
with a test grounded in the real
server contract and remove its skip before treating the run as a behavior
check. Both name flags suppress interactive questions for an agent. A person
running `m3 init` without flags receives the questions one by one.
Repeating it preserves existing files and creates `.env.example` if missing.

`m3 setup` installs the exact matching SDK with pytest, storage, and judge support into
the selected project environment. It does not install the CLI there and does
not edit the project's dependency manifest or lockfile. The CLI and project
SDK versions must match; do not work around a mismatch by bypassing `doctor`.

Once the project is ready, run a narrow test with normal pytest feedback:

```bash
m3 test -- tests/test_shipping.py
```

Select a named suite using the existing marker in each suite file:

```python
import pytest
pytestmark = pytest.mark.m3(suite_name="catalog")
```

```bash
m3 test --suite=catalog -- tests
m3 test --suite catalog -- tests/catalog_tools.py tests/catalog_prompts.py
```

Suite selection is intersected with paths, `-k`, `-m`, harnesses, and trials
before agent expansion. Add `--harness` and `--trials` to select agent
combinations when the marked tests provide the required agent configuration.
An unknown suite exits 5; blank suite input exits 2.
The printed run ID can be reused with `--baseline RUN_ID`.

`m3 test` always adds both `-p m3.pytest_plugin` and
`--results-db PATH` to the child pytest command. The plugin installs a
default `SQLiteExecutionStore` for `MCPTestKit` instances that did not receive
an explicit store. The default database is `.m3/executions.sqlite` below
the project root; select another with the CLI's `--results-db` option:

```bash
m3 test --results-db /tmp/m3-runs.sqlite -- tests/test_shipping.py
```

Use `m3 test --ui -- tests/test_shipping.py` when the user wants to run
pytest and then keep the local viewer open. To inspect old runs without
starting any new tests, run `m3 ui` from the directory containing the existing
`.m3/executions.sqlite` database. It prints a tokenized link to `/reports`
after the server starts, and stays open until interrupted. `m3 ui` neither
creates a database nor searches parent directories, and has only the optional
`--port PORT` flag; change directories first if history is elsewhere.
Everything after `--` in `m3 test` is forwarded to pytest. Direct pytest
remains valid when the standalone CLI is not needed:

```bash
uv run pytest tests/test_shipping.py
```

That direct command uses in-memory SDK execution storage by default. A project
that wants saved history without the standalone CLI can either pass
`SQLiteExecutionStore` to its kit or invoke pytest with the storage plugin:

```bash
uv run pytest -p m3.pytest_plugin \
  --results-db .m3/executions.sqlite tests/test_shipping.py
```

Direct SQLite use requires the `sf-m3[storage]` extra; install
`sf-m3[pytest,storage]` when both pytest and SQLite support are needed.

The SQLite history contains SDK executions, specifications, recorded events
and traces, sessions/turns, saved artifacts/evidence, and evaluations
explicitly attached to executions. Direct SDK evaluation persistence requires
`store=SQLiteExecutionStore(path)`; the CLI/plugin flag
`--results-db PATH` selects the same saved store. Without either,
SDK storage is in memory. When the M3 plugin is active, pytest item
outcomes are saved in internal run records and M3 matcher checks are saved
as execution evaluations. Other Python assertion results and aggregate summary
rows are not saved; calculate trends from saved evaluations
with `store.aggregate_evaluations(...)`. Do not report a saved `completed`
execution as a saved passing result.
