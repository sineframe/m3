# M3 architecture

This document is for contributors and maintainers. End users should start with
the [root README](https://github.com/sineframe/m3/blob/main/README.md), the
[CLI guide](https://github.com/sineframe/m3/blob/main/cli/README.md), or the
[SDK documentation](https://m3.sineframe.com/docs/reference/python/).

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
the [Codex limitations section](https://m3.sineframe.com/docs/reference/python/m3/elicitation)
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

### Timing spans

The opt-in `M3_TIMINGS=1` log is fed by `m3._timing` decorators and spans.

- Instrument shared code paths only. Never add spans to an individual harness
  adapter; adapter time shows up under `harness.open` and `session.turn`.
- Prefer `@_timing.timed("name")` when the whole function is the step, and a
  single `with _timing.span("name", key=...)` when it is a portion of one.
- Use `_timing.counted` or `_timing.count` for anything called per event or per
  MCP frame, so no record is written per call.
- Pass `key` as a callable when computing it costs anything, and keep keys and
  notes to names, ids, kinds and counts. Never record prompts, arguments,
  paths with user data, or credentials.
- Step names are part of the public surface. Renaming one means updating the
  timings guide as well.

See the package-specific READMEs for implementation commands and the
[release guide](releasing.md) for versioned artifacts.

### Owned preparation

Agent session workspace creation uses `WorkspaceManager.create_async`. On
POSIX, copying, read-only materialization, git worktree creation and source
snapshotting run in private preparation jobs. The ordinary empty temporary
workspace has no source reads or background writer and retains its small
synchronous default path. Supplying an absolute total deadline routes even
that empty workspace through an owned job.

`PreparationSupervisor` is a kit facade with event-loop-local futures.
`PreparationWorker` owns one helper for the worker process across kits and
event loops, including the private loop threads used by synchronous kits. Its
thread routes bounded versioned JSON frames and owns process handles; it never
copies, publishes or deletes files. Pytest run teardown calls
`shutdown_worker_preparation`; standalone use has the same explicit teardown
seam and ownership-channel EOF at process exit. Closing a kit fences only that
kit's jobs. Submission EOF fences work without closing the separate ownership
channel: cleanup identities still reach the router and require its acknowledgement.
Ownership EOF fences running cleanup and never releases a new mutation gate;
dead-writer roots then remain explicit quarantine.

The isolated helper remains single-threaded and forks each job into its own
process group. The child closes inherited controller and sibling descriptors.
The helper first waits for a private acknowledgement that `setsid()` completed;
until then it owns and signals the unreaped child PID. The child waits at a
start gate until the router has recorded the acknowledged group identity.
Fencing before acknowledgement never opens that gate.
Staging allocation and framework-owned materialization happen in that child. Before a
result is ready, the helper joins the child and checks that its entire group
has disappeared. The controller checks the same absolute deadline through
ready, provisional acceptance and release. It then adopts the completed root
without another suspension. Git worktrees retain their staged absolute path
so their repository registration remains valid. Under an explicit total budget,
worktree preparation rejects executable Git hooks, filter commands, external
fsmonitor and partial-clone/promisor checkout before checkout. Per-command native
hook/fsmonitor guards also apply after that capability check. Plain Git worktrees
remain supported, and rejected preparation preserves the source and its worktree
registrations. The unbounded compatibility path retains custom extension behavior
and does not claim descendant containment for code that creates a new session.

Cancellation fences the generation before requesting stop. The helper sends
TERM, escalates to KILL and retains a quarantined root while any group member
survives. Recursive deletion runs in another owned process after writer death;
cancelled git worktrees unregister only their exact owned target. EOF fences
unreleased jobs. Every cleanup group uses the same established-group acknowledgement
and router start gate as a materialization group, including after submission EOF.
An unexpected helper loss also kills groups whose identities
were recorded before their start gates opened and the helper session containing
unacknowledged children. Unannounced children observe start-gate EOF and cannot
materialize. Roots remain quarantine evidence. A kernel IO wait may outlive KILL; callers return within
the stop budget while the worker owner retains that group. Neither late
results nor closing event loops permit publication or unsafe deletion.

The deadline integration seam is
`WorkspaceManager.create_async(supervisor, deadline=absolute_monotonic_time)`.
The shared worker and kit facades can host further framework-owned operations.
Native runtime installation, OpenCode catalog preparation and artifact
capture/cleanup still have existing thread-based call sites in
`agent_session.py`, `runtime/core.py` and `workspace.py`; total startup deadline
integration must convert or reject those operations before claiming bounded
ownership for the complete preparation flow. MCP capture already owns async
observer tasks and proxy processes, but its synchronous handoff/config writes
also need that integration audit. Arbitrary uncancellable callbacks are not a
bounded preparation contract.

Private capability capsule creation, protected reads/unlink and synchronous
IPC setup require a typed owned-operation provider on both POSIX and Windows
to enforce finite bootstrap deadlines. Those operations currently lack that
provider. The generic preparation result dictionary must not carry capability
bytes. Late filesystem completion must not publish a capability after fencing.

Equivalent descendant ownership on Windows requires native Job Objects and
is currently unsupported. Existing default Windows workspace behavior is retained;
a requested bounded workspace preparation fails before allocating or spawning.
The POSIX lifecycle tests in `sdk/tests/unit/test_preparation.py` cover real
stalled writers, ignored TERM, group descendants, controller EOF, inherited
descriptors, late results, quarantine, provisional acceptance, exact git
registration cleanup, multiple kits/loops and worker failure.
