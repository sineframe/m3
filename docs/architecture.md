# M3 architecture

This document is for contributors and maintainers. End users should start with
the [root README](../README.md), the [CLI guide](../cli/README.md), or the
[SDK documentation](../sdk/docs/README.md).

## Product boundary

M3 has two user-facing entry points that run in separate environments:

```text
project under test
└── m3 SDK + pytest plugin ── MCP server or agent harness

machine environment
└── m3 CLI (optional) ────── selects the project Python, SQLite history,
                                  internal app, and local viewer
```

The SDK is installed in the project being tested. The standalone CLI is a
separate machine-level installation: it selects the project's Python,
launches pytest, supplies the persistence plugin, and serves the bundled SPA.
The CLI installs the internal application package transitively, but users do
not install or interact with that package directly.

## Workspace packages

The `uv` workspace contains three distributions with deliberately separate
responsibilities:

| Directory | Distribution | Responsibility |
| --- | --- | --- |
| [`sdk/`](../sdk/) | `sf-m3` | Public Python API, direct MCP clients, agent sessions, harness adapters, traces, assertions, pytest integration, and storage interfaces. |
| [`cli/`](../cli/) | `sf-m3-cli` | The `m3` command, project-environment discovery, pytest supervision, release packaging, and the bundled browser UI. |
| [`app/`](../app/) | `sf-m3-app` | FastAPI services and API adapters packaged with the standalone CLI. |

The CLI depends on the SDK and app at matching versions. A project that uses
only the library does not need the CLI or the app.

## Execution lifecycle

`MCPTestKit` and `AsyncMCPTestKit` own the execution boundary and cleanup. A
test describes a server or agent execution, opens a direct client or session,
records protocol and lifecycle events, and receives typed results. Closing the
client finalizes its trace; closing the kit releases the surrounding runtime.

The same public SDK contracts are used by direct Python tests, the pytest
plugin, and the app's v2 execution service. This keeps assertions and captured
observability consistent regardless of how a test is launched.

### Harness-owned elicitation retries

The Codex App Server integration is an observer and native-request responder,
not a replacement MCP client. Codex owns tool selection, dispatch, approval,
MCP retries, and cancellation. M3 passively observes the original MCP exchange,
associates a complete request round with Codex's exposed elicitation prompts,
and responds through Codex's existing server-request resolution method only
when the association is unambiguous. Original traffic continues through the
configured MCP transport. Capture failure, missing identity, or ambiguous
prompts fails the planned action; it never triggers a synthetic call or retry.

The observer's terminal barrier covers events already accepted by its manager,
including scheduled thread-safe callbacks, while a separate bounded wait
checks for the expected retry that Codex sends. The barrier cannot flush an
event still queued in the child-side relay. Codex also omits the MCP request
key, reverses multi-prompt order in observed cases, normalizes accepted URL
responses to empty content, and supports at most nine MRTR prompts in tested
version 0.156.1. The canonical details and evolving verification state live in
the [Codex limitations section](../sdk/docs/elicitation-api.md#codex-app-server-support-and-limitations)
and [Pi-to-Codex parity inventory](../sdk/tests/mrtr-harness-parity.md).

## Persistence and feedback

SDK storage is in memory unless the caller selects a store explicitly. The
SQLite store persists execution specifications and snapshots, events and
traces, sessions and turns, artifact/evidence references, and evaluations. The
pytest plugin can select that store with `--results-db`; the CLI uses
the equivalent project-local database by default.

The plugin also records pytest run metadata and writes a deterministic feedback
bundle under `.m3/reports/<run-id>/feedback.json`. A baseline comparison
reads an existing run and does not mutate it. Ordinary pytest output and Python
assertions remain normal diagnostics and test results; they are not inferred
as M3 evaluations.

## API and browser viewer

The app exposes the current, supported versioned v2 API over the loopback
server started by the CLI's `--ui` mode. It also retains deprecated v1
compatibility routes for existing callers while migrations are completed. The
API uses the SDK's execution, trace, evaluation, and storage contracts. The
compiled SPA is packaged with the CLI release and is served by that same local
process; Node.js, npm, and Vite are build-time concerns only.

The server binds to `127.0.0.1` for local use. The CLI keeps the process alive
after pytest so the browser can load history and run details. A pytest failure
still preserves pytest's exit status while making its captured run available
to the viewer.

## Local development

Install [`uv`](https://docs.astral.sh/uv/) and [`just`](https://just.systems/),
then use the repository recipes:

```bash
just setup
just test-all
just lint
just format-check
```

Use `just format` to modify Python files in place. `just --list` shows focused
tests, type checks, packaging checks, and local API/UI commands. The setup
recipe also installs the pre-push hook; `just install-hooks` can install it
separately. Live provider tests and the browser gate are opt-in because they
require external credentials and may incur provider costs.

## Contributor guidance

- Keep public execution and result behavior in the SDK; adapt it at the app or
  CLI boundary rather than duplicating lifecycle logic.
- Treat SQLite persistence as an explicit SDK capability. Do not make direct
  SDK use depend on the application package.
- Keep the CLI and SDK installation boundaries intact: the CLI is optional for
  SDK users, and users interact with the app through the CLI rather than
  installing or calling its internal APIs directly.
- Preserve the bundled SPA contract when changing the CLI web server or app API.
- Run the deterministic suite and formatting/type-check gates before pushing;
  use the live recipes only when credentials and external services are
  intentionally available.

See the package-specific READMEs for implementation commands and the
[release guide](releasing.md) for versioned artifacts.
