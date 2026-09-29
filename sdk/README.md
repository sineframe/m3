# M3 Python SDK

`m3 setup` includes response judges in the project environment. See
[`docs/evaluations.md`](docs/evaluations.md) for
helper and registered evaluator usage, credential separation, persistence, and
request caps. Installing the standalone CLI does not install project Python
dependencies.

`m3` is the public Python SDK for testing MCP servers and verifying how
agent harnesses use their tools. It provides direct MCP clients, pytest
integration, agent sessions, matrices, typed traces, assertions, and optional
persistent storage.

## Install the SDK

Install from PyPI in the project being tested:

```sh
uv add "sf-m3[pytest,judge]"
```

The SDK requires Python 3.10 or newer. The standalone CLI is optional; install
it separately only when you want the `m3` command or bundled UI, as
described in the [quick start](docs/quick-start.md#install-the-standalone-cli).

## Choose an import

Start with the package root for the usual synchronous test workflow:

```python
from m3 import MCPTestKit, expect
from m3.types import StdioServer
```

When a test needs a focused part of the SDK, use its corresponding module:

| Need | Import from |
| --- | --- |
| Run synchronous tests and assertions | `m3` |
| Define servers, harnesses, and result values | `m3.types` |
| Use the asynchronous test kit | `m3.async_api` |
| Inspect typed traces | `m3.observability` |
| Build repeated server or harness cases | `m3.matrix` |
| Define and run evaluations | `m3.evaluations` |
| Use mock and replay servers | `m3.testing` |
| Persist executions in SQLite | `m3.storage` |

## Learn the SDK

- [Streamable HTTP](docs/http.md) — test a deployed MCP endpoint
  directly, through an agent session, or with a harness matrix.
- [Quick start](docs/quick-start.md) — install, write, and run the first MCP
  test with Streamable HTTP or the local stdio alternative.
- [Concepts](docs/concepts.md) — server bindings, lifecycle, results, schemas,
  workflows, traces, optional persistence, and isolation.
- [Examples](docs/examples.md) — executable Streamable HTTP, local stdio,
  and evaluation examples.
- [Evaluations](docs/evaluations.md) — explicit built-in and custom verdicts,
  repeated harness trials, saved SQLite records, and aggregate pass rates.

The examples use only public SDK APIs and run as ordinary pytest tests.

## Managed native harnesses

Native Claude Code, OpenCode, Codex, and Pi agents can use a harness release
managed by M3. Set `runtime="managed"` and an exact `version` on the harness
specification, or omit the version to resolve `latest` once per test invocation.
For example:

```python
from m3.types import OpenCode

harness = OpenCode(
    model="opencode/big-pickle", runtime="managed", version="1.18.30"
)
```

Both `MCPTestKit` and `AsyncMCPTestKit` accept `harness_cache_dir=...` to
override the per-user cache. M3 resolves and verifies the selected release
before adapter startup, retains it while the agent runs, and includes the
requested model and resolved runtime identity in snapshots, reports, and
trace views. The system runtime remains the default.

M3 chooses the CLI asset for the machine running the Python process. The
default cache is `~/Library/Caches/m3/harnesses` on macOS,
`${XDG_CACHE_HOME:-~/.cache}/m3/harnesses` on Linux, and
`%LOCALAPPDATA%/m3/harnesses` on Windows (falling back to
`~/AppData/Local/m3/harnesses`). Set `M3_HARNESS_CACHE_DIR` or the kit
constructor argument to override it. Installations are grouped under separate
harness, version, target, and digest directories. `m3 runtime cache list`
shows cached releases; `m3 runtime cache prune` removes them when no run is
using them. M3 verifies the release digest and cache receipt before launch,
rejects unsupported targets and unsafe archives, and reports setup failures
on the selected test. Managed mode uses an isolated executable and writable
runtime state; it is not an operating system sandbox.
For GitHub API rate limits, set `M3_GITHUB_TOKEN`; M3 sends it only to
`api.github.com` metadata requests and removes it on redirects.

## Develop the SDK

From the repository root, install the workspace with `just setup`, then run the
SDK suite directly:

```bash
uv run --project sdk --extra pytest --group typecheck pytest sdk/tests
```

Run all workspace suites with `just test` and compile/import checks with
`just check`. Product installation and UI troubleshooting belong in the
[CLI guide](../cli/README.md); internal viewer/API development belongs in the
[App guide](../app/README.md).
