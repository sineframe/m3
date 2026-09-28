# Harness runtimes

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
