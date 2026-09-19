# M3 Python SDK

`m3` is the public Python SDK for testing MCP servers and verifying how
agent harnesses use their tools. It provides direct MCP clients, pytest
integration, agent sessions, matrices, typed traces, assertions, and optional
persistent storage.

## Install the SDK

Choose a release version and add the SDK wheel with pytest support to the
project being tested:

```bash
VERSION=X.Y.Z
uv add \
  "m3[pytest] @ https://github.com/sineframe/m3/releases/download/v${VERSION}/m3-${VERSION}-py3-none-any.whl"
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
