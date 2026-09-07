# MCP Pal Python SDK

`mcp-pal` is the public Python SDK for testing MCP servers and verifying how
agent harnesses use their tools. It provides direct MCP clients, pytest
integration, agent sessions, matrices, typed traces, assertions, and optional
persistent storage.

## Install the SDK

Add the SDK and pytest support to the project being tested:

```bash
uv add "mcp-pal[pytest]"
```

With pip:

```bash
python -m pip install "mcp-pal[pytest]"
```

The SDK requires Python 3.10 or newer. This project dependency does not install
the standalone `mcp-pal` command or bundled UI. Those come from the separate
machine-level CLI installation described in the
[quick start](docs/quick-start.md#install-the-standalone-cli).

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
  saved SQLite records, and turn-scoped evaluation patterns.

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
