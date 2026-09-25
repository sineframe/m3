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
Missing names fail collection with the affected test IDs. Pytest runs without
persistence and direct SDK executions do not require a suite name.

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

### Managed harness runtimes

The default `--runtime=system` launches the harness already installed on the
machine. A version selector requires `--runtime=managed`. With managed mode,
an unversioned harness requests `latest`; M3 resolves it once for the run and
shares that pinned version across pytest workers. Explicit versions are
reused until their verified cache entry is removed.

```sh
m3 test --runtime=managed \
  --harness opencode@1.18.30=opencode/big-pickle \
  --harness opencode@1.18.31=opencode/big-pickle \
  -- tests/test_shipping.py
```

Harness setup starts when the selected test runs. The test waits for the
requested release to be resolved, downloaded, checked, and installed, or for
an existing cache entry to be checked. The CLI prints resolution, download,
verification, and ready states; cache hits are shown as loaded from cache.
Setup failures fail the affected test. Each execution records the selected
model, requested harness selector, resolved harness version, target, and
download digest. Matrix reports distinguish different resolved versions.

The default cache root is `~/Library/Caches/m3/harnesses` on macOS,
`${XDG_CACHE_HOME:-~/.cache}/m3/harnesses` on Linux, and
`%LOCALAPPDATA%/m3/harnesses` on Windows (falling back to
`~/AppData/Local/m3/harnesses`). Entries are grouped by harness,
version, target, and digest. Set `M3_HARNESS_CACHE_DIR`, pass
`--harness-cache-dir PATH`, or use the SDK's `harness_cache_dir` argument to
override the root. The SDK constructor takes precedence for SDK calls; the
CLI flag takes precedence over the environment variable for CLI runs. Keep
the cache outside the tested repository and system executable directories.
M3 runs the cached executable by absolute path without a global install or
PATH change.

Inspect or remove cached releases with `m3 runtime cache list` and
`m3 runtime cache prune`. Pruning waits for active leases for up to 10 seconds
and returns an operational error if a release remains in use.

Known provider variable names are:

| Route | Variable name |
| --- | --- |
| OpenCode or Pi with `opencode/` models | `OPENCODE_API_KEY` |
| Codex, OpenCode, or Pi with `openai/` models | `OPENAI_API_KEY` |
| Claude Code, OpenCode, or Pi with `anthropic/` models | `ANTHROPIC_API_KEY` |
| Pi with `openai-codex/` models | `PI_CODING_AGENT_DIR` |
| `m3.judges.LLMJudge` | `M3_JUDGE_API_KEY` by default, or explicit `api_key_env` |

For example, put `OPENAI_API_KEY` for an agent using OpenAI and
`M3_JUDGE_API_KEY` for the judge in the same `.env` file, then run
`m3 test --env-file .env`. The judge does not fall back to the agent key.
If the judge key is already named `MY_JUDGE_KEY`, use
`--credential-env judge:M3_JUDGE_API_KEY=MY_JUDGE_KEY`. The explicit mapping
overrides `M3_JUDGE_API_KEY` for that test run. A missing source stops the run
before tests execute.

Custom providers use names only, for example
`--credential-env VENDOR_API_KEY=MY_VENDOR_KEY`; use
`--credential-env opencode:VENDOR_API_KEY=MY_VENDOR_KEY` to scope a mapping.
Unscoped mappings apply to harnesses, not judges. Only names appear in flags
and test code. `.env` is read only when
`--env-file` is supplied, and ambient variables take precedence. `doctor
--env-file` checks configuration and does not provide credentials to a later
test command.

Use `LLMJudge(api_key_env=...)` to select a different judge variable.
`--judge-max-requests N` caps attempts including retries. Custom endpoints
require explicit `api_key_env` and `response_mode`; loopback `auth="none"`
reads no key and sends no `Authorization` header. Judges use Chat Completions,
so the endpoint and model must support the selected response mode.

