# M3 CLI

`m3` is a standalone test runner for projects that use the M3 SDK.
The command is distributed with the production web UI, so users do not need a
checkout of this repository, Node.js, Vite, or `uv` in the project
being tested.

## Install

Install the `m3` command with uv:

```sh
uv tool install sf-m3-cli
```

Or use the shell installer on macOS or Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/sineframe/m3/main/scripts/install-latest.sh | sh
```

The installer prefers `uv tool install`, and otherwise creates a dedicated
virtual environment. The machine-level CLI and bundled UI stay isolated from
global Python and every project environment.

## Set up a project

After installing the CLI, initialize the project and answer its two short
questions:

```sh
cd my-project
m3 init
m3 setup
m3 doctor
# Replace the starter TODO with a real assertion and remove its skip.
m3 test --suite mcp-behavior -- tests/test_m3_starter.py
```

`init` defaults the project name to the repository name and the suite name to
`mcp-behavior`. It creates `m3.toml` (the stable project identity) and a
single skipped starter test at `tests/test_m3_starter.py`. Running `init`
also creates `.env.example` with blank `OPENCODE_API_KEY`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, and `M3_JUDGE_API_KEY` entries. Copy it to `.env` if you
do not already have one; otherwise add only the keys your tests need. Add
`.env` to `.gitignore` if needed and pass `--env-file .env` to `m3 test`. Running
`init` again preserves existing files and adds `.env.example` if it is
missing. The first skipped run confirms collection but exits 1 under `m3 test`
because no test executed. Replace the starter before using the run as a CI gate.

`m3 setup` installs the matching SDK with pytest, storage, and judge support
into the project environment. It selects `--python`, then an active `VIRTUAL_ENV` or
`CONDA_PREFIX`, then `.venv`, creating `.venv` when needed. It never installs
the CLI or bundled app there, never edits dependency manifests or lockfiles,
and verifies the exact SDK version installed from PyPI. Judge support is
included. If you recreate or sync the environment, run setup again. Rerunning setup also adds judge support to an older
project environment.

For an isolated pip environment without the `m3` command, install the SDK directly:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install "sf-m3[pytest,judge]"
```


## Commands

The public commands include:

```text
m3 --version
m3 init
m3 setup [options]
m3 doctor
m3 test [options] -- [pytest arguments]
m3 ci test [options] -- [pytest arguments]
m3 upload RUN_ID [--env-file PATH]
m3 auth login
m3 auth status
m3 auth logout
m3 ui [--port PORT]
```

Use `m3 test --ui` to run pytest and then view its results. Use `m3 ui` to
view previously saved test runs without running pytest.
Arguments after `--` go to pytest, except M3-owned options and pytest `@` response
files; pass M3 options before `--` so the CLI can track the exact run and scan
mapped credentials before upload.

### `init`

Run `m3 init` from the repository you want to test. It asks for the
project name, then the suite name, showing a default for each. It creates the
identity file, skipped pytest starter, and `.env.example` template. If the
identity and starter already exist, it reports the existing project and adds
the template only when missing. If one of those two required files is
missing or the identity is invalid, it reports the partial state for repair.

### `doctor`

`doctor` reports the standalone CLI and project environment independently. A
missing project environment is a normal `not ready` result with `m3 setup`
as remediation; invalid interpreters and invalid configuration are operational
errors. It also checks explicitly requested capabilities:

```sh
m3 doctor
m3 doctor --require storage:sqlite
m3 doctor --python .venv/bin/python --project-root . --json
```

### `test`

Agent selection flags:

| Flag | Meaning |
| --- | --- |
| `--harness KIND[@VERSION]=MODEL[,MODEL...]` | repeatable harness, optional version, and model selection |
| `--runtime=system\|managed` | use the installed harness (default) or the managed cache |
| `--harness-cache-dir PATH` | override the per-user harness cache for this run |
| `--trials N` | independent executions per combination |
| `--execution-timeout SECONDS` | full deadline for each selected execution; each case has its own deadline |
| `--env-file PATH` | explicitly load provider variables for the pytest child |
| `--credential-env [KIND:]TARGET=SOURCE` | map an agent credential; use `judge:TARGET=SOURCE` for the judge |
| `--suite NAME` or `--suite=NAME` | select tests whose inherited `m3` marker has this suite name |

