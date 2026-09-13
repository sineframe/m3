# MCP Pal Python SDK

`mcp-pal` is the public Python SDK for testing MCP servers and verifying how
agent harnesses use their tools. It provides direct MCP clients, pytest
integration, agent sessions, matrices, typed traces, assertions, and optional
persistent storage.

## Install the SDK

Choose a release version and add the SDK wheel with pytest support to the
project being tested:

```bash
VERSION=X.Y.Z
uv add \
  "mcp-pal[pytest] @ https://github.com/mcppal/mcp-pal/releases/download/v${VERSION}/mcp_pal-${VERSION}-py3-none-any.whl"
```

The SDK requires Python 3.10 or newer. The standalone CLI is optional; install
it separately only when you want the `mcp-pal` command or bundled UI, as
described in the [quick start](docs/quick-start.md#install-the-standalone-cli).

## Choose an import

Start with the package root for the usual synchronous test workflow:

```python
from mcp_pal import MCPTestKit, expect
from mcp_pal.types import StdioServer
```

When a test needs a focused part of the SDK, use its corresponding module:

| Need | Import from |
| --- | --- |
| Run synchronous tests and assertions | `mcp_pal` |
| Define servers, harnesses, and result values | `mcp_pal.types` |
| Use the asynchronous test kit | `mcp_pal.async_api` |
| Inspect typed traces | `mcp_pal.observability` |
| Build repeated server or harness cases | `mcp_pal.matrix` |
| Define and run evaluations | `mcp_pal.evaluations` |
| Use mock and replay servers | `mcp_pal.testing` |
| Persist executions in SQLite | `mcp_pal.storage` |

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