The CLI runs pytest using the project Python. It discovers that Python in this
order:

1. `--python PATH`, when supplied;
2. the `VIRTUAL_ENV` Python;
3. the `CONDA_PREFIX` Python;
4. `<project-root>/.venv/bin/python` (or `Scripts/python.exe` on Windows);
5. `python3`, then `python` on `PATH`.

The selected environment must contain `pytest`, the SDK pytest plugin, SQLite
storage, judge support, and `m3` at exactly the same version as the CLI's SDK.
Run setup in the project to install these together:

```sh
m3 setup
```

The default results database is `.m3/executions.sqlite` below the project
root. Override it when needed:

```sh
m3 test --results-db /tmp/my-runs.sqlite -- -q tests
m3 test --python .venv/bin/python -- -q --maxfail=1
m3 test --baseline run-123 -- -q tests
```

`--baseline RUN_ID` is the only feedback comparison option. It validates the
explicit run ID in the selected results database before pytest starts. A run
through the plugin writes `.m3/reports/<run-id>/feedback.json`; the bundle
contains the saved test manifest and references to detailed executions and
catalogs. Existing pytest output, including test prints and logs, remains
diagnostic output and is not interpreted as a score.

Use `--execution-timeout 30` to bound startup, the harness turn, and cleanup for
each selected execution. A timeout writes a partial trace with the observed
stage and operation into the execution and trace JSON files under the feedback
bundle. `handle.result(timeout=...)` in SDK code remains a wait-only timeout.
Provider credentials still come from the child test environment: use
`--env-file .env` or ambient variables, and use `--credential-env TARGET=SOURCE`
when the provider variable has a project-specific name. Values are never put in
the timeout summary or feedback paths.

Everything after `--` is passed to pytest unchanged. The CLI adds its storage
plugin and `--results-db` option itself. In other words, SQLite
execution persistence is automatic whenever tests run through `m3 test`;
tests run directly with pytest use in-memory SDK storage unless they pass an
explicit `SQLiteExecutionStore` or install the plugin and flag themselves.

Profiles saved in the UI are stored in that same results database. A later
CLI-managed test can reference one directly; no additional CLI option is
needed. Use the profile ID shown by the UI and explicitly select the server
inside its MCP document:

```python
from m3 import MCPTestKit
from m3.types import RevisionSelection, ServerBinding, ServerProfileRef

saved_server = ServerBinding(
    profile=ServerProfileRef(
        profile_id="profile-from-ui",
        server_name="orders",
        revision=RevisionSelection(mode="latest"),
    )
)

def test_saved_server_profile():
    with MCPTestKit() as kit, kit.direct(saved_server) as client:
        assert client.list_all_tools()
```

At execution start, `latest` resolves to one immutable revision and its profile
and revision IDs are retained with the execution. Use a pinned
`RevisionSelection` when the test must name an exact revision. The same shared
store resolves `HarnessProfileRef` in agent specifications. This automatic
store selection applies to `m3 test` (including `--ui`); plain `pytest`
must be given the same `SQLiteExecutionStore` explicitly.

The database records SDK executions, specifications, recorded events and
traces, sessions/turns, saved artifacts/evidence, evaluations attached to
those executions, and internal pytest run records. Direct SDK evaluations are saved only with
`store=SQLiteExecutionStore(path)`; `m3 test` selects the equivalent
store through `--results-db`. In-memory SDK storage is temporary.
When the M3 pytest plugin is active, pytest item outcomes, phase
diagnostics, and execution associations are saved in internal run records.
M3 matcher checks are saved as evaluation records on their associated
executions. Ordinary `print()` and logging output remain diagnostic text; they
are never parsed into a score. Aggregate matrix/trial trends are calculated from
saved evaluations with the SDK store or the API v2 aggregate route. A
completed execution is still not by itself a saved test-pass result.