Server selection flags:

| Flag | Meaning |
| --- | --- |
| `--server http` or `--server stdio` | start one server case; repeat for alternatives |
| `--url URL` | Streamable HTTP MCP endpoint for the current HTTP case |
| `--command CMD` and repeated `--arg VALUE` | executable and arguments for the current stdio case; write `--arg=-m` for a leading dash |
| `--trust public\|trusted_private\|untrusted` | trust for the current HTTP case |
| `--name NAME` | optional server name; defaults to `server` |

For two harnesses, two servers, and two trials, M3 runs eight executions:

```sh
m3 test --env-file .env \
  --harness opencode=opencode/big-pickle \
  --harness codex=gpt-5.6-sol \
  --server http --url https://shipping.example.com/mcp --trust public \
  --server stdio --command python --arg=-m --arg=shipping_mcp \
  --trials 2 -- tests/test_shipping.py
```

In a marked test, request `server` alongside `agent` and pass it to
`agent.run(..., server=server)`. The marker can instead declare
`servers=[{"type": "http", "url": "https://shipping.example.com/mcp",
"trust": "public"}]`. If CLI server groups are present, they replace the
whole marker list; no marker fields, including trust, carry over. A nonlocal
HTTP endpoint defaults to `untrusted`, so an agent test needs explicit
`public` or `trusted_private` trust. `localhost` and literal loopback IPs
default to loopback-only private trust and fail if any resolved address is
not loopback. Direct tests may use a public endpoint with default trust.

Suite selection happens during pytest collection, before agent expansion. Put
the same existing marker in each file belonging to one suite:

```python
import pytest
pytestmark = pytest.mark.m3(suite_name="catalog")
```

When persisting pytest results with `m3 test` or `--results-db`, every selected
test needs a non-empty `suite_name` on its own or an inherited `m3` marker.
Missing names fail collection with the affected test IDs. Tests deselected by
pytest (`-k`, `-m`, or `--suite`) and `--collect-only` runs do not need a name.
Pytest runs without persistence and direct SDK executions do not require a
suite name, even if a Python script or notebook explicitly uses SQLite.

Combine `--suite` with `--harness`, `--trials`, paths, `-k`, and `-m`; every
selector must match. An unknown suite exits 5 and a blank value exits 2. The
selected run ID is printed and can be passed to `--baseline RUN_ID`.

For example, two selections and two trials produce four agent executions:

```sh
m3 test --env-file .env --harness opencode=opencode/big-pickle \
  --harness codex=gpt-5.6-sol --trials 2 -- tests/test_shipping.py
```

Mark a test with `@pytest.mark.m3(suite_name="catalog")` and request the `agent` fixture. The
CLI supplies its harnesses and models. A marker may instead set defaults with
`@pytest.mark.m3(suite_name="catalog", agents=[...], trials=2)`. CLI `--harness` replaces those
defaults; CLI `--trials` replaces the marker's trial count. Ordinary tests
without an `agent` fixture still run once. For an agent test, the item count is
ordinary pytest cases × selected harness/model choices × trials.

## Related guides

- [Harness runtimes](/cli/harnesses)
- [Results and local UI](/cli/results-and-ui)
- [CI and report publishing](/ci)

## Troubleshooting

- `project Python ... does not match`: install the same `m3` version as
  the CLI, including the `[pytest,storage,judge]` extras, by running `m3 setup`
  in the project.
- `pytest`, SQLite, or judge support is missing: run `m3 setup` in the project
  to install the matching SDK and extras.
- `port is already in use`: select another port with `--port 8123`.
- No UI bundle is available: reinstall the CLI release; development checkouts
  do not contain generated UI files.
- The UI shows no runs: confirm the test uses `MCPTestKit` and that the CLI
  results database is the same file passed to the API. Runs are written by
  the SDK's default storage plugin; the CLI never writes test results itself.
- `m3 ui` reports no saved history: run it from the directory containing
  `.m3/executions.sqlite`. An existing but invalid or unrelated SQLite file
  is rejected rather than initialized as new M3 history.