Each run also writes an agent-readable JSON bundle to
`.m3/reports/<run-id>/feedback.json`. Pass `--baseline RUN_ID` to add a
read-only comparison. The JSON is deterministic for the saved run, and normal
pytest results remain visible alongside the M3 run ID and feedback path.

### CI and publishing

`m3 ci test` runs the normal test selection except tests marked
`pytest.mark.m3(ci=False)`. It remains local unless `--upload` is supplied.
With `--upload`, it publishes completed passing and failing reports using
`M3_ACCESS_TOKEN`, and an upload failure fails an otherwise passing job.
`m3 upload RUN_ID` retries a saved run without rerunning tests.

`m3 auth login` opens the M3 control-plane sign-in page in your browser and
returns to the waiting CLI through a temporary loopback callback. If you choose
to create a developer token on the hosted page, the CLI saves it in the OS
credential store. Otherwise, any saved token is left unchanged. CI jobs set
`M3_ACCESS_TOKEN` as a secret rather than opening a browser. Harness and judge
keys are separate.
See the [CI and credentials guide](../docs/ci.md) for the complete flow and
GitHub Actions example. The transport contract is documented in the
control-plane repository.

### `--ui`

Add `--ui` to keep a local viewer open after pytest finishes:

```sh
m3 test --ui
```

### `ui` (saved history only)

From the directory where earlier `m3 test` commands saved their results, run:

```sh
m3 ui
# If port 8000 is busy:
m3 ui --port 8123
```

This does not run tests, resolve the project Python, or create a results
database. It requires an existing, readable M3 database at
`.m3/executions.sqlite` **in the current directory**. It does not search
parent directories or accept `--project-root` or `--results-db`; if you run
it from `~`, `/`, or another directory without M3 history, it exits with a
message asking you to change to the correct directory. A valid database
with no runs opens an empty runs index.

After the server is ready, `m3 ui` waits one second and opens the saved-runs
index in your default browser. It also prints a tokenized
`http://127.0.0.1:8000/reports#m3_token=...` link in case the browser cannot
be opened automatically. It does not select the latest run. Press Ctrl+C to
stop serving.
`m3 test --results-db PATH` can write to another database, but `m3 ui`
currently views only the default database. Opening the UI itself does not
start an execution, although the full UI still offers controls that can
start one later.

### UI server and security

This starts one FastAPI server and one loopback port. The production UI is
bundled inside the CLI wheel; Node.js, npm, and Vite are not run at runtime.
`m3 test --ui` waits one second after the server is ready, then opens the
newest newly stored report in your default browser. It prints a link for each
newly stored report like this:

```text
Run label: Run #42
Run: http://127.0.0.1:8000/reports/runs/<runId>#m3_token=<token>
```

The `<runId>` in the direct link is the pytest run ID stored in the test-run
manifest and returned by `/api/v2/feedback/<runId>`. The adjacent label is the
human-facing name; the `Run:` URL line remains unchanged for tools that parse it.
The CLI stays open so the
browser can load results; press Ctrl+C to stop it. A normal pytest failure
still opens the UI and keeps that pytest exit code. Collection/configuration
errors, interruption, server startup errors, and invalid configuration return
an operational failure.

If the test run saves no results, the browser opens the saved-runs index and
the CLI prints `No new stored runs.` and its index link. If opening the browser
fails (for example, on a headless machine), use a printed link from the current
CLI process to authorize the browser. The browser removes the token fragment
from its address bar and keeps the token in the current tab's session storage
for API requests. A new CLI launch uses a new token, so old links stop working.
Treat the printed links as credentials while the server is running.

When stdout is an interactive terminal, the SDK plugin shows a compact test
progress bar. It is disabled for non-TTY output and for verbose pytest modes,
where pytest's normal output remains available.

The server binds only to `127.0.0.1` and requires the launch token for API
requests. It is intended for local use; do not expose it through a public
interface or reverse proxy without suitable network controls.

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
