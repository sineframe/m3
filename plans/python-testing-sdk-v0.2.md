# MCP Pal v0.2 — Python SDK for Testing MCP Servers

## Summary

Build MCP Pal around a published, framework-neutral Python SDK that becomes the single source of truth for direct MCP protocol testing, agent-driven multi-turn testing, execution management, tracing, persistence, assertions, and evaluation.

The existing FastAPI application becomes a thin serialization adapter over the SDK. Streamlit uses the SDK directly and retains its current behavior, including complete traces for successful, failed, timed-out, cancelled, and interrupted runs.

SDK users write normal Python and pytest tests. There will be no JSON/YAML scenario authoring format. A thin `mcp-pal test` command will wrap pytest without replacing its discovery, fixtures, parametrization, plugins, reporting, or exit codes.

Deliver the work through runnable milestones:

1. `0.2.0a1`: package foundation, domain models, direct MCP testing, tracing, persistence, assertions, pytest foundation.
2. `0.2.0a2`: multi-turn agent runtime, multiple servers, harness adapters, policies, workspace isolation, capture.
3. `0.2.0a3`: `/api/v2` adapter, execution workers, Streamlit migration to direct SDK use, fresh database schema.
4. `0.2.0rc1`: complete documentation, reference pytest corpus, deterministic and live E2E suites, packaging and release validation.
5. `0.2.0`: stable only after all acceptance gates pass.

Each milestone must leave the repository installable, testable, and runnable. Preparing release metadata and publishing automation is in scope; uploading to TestPyPI or PyPI, creating release tags, and configuring external Trusted Publishing are separate explicitly authorized release actions.

## 1. Repository and package architecture

### Project layout

Convert the repository into a uv workspace with one published project under `sdk/`:

- `sdk/src/mcp_pal`: the complete published package.
- `sdk/tests`: unit, integration, contract, API, UI-support, packaging, and deterministic E2E tests.
- `sdk/examples`: real pytest files that MCP authors can copy and that CI executes against an installed wheel.
- `sdk/docs`: versioned Diátaxis documentation, ADRs, API reference, schemas, and executable examples.
- Root-level orchestration remains limited to workspace configuration, the `justfile`, CI, and project-wide development commands.

Use Hatchling for builds and one authoritative version in package metadata. Runtime version reporting, documentation, and CLI output must obtain it through `importlib.metadata`, not duplicated constants.

### Dependency boundaries

Publish `mcp-pal` with Python `>=3.10` and conservative upper bounds for dependencies that expose unstable public APIs.

Dependency extras:

- Base package: core SDK, official MCP v2 client, direct testing, built-in Claude Code/OpenCode/ACP adapters, capture, typed models, assertions, in-memory operation.
- `mcp-pal[pytest]`: pytest plugin and test-report integrations.
- `mcp-pal[storage]`: SQLAlchemy-backed SQLite persistence and filesystem blob storage.
- `mcp-pal[app]`: storage plus FastAPI, Uvicorn, Streamlit, and application dependencies.
- `mcp-pal[property]`: Hypothesis schema-driven strategies.
- `mcp-pal[docs]`: MkDocs Material, mkdocstrings, and documentation validation.
- `mcp-pal[all]`: all supported optional capabilities.

Use the official MCP Python SDK as `mcp>=2,<3`. Importing `mcp_pal` must not initialize databases, inspect binaries, load `.env`, create event loops, start workers, or perform network access.

Support and document both uv and pip:

```bash
uv add "mcp-pal[pytest]"
uv run pytest
uv run mcp-pal test

pip install "mcp-pal[pytest]"
pytest
mcp-pal test
```

Ship `py.typed`, runtime schemas, and the Apache-2.0 license in the wheel. Include examples and documentation in the source distribution and repository, but not in the wheel.

### Public module organization

Expose common synchronous symbols from `mcp_pal`, with complete explicit surfaces under:

- `mcp_pal.sync_api`
- `mcp_pal.async_api`
- `mcp_pal.types`
- `mcp_pal.matchers`
- `mcp_pal.testing`
- `mcp_pal.pytest_plugin`

Use frozen Pydantic models for public specifications, snapshots, events, results, evaluations, and errors. Controllers such as kits, clients, sessions, handles, and workers remain mutable lifecycle objects.

All public exports must be tracked in a manifest checked by CI and represented in the generated API documentation.

## 2. Public SDK design

### Test-kit lifecycle

Provide synchronous and asynchronous twin APIs:

```python
from mcp_pal import MCPTestKit, expect

with MCPTestKit() as kit:
    with kit.direct(server) as client:
        tools = client.list_tools()
        result = client.call_tool("add", {"a": 2, "b": 3})
        expect(result).to_have_structured_content({"value": 5})
```

```python
from mcp_pal.async_api import AsyncMCPTestKit

async with AsyncMCPTestKit() as kit:
    async with kit.direct(server) as client:
        result = await client.call_tool("add", {"a": 2, "b": 3})
```

`MCPTestKit` and `AsyncMCPTestKit` must support context managers and explicit `close()`/`aclose()`. Closing waits for owned sessions, MCP processes, proxies, readers, workers, and storage writes. Cleanup failures are recorded in the terminal result and raised when appropriate.

Configuration precedence is:

1. Explicit constructor or method arguments.
2. MCP Pal environment variables.
3. `[tool.mcp-pal]` in `pyproject.toml`.
4. SDK defaults.

Library imports never load `.env`. The application and CLI may load a user-selected `.env` explicitly.

Expose typed capability inspection through `kit.capabilities()`, including transport, harness, binary, protocol, storage, and optional-feature readiness with reasons and detected versions.

Expose larger domains through services rather than an ever-growing kit object:

- `kit.server_profiles`
- `kit.harness_profiles`
- `kit.executions`
- `kit.probes`

### Server definitions

Support:

- In-process MCP applications.
- Stdio servers.
- Streamable HTTP servers.
- Legacy SSE servers.
- Trusted private direct endpoints.
- SDK-hosted loopback endpoints for connecting agent harnesses to in-process servers.

`InProcessServer(name, factory=...)` is runtime-only. Persist and report its descriptor and origin, but never pickle its factory or accept it through the JSON API.

Serializable server profiles contain transport configuration, command or endpoint information, environment references, authentication references, revision identity, and trust classification. Secrets are resolved only at execution time and never copied into snapshots.

Required servers failing startup terminate execution preflight. Servers explicitly declared optional remain visible as unavailable and do not block the session.

### Direct MCP client

The asynchronous implementation uses the official MCP v2 `Client` directly. The synchronous implementation runs the same engine behind a generated, lifecycle-safe sync proxy; it must not expose an async client across threads.

Expose:

- Initialization and negotiated protocol information.
- Server identity, instructions, capabilities, extensions, and transport details.
- Tools, resources, resource templates, prompts, completions, logging, progress, roots, sampling, elicitation, subscriptions, notifications, ping, cancellation, and any additional capability exposed by the pinned official client.
- Page-level methods and convenience `list_all_*` methods for paginated collections.
- Convenient typed wrapper fields while preserving the official response at `.raw`.

Direct calls return typed results. MCP tool errors represented by `is_error` remain normal tool-call results. JSON-RPC failures raise a typed `ProtocolError`. Transport and infrastructure exceptions include partial trace evidence.

Default protocol negotiation is automatic. Tests can explicitly constrain revisions or run revision and transport matrices.

In-process direct testing defaults to `raise_server_exceptions=True` for useful test failures. Setting it to false exercises real protocol sanitization behavior.

### Execution specifications

Provide two public frozen, serializable specification families:

- `DirectExecutionSpec`
- `AgentExecutionSpec`

These are ordinary Pydantic Python objects used by `kit.run(...)`, storage, the UI, and the API. They are not scenario files and are not a parallel test-discovery system.

Specifications capture effective non-secret configuration, server and harness revision references, protocol or transport constraints, timeouts, goal, evaluation registrations, artifact collection rules, workspace policy, tool policy, and user metadata.

`kit.run(spec)` performs a complete one-shot execution. Interactive agent work uses a session.

### Multi-turn agent sessions

Provide:

```python
with kit.agent_session(spec) as session:
    first = session.send("Store the value 42 using the memory server.")
    second = session.send("Retrieve the value you stored.")
    expect(second).to_have_text_containing("42")

result = session.result
```

The session must preserve across turns:

- The same agent conversation.
- The same harness process or server.
- The same MCP processes and connections.
- Server-side state.
- Workspace state.
- Tool and permission policy.
- Model, harness, server, and transport configuration.

Harness behavior:

- Claude Code: one isolated stream-JSON input/output process for the full conversation. No resume-based approximation. If streaming multi-turn support is unavailable, the harness is not ready.
- OpenCode: one isolated `opencode serve` instance with sequential turns attached to the same session and persistent MCP connections.
- ACP: one process, connection, ACP session ID, MCP configuration, and capture context across all sends.

The session’s model, server set, workspace, harness, tool policy, permission policy, and operating mode are immutable after opening. Each turn may vary only its message, timeout, and metadata.

`AgentSession.send(...)` accepts either text or a typed `UserMessage`. Supported message blocks include text, file/path references, images, audio, MCP resource links, and explicitly typed opaque provider attachments. Unsupported content fails preflight; it is never silently discarded or stringified.

Concurrent `send()` calls raise `SessionBusy`. `enqueue_turn()` is the explicit serialized alternative.

A tool-level MCP error does not close the session. Session-wide timeout, cancellation, harness termination, transport loss, or cleanup failure makes the session terminal.

`.snapshot()` is available while live. `.result` is terminal-only and otherwise raises `SessionStillOpen`.

Portable session forking is represented as a new execution with explicit replay inputs and provenance. Do not claim that replay is the same live conversation. Native provider forks may be exposed only as nonportable low-level operations.

### Harness, workspace, and policy objects

Represent harnesses as typed values such as `ClaudeCode(model=...)`, `OpenCode(model=...)`, and `ACPAgent(...)`, with immutable update helpers such as `.with_model(...)`.

Readiness is capability-based and records binary and protocol versions. Do not maintain brittle allowlists or silently substitute one vendor or harness for another.

Use a portable typed `ToolPolicy`. If a harness cannot enforce a requested policy, preflight fails. Allow an explicitly nonportable native-policy escape hatch and record it in provenance.

Unrestricted tools require `FullToolPolicy(acknowledge_risk=True)`.

Provide typed handlers for:

- Permission requests.
- MCP elicitation.
- Sampling.
- Filesystem requests.
- Terminal requests.

Filesystem and terminal access default to denied unless an advanced handler explicitly permits them.

Workspace policies:

- Fresh temporary workspace by default.
- Filesystem copy.
- Git worktree.
- Read-only source view.
- Explicit in-place execution with a risk acknowledgement.

Copies and worktrees exclude `.git`, virtual environments, caches, `.env*`, credentials, and MCP Pal artifact directories by default. Risky inclusion requires an explicit override.

Capture a structured workspace diff and declared output artifacts at execution completion.

### Execution handles and streaming

`kit.submit(spec)` returns an `ExecutionHandle` containing:

- Stable execution ID.
- Submitted and effective specifications.
- Lifecycle state.
- Latest immutable snapshot.
- Ordered event stream.
- Cancellation.
- Terminal result retrieval.

Provide an asynchronous twin.

Event consumption supports sequence-based iterators, async iterators, and callbacks. Every event must be committed to the configured store before it becomes observable to consumers.

Lifecycle states:

- Nonterminal: `created`, `queued`, `starting`, `idle`, `running_turn`, `closing`.
- Terminal lifecycle: `finished`.
- Terminal outcome: `completed`, `failed`, `timed_out`, `cancelled`, `interrupted`.

Turns have their own queued/running/finished lifecycle and outcome, timing, message, response, evidence, evaluation, and artifact references.

Use UTC wall-clock timestamps plus monotonic offsets for ordering and duration.

### Results, assertions, and evaluators

Turn responses contain ordered typed content blocks and a `.text` convenience projection.

`expect(subject)` is pure and does not persist data. Each matcher call performs one assertion and returns without building a deeply mutable assertion chain.

Core matchers include:

- Exact, contains, and regex text matching.
- Ordered and unordered content-block matching.
- Tool calls by tool name, optional server name, arguments, result, status, count/range, turn, or predicate.
- Explicit negative tool-call and text assertions.
- Execution-wide and session-wide assertions across all turns.
- Lifecycle, outcome, error, timing, protocol, transport, capability, artifact, workspace-diff, and trace assertions.
- Eventual assertions for live handles using event-driven waiting.
- Canonical model serialization for use with existing snapshot libraries.

If multiple servers expose the same tool, a serverless tool assertion fails as ambiguous.

Assertion failures include structural differences, candidate calls, searched scope, turn and trace identifiers, lifecycle state, redacted excerpts, and the related artifact path.

Support grouped soft assertions:

```python
with check() as checks:
    checks.expect(turn).to_have_tool_call("lookup")
    checks.expect(turn).to_have_text_containing("answer")
```

`kit.evaluate(subject, evaluator, required=False)` runs a deterministic evaluator and persists an `EvaluationResult`.

`EvaluationContext` is immutable and contains the subject, goal, trace, artifacts, and non-secret execution metadata. Evaluation states are `passed`, `failed`, `inconclusive`, `error`, and `not_run`.

Evaluations never rewrite execution lifecycle or outcome. A required failed/error evaluation fails the containing pytest test or API operation at the outer integration layer.

No built-in LLM-as-judge is included in v0.2. Users can implement an evaluator that calls an LLM explicitly.

Canonical snapshot normalization removes run IDs, timestamps, durations, costs, paths, ports, session IDs, trace IDs, secrets, and unstable vendor metadata by default. Users may opt specific fields back in.

### Testing utilities

Publish `mcp_pal.testing` with:

- A decorator-based and stateful `MockMCPServer`.
- Scripted expected request/response sequences.
- Strict unexpected-call failure by default.
- Explicit optional, repeated, subset, unordered, and fallback expectations.
- Deterministic synchronization gates and virtual clocks.
- Fault injection for delays, hangs, disconnects, malformed payloads, protocol errors, invalid schemas, duplicate IDs, partial frames, reordered frames, oversized content, and cancellation races.
- Trace recording and strict replay.

Replay matches method, server, tool/resource/prompt identity, arguments, and ordering. Volatile IDs and timestamps are ignored. Redacted fields can be wildcards or runtime secret bindings. Every relaxation is explicit and recorded.

Optional property-testing helpers generate valid and invalid values from MCP input/output schemas when `[property]` is installed.

## 3. Tracing, persistence, workers, API, and UI

### Trace guarantees

Every execution creates an in-memory complete or partial trace, regardless of persistence mode or outcome.

The canonical normalized trace is the authoritative cross-harness representation. Preserve raw harness and MCP evidence separately for debugging. Raw evidence must never replace canonical semantic events.

Capture:

- Configuration resolution and readiness.
- Process and transport lifecycle.
- MCP initialization and negotiation.
- Requests, responses, errors, notifications, progress, and cancellation.
- Agent turns and ordered content.
- Server/tool/resource/prompt identity.
- Permission, sampling, and elicitation events.
- Explicitly emitted reasoning only.
- Evaluations, workspace changes, artifacts, cleanup, and terminal outcome.

Never infer or reconstruct hidden chain-of-thought. Mark unavailable, encrypted, or provider-hidden reasoning honestly.

Correlation uses connection identity, typed JSON-RPC ID, request sequence, direction, session, and turn. Persistence and streaming preserve canonical sequence ordering.

Apply configurable redaction before persistence, artifact export, UI rendering, API serialization, logs, and telemetry. Keep original values only in process long enough for assertions that explicitly need them.

Do not truncate canonical evidence. Move large values to content-addressed compressed blobs while retaining searchable metadata in SQLite.

### Ephemeral and persistent modes

Ephemeral test mode:

- In-memory execution/event store.
- Temporary blob directory.
- Complete traces remain available to assertions and results.
- Pytest artifact export defaults to failed tests only.
- Artifact policy is configurable as `failed`, `always`, or `never`.

Persistent application mode:

- SQLite metadata and filesystem blobs.
- Persist every execution and trace, including successful, failed, timed-out, cancelled, interrupted, and startup-failed runs.
- The Streamlit UI can always reopen and display terminal traces.
- Retain data until explicit deletion; retention automation is disabled by default.

### Fresh database schema

v0.2 uses a fresh schema only. Do not add:

- v1/v2 runtime detection.
- Legacy imports or data conversion.
- Backup-on-start.
- Alembic or migration machinery.
- Compatibility tables.
- A database-reset CLI command.
- Table-by-table legacy cleanup.

Fresh startup creates all required profile/revision, execution, turn, event, evaluation, artifact, blob-reference, lease, cancellation, and queued-command structures.

Add a guarded root recipe:

```bash
just dev-db-reset CONFIRM=reset
```

It deletes only the explicitly configured app-owned development SQLite file and its exact `-wal` and `-shm` sidecars. It must reject missing confirmation, broad paths, directories, and paths outside the configured development location. The next startup recreates the database.

### Storage behavior

Define `ExecutionStore` and `ArtifactStore` protocols. Ship:

- In-memory execution and artifact stores.
- Filesystem artifact/blob store.
- SQLite execution store with filesystem blobs.

SQLite behavior:

- Foreign keys enabled.
- WAL where supported.
- Busy timeout.
- Explicit transactions.
- Atomic claim/lease compare-and-set.
- Integrity checks.
- Events visible only after commit.
- Blob hashes and lengths verified on load.
- Blob reference counting and integrity-aware garbage collection.
- Terminal execution deletion with explicit cascading.
- Active execution deletion rejected until terminal or cancelled.

Snapshots record full effective non-secret configuration, profile names and revision IDs, capability results, dependency versions, and provenance. A `latest` profile reference resolves when the execution is created. Cloning defaults to the original resolved revision unless the caller explicitly requests latest.

### Shared workers and cancellation

Persistent execution queues, leases, heartbeats, cancel flags, and interactive turn commands live in the database so Streamlit and FastAPI processes can share one store safely.

Any persistent toolkit can enable an embedded worker. Streamlit and FastAPI enable workers by default. Atomic leases ensure only one worker owns an execution.

Queue scheduling is strict FIFO without priority in v0.2.

Cancellation is durable. The owner checks the cancellation flag during startup, turn processing, MCP calls, and cleanup. If an owner loses its lease or crashes, a later worker marks the execution interrupted with all committed partial evidence; it does not resume the agent conversation.

### FastAPI v2 adapter

Remove `/api/v1` and expose only `/api/v2`.

The API serializes the same Pydantic models used by the SDK. It may reference only registered server, harness, evaluator, and policy names; arbitrary Python callables and pickled objects are forbidden.

Provide:

- Create execution, returning `202`.
- List/get executions with cursor pagination and filtering.
- Snapshot and terminal report retrieval.
- Event snapshot and resumable SSE using canonical sequence numbers.
- Cancel and delete operations.
- Interactive agent session open, enqueue turn, snapshot, close, and cancel operations.
- Optional idempotency keys for execution and turn creation; conflicting reuse returns `409`.

Use a stable typed error envelope containing code, message, phase, retryability, execution/session/turn IDs, redacted details, and validation errors.

Generate OpenAPI from the shared models and contract-test round trips between SDK models, JSON, and OpenAPI examples.

The API remains localhost-oriented. Configurable non-loopback binding emits a warning. Production authentication and multi-user authorization are out of scope.

### Streamlit migration

`just ui` constructs and caches a persistent `MCPTestKit` directly. It does not require FastAPI or issue HTTP requests.

Retain the current v0.2 UI feature set:

- One prompt/goal per submitted execution.
- One selected server in the form.
- Profile management and revisions.
- Harness and server probes.
- Clone, cancel, history, delete, and reports.
- Trace display for every terminal outcome.
- Browser-session isolation for draft and selected UI state.

Although the UI remains one-turn/one-server, it submits the same extensible SDK specifications used by multi-turn and multi-server Python tests. Do not build the multi-turn UI in this change.

Rename `expected_output` to SDK `goal`. The UI continues requiring it and labels it “Goal / expected outcome.” The SDK keeps it optional.

Separate presentation of:

- Execution lifecycle/outcome.
- MCP activity health: `no_calls`, `all_succeeded`, `mixed`, `all_failed`.
- Explicit deterministic evaluation results.

UI tests inject an in-memory kit and fake harness. Remove HTTP/request mocking from UI tests.

## 4. Pytest plugin and CLI

### Pytest integration

Provide function-scoped fixtures:

- `mcp_test`
- `async_mcp_test`

Fixtures own cleanup and use isolated ephemeral stores, artifact directories, event-loop portals, workspaces, and port allocations.

Do not hide expensive session infrastructure inside implicit session-scoped fixtures. Provide explicit session-factory fixtures/helpers for authors who intentionally want reuse.

Register descriptive markers:

- `mcp_direct`
- `mcp_agent`
- `mcp_live`
- `mcp_transport`
- `mcp_slow`
- `mcp_e2e`

Markers classify tests but never silently skip them. A selected test that requests an unavailable required harness fails with a clear readiness report. Repository CI chooses deterministic paths/marker expressions explicitly and does not rely on plugin auto-skipping.

On failure, attach concise trace/artifact locations and identifiers to pytest terminal output. Add safe identifiers and paths as JUnit properties; do not embed full traces in XML.

Support pytest-xdist through isolated per-worker ephemeral directories, unique portals and ports, and database leases for persistent tests.

### `mcp-pal test`

Implement:

```bash
uv run mcp-pal test [PYTEST_ARGS...]
```

The command:

- Verifies that the pytest plugin is installed and loadable.
- Prints a concise SDK/plugin/artifact readiness summary.
- Applies only MCP Pal artifact/reporting defaults.
- Passes all remaining arguments to pytest unchanged.
- Preserves pytest discovery, fixtures, hooks, parametrization, plugins, coverage, JUnit, xdist, output, and exact exit code.

It must not:

- Discover custom scenario files.
- Add implicit retries.
- Change skip semantics.
- Select a harness.
- Fail globally because an unused harness is unavailable.
- Replace pytest reporting.
- Interpret JSON/YAML test definitions.

`uv run pytest` remains canonical and behaviorally equivalent.

Add strict `mcp-pal doctor` preflight for users who want to validate all requested harnesses, binaries, transports, persistence, and configuration before a test run. Preserve existing operational CLI capabilities under the consolidated CLI.

## 5. Documentation and examples

### Documentation system

Build versioned MkDocs Material documentation from `sdk/docs/` using mkdocstrings and Google-style docstrings.

Organize documentation through Diátaxis:

- Tutorials: first direct test, first deterministic agent test, multi-turn state, multiple servers, tracing, pytest setup.
- How-to guides: transports, harnesses, authentication, workspace policies, tool permissions, fault injection, replay, artifacts, storage, API deployment, UI development, CI.
- Reference: complete public API, models, exceptions, configuration, environment variables, CLI, markers, event schema, trace schema, API schema.
- Explanation: architecture, direct versus agent testing, lifecycle semantics, trace model, persistence model, isolation, redaction, evaluation philosophy.
- ADRs: SDK-first architecture, official MCP client, sync/async twins, pytest-native authoring, no scenario files, persistent-all versus export-failed traces, multi-turn harness guarantees, fresh database break, SQLite leases, separate conformance project.

Enable strict link, reference, snippet, and API-export checks. Generate concise `llms.txt` and comprehensive `llms-full.txt` so coding agents can understand and use the SDK.

Publish machine-readable JSON schemas for serializable public models and API contracts.

### README and executable examples

The SDK README stays concise:

- uv-first installation.
- pip alternative.
- Minimal direct test.
- Minimal deterministic agent test.
- Links to the full documentation and example corpus.

All examples are real pytest modules that import only the public API from an installed wheel. No example may import repository internals.

Organize examples by capability, with combined E2E journeys and explicit expected-failure examples that pass by asserting the intended error.

The first direct quickstart uses an in-process MCP server. The first agent quickstart uses a deterministic local ACP agent. Live Claude Code, OpenCode, and model-backed variants are separate and carry cost, credentials, privacy, and nondeterminism warnings.

The canonical multi-turn example writes state through an MCP tool during one turn and retrieves it during a later turn, proving that the agent conversation and MCP process survived.

The canonical multi-server example connects servers exposing overlapping tool names and asserts correct server-qualified routing.

Trace examples assert semantic events and correlation rather than unstable raw strings.

### Conformance roadmap

Official MCP conformance integration is not part of v0.2 implementation.

Add one roadmap item linking to the official [SDK integration guide](https://github.com/modelcontextprotocol/conformance/blob/main/SDK_INTEGRATION.md) and state that a separately planned wrapper will reuse upstream conformance rather than recreate it.

Do not add conformance-specific dependencies, fixtures, models, CLI behavior, CI jobs, or partial adapters now. Preserve only generic extension boundaries that are independently useful.

The later conformance design must separately decide upstream version pinning, Node runner invocation, fixture-server contracts, result preservation, CI ownership, and update policy.

## 6. Detailed test and E2E plan

### Test-layer policy

Use four explicit layers:

1. Unit tests for frozen models, configuration, matchers, normalization, state machines, and pure utilities.
2. Deterministic integration tests for transports, processes, stores, workers, API, UI integration, and fake harnesses.
3. Black-box reference E2E tests from `sdk/examples`, executed against the built and installed wheel.
4. Explicit live E2E tests for real Claude Code, OpenCode, ACP implementations, remote servers, and model providers.

Every public capability requires:

- At least one success test.
- Its relevant failure or edge case.
- Assertions covering cleanup and trace evidence.
- Documentation or a reference example when authors are expected to use it directly.

Deterministic CI must not require public internet, paid models, or user credentials. Live tests are separate commands and workflows; when explicitly selected, missing required prerequisites fail rather than silently skip.

### A. Installation and import tests

Cover:

- Install base wheel on Python 3.10, 3.11, 3.12, and 3.13.
- Install each extra independently in clean environments.
- Install `[all]`.
- Install with uv and pip.
- `import mcp_pal` with no filesystem, database, process, event-loop, or network side effects.
- Import sync and async APIs independently.
- Import pytest plugin only when its extra is installed.
- Runtime version equals wheel metadata.
- `py.typed` is included and discoverable.
- Runtime schemas and license are present.
- Examples and development-only files are absent from the wheel.
- Intended examples/docs are present in the sdist.
- Wheel and sdist pass metadata and content checks.
- Minimal direct and agent examples run from outside the repository against the installed wheel.
- Unsupported Python versions receive a clear installer rejection.

### B. Configuration and capability tests

Cover:

- Defaults with no configuration.
- `[tool.mcp-pal]` discovery.
- Environment override.
- Explicit argument override.
- Full precedence across all four levels.
- Library does not load `.env`.
- CLI/application loads only an explicitly selected `.env`.
- Invalid configuration reports the exact field and origin.
- Secret references resolve at execution time.
- Secrets never appear in snapshots, reprs, logs, API JSON, traces, or artifacts.
- `capabilities()` reports ready, unavailable, degraded, and unsupported states.
- Detected binary/protocol versions are recorded.
- Missing Claude/OpenCode/ACP binaries affect only requested harnesses.
- `doctor` succeeds for a complete requested setup and fails with actionable diagnostics otherwise.
- No vendor fallback occurs when the selected harness is unavailable.

### C. Direct MCP protocol examples and tests

Create actual author-facing pytest scenarios for:

- In-process initialization and clean shutdown.
- Stdio initialization and process cleanup.
- Streamable HTTP initialization.
- Legacy SSE initialization.
- Automatic protocol negotiation.
- Explicit protocol-revision constraints.
- Initialization rejection and partial trace.
- Server identity, instructions, capabilities, and extensions.
- Empty tool/resource/prompt collections.
- Single-page and multi-page tool listing.
- `list_all_tools()` pagination.
- Tool call with primitive arguments.
- Tool call with nested object/list arguments.
- Tool call returning text content.
- Tool call returning structured content.
- Tool call returning multiple ordered content blocks.
- Tool call returning binary/image/audio/resource-link content.
- Tool call returning `is_error=True`.
- Tool call raising a JSON-RPC protocol error.
- Tool call with invalid arguments.
- Tool result violating advertised output schema.
- Client-side structural schema expectation enabled and disabled.
- In-process server exception propagation enabled.
- In-process server exception sanitization enabled.
- Resource listing and pagination.
- Static resource read.
- Binary resource read.
- Resource template listing and expansion.
- Resource-not-found failure.
- Resource subscription, update notification, and unsubscribe.
- Prompt listing and pagination.
- Prompt retrieval with arguments.
- Missing/invalid prompt arguments.
- Completion request and response.
- Server log-message callback.
- Progress notifications and correlation.
- Server-to-client sampling callback.
- Server-to-client elicitation callback.
- Roots listing and change notification where supported.
- Ping success and timeout.
- Request cancellation initiated by the client.
- Cancellation race with a completing request.
- Unknown server capability use.
- Capability advertised but operation malformed.
- Custom extension round-trip through `.raw`.
- Typed convenience fields matching the official raw object.
- Multiple simultaneous direct clients with isolated IDs and traces.
- Transport matrix parametrization producing separate pytest cases.
- Protocol-revision matrix parametrization producing separate cases.
- User-supplied headers and bearer tokens.
- Deterministic authentication challenge fixture.
- User-supplied official OAuth implementation.
- Private direct endpoint accepted when explicitly trusted.
- Untrusted private endpoint rejected for agent exposure.
- Connection loss with partial trace.
- Stdio process exit before initialization.
- HTTP/SSE malformed event stream.
- Oversized payload stored as a blob without trace truncation.

### D. Mock server, fault, and replay tests

Provide reference tests for:

- Decorator-defined tool, resource, and prompt.
- Stateful server with state shared across calls.
- Strict expected-call ordering.
- Unexpected call failure with a useful diff.
- Optional expected calls.
- Repeated calls with bounds.
- Subset argument matching.
- Unordered expectation group.
- Explicit fallback behavior.
- Deterministic delayed response.
- Gate-controlled hung request.
- Disconnect before response.
- Partial stdio frame.
- Malformed JSON.
- Invalid JSON-RPC envelope.
- Duplicate JSON-RPC response ID.
- Reordered responses.
- Invalid advertised schema.
- Invalid structured tool result.
- Protocol error injection.
- Oversized response.
- Cancellation before dispatch.
- Cancellation during handler execution.
- Completion/cancellation race.
- Recording a direct interaction.
- Strict replay success.
- Replay mismatch on method, server, operation, arguments, or order.
- Replay with volatile IDs/timestamps normalized.
- Replay with a redacted wildcard.
- Replay with a runtime secret binding.
- Explicit relaxed replay recorded in provenance.
- Replay artifact hash and integrity verification.

### E. Multi-turn agent tests

Use a deterministic local ACP agent and stateful MCP servers to cover:

- Open session, one turn, clean close.
- Turn text shorthand.
- Typed `UserMessage`.
- Ordered mixed content blocks.
- File/path attachment.
- Image attachment.
- Audio attachment.
- Resource-link attachment.
- Provider-specific opaque attachment.
- Unsupported content rejected before harness invocation.
- First turn writes server state; second turn retrieves it.
- Three or more turns preserve the same agent conversation.
- Three or more turns preserve the same MCP server process.
- Workspace changes persist between turns.
- Tool policy remains immutable between turns.
- Model and server set remain immutable.
- Per-turn timeout and metadata vary successfully.
- MCP tool error leaves the session usable for a later turn.
- Permission denial produces a normal observable turn result where the harness supports continuation.
- Harness transport loss makes the session terminal.
- Session timeout makes it terminal.
- Cancellation makes it terminal.
- Cleanup failure is represented in the result.
- `.snapshot()` works before, during, and after a turn.
- `.result` before termination raises `SessionStillOpen`.
- `.result` after close returns the complete result.
- Concurrent `send()` raises `SessionBusy`.
- `enqueue_turn()` preserves FIFO ordering.
- Close with queued turns has defined cancellation results.
- New execution with explicit replay records its source execution.
- Replay is not mislabeled as the same live session.
- Session state is isolated from another session using the same profile.
- Two sessions can use distinct temporary homes/workspaces concurrently.
- Optional server unavailable while required server remains usable.
- Required server startup failure prevents opening.
- Agent session with no MCP call remains a valid `no_calls` execution.
- Tool error followed by successful recovery produces `mixed` activity health.
- All failed calls produce `all_failed`.
- Successful calls produce `all_succeeded`.

### F. Multiple-server and routing tests

Create author-facing tests for:

- Two servers with distinct tools.
- Two servers exposing the same tool name.
- Server-qualified tool selection.
- Ambiguous serverless assertion failure.
- Assertion scoped to a specific server.
- Same JSON-RPC ID on different connections remains correctly correlated.
- One optional server fails while another serves calls.
- One required server disconnects mid-session.
- Calls interleave across servers without trace misattribution.
- Server state persists independently across turns.
- Server profile revisions are captured separately.
- Agent cannot access an untrusted private endpoint.
- SDK-created loopback endpoint is accepted.
- Per-server authentication references resolve independently.
- Redaction rules apply consistently across every server.

### G. Harness contract and live integration tests

Define a common harness contract suite and run it against deterministic fake adapters on every pull request:

- Capability/readiness reporting.
- Process ownership and cleanup.
- One-turn execution.
- True multi-turn preservation.
- MCP server configuration applied once.
- Sequential turn delivery.
- Tool-call capture.
- Text and content-block capture.
- Cancellation.
- Timeout.
- Unexpected harness exit.
- Unsupported policy rejection.
- Unsupported attachment rejection.
- Isolated home/config/data directories.
- Plugins and project auto-discovery disabled.
- Explicit credential passthrough only.
- Locale/timezone determinism where supported.
- No silent provider fallback.
- Usage provenance values: requested, enforced, observed, unavailable.

Run the same behavioral characterization against real harnesses in separate live workflows:

- Claude Code streaming multi-turn.
- Claude Code unavailable streaming capability fails readiness.
- OpenCode server/session continuity.
- ACP process/connection/session continuity.
- Real tool call and capture for each harness.
- Real multi-server routing where supported.
- Real cancellation and timeout.
- Real cleanup with no surviving processes.
- Tool-policy enforcement characterization.
- Permission and elicitation handling.
- Usage data reported without estimation.
- Version and capability evidence stored in results.

### H. Tool policy, permission, and workspace tests

Cover:

- Default restrictive tool policy.
- Portable allowlist and denylist.
- Argument-aware policy rule.
- Harness incapable of enforcement fails preflight.
- Native policy escape hatch marked nonportable.
- `FullToolPolicy` without acknowledgement rejected.
- `FullToolPolicy(acknowledge_risk=True)` accepted.
- Default filesystem request denied.
- Explicit filesystem handler allow/deny.
- Default terminal request denied.
- Explicit terminal handler allow/deny.
- Permission handler accepted, denied, and errored.
- Elicitation handler accepted, declined, cancelled, and errored.
- Sampling handler success and failure.
- Fresh temporary workspace default.
- Filesystem copy excludes `.git`, `.env*`, credentials, venvs, caches, and artifacts.
- Risky excluded-file inclusion requires acknowledgement.
- Git-worktree creation and cleanup.
- Read-only workspace prevents mutation.
- In-place workspace requires acknowledgement.
- Structured added/modified/deleted file diff.
- Declared artifact collection.
- Undeclared files remain visible in diff but are not silently promoted to artifacts.
- Cleanup removes owned temporary workspaces after evidence collection.
- Concurrent workspaces remain isolated.

### I. Assertion and evaluation tests

Cover:

- Exact, contains, regex, and negative text assertions.
- Ordered content blocks.
- Content-block type and predicate matching.
- Tool-call match by name.
- Tool-call match by server and name.
- Exact arguments.
- Partial arguments.
- Nested structural arguments.
- Argument predicate.
- Result/status matching.
- Exact count and range.
- Turn-scoped search.
- Execution/session-wide search.
- Ambiguity diagnostics.
- Zero-call negative assertion.
- Lifecycle/outcome assertions.
- Protocol/transport/capability assertions.
- Artifact and workspace-diff assertions.
- Immediate assertion on completed values.
- Event-driven eventual success.
- Eventual timeout with latest snapshot.
- Grouped soft assertions report all failures.
- Structural failure diffs redact secrets.
- Failure output contains trace and artifact locations.
- Canonical snapshots remove all default unstable fields.
- Explicit opt-in restores selected fields.
- Existing snapshot libraries accept canonical output.
- Evaluator passed, failed, inconclusive, error, and not-run states.
- Required failed evaluator fails pytest.
- Optional failed evaluator does not rewrite execution outcome.
- Evaluator receives immutable context.
- Evaluator cannot mutate the trace or subject.
- Evaluations persist and reload.
- Registered evaluator referenced by serializable name.
- API rejects unregistered Python evaluator callables.

### J. Trace and redaction tests

Cover every outcome:

- Completed.
- Agent/model failure.
- MCP protocol error.
- Server startup failure.
- Harness startup failure.
- Timeout.
- User cancellation.
- Worker interruption.
- Cleanup failure.

For each, verify:

- A complete or partial trace exists.
- Canonical sequence numbers are contiguous.
- Events are persisted before delivery.
- Wall time and monotonic ordering are present.
- Requests correlate to responses across transports.
- Connection identity prevents cross-server ID collisions.
- Turn and session attribution is correct.
- Canonical and raw evidence remain separately addressable.
- Large content is blob-backed without semantic truncation.
- Blob hash, length, media type, schema, redaction, and generation metadata are correct.
- Explicit reasoning is retained.
- Hidden reasoning remains marked unavailable and is not reconstructed.
- Prompts, tool arguments/results, headers, credentials, and raw evidence obey redaction.
- In-process assertions can use original values before disposal.
- Persistent/UI/API/artifact outputs contain only redacted forms.
- Pytest exports failures by default.
- `always` exports successes and failures.
- `never` suppresses file export while keeping in-memory traces.
- Persistent application mode stores successful and unsuccessful traces equally.
- Terminal UI can reopen and display each trace.

### K. Store, queue, and worker tests

Cover:

- In-memory store behavior.
- SQLite fresh-schema creation.
- Filesystem blob writes and reads.
- Foreign-key enforcement.
- WAL behavior where supported.
- Busy-timeout contention.
- Transaction rollback.
- Event invisibility before commit.
- Atomic execution claim.
- Competing workers cannot double-own an execution.
- Heartbeat renewal.
- Stale lease detection.
- Owner crash becomes interrupted.
- Partial evidence survives owner crash.
- Interrupted agent sessions are not resumed.
- Durable cancellation before claim.
- Durable cancellation during execution.
- FIFO scheduling.
- Interactive turn-command FIFO.
- Cross-process submit/read/cancel.
- Streamlit and FastAPI workers sharing one database.
- Profile revision resolution at execution creation.
- Clone pins original revision.
- Explicit clone-latest resolves new revision.
- Profile archive leaves historical execution readable.
- Active execution deletion rejected.
- Terminal execution deletion cascades correctly.
- Shared blobs remain while referenced.
- Unreferenced blobs are garbage-collected.
- Corrupt or missing blob detected.
- Retention disabled by default.
- Explicit retention operation preserves integrity.
- xdist workers remain isolated.
- SQLite and memory implementations pass the same store contract suite.

### L. API v2 contract tests

Cover:

- Create direct execution.
- Create agent execution.
- `202` and stable execution identifier.
- Specification JSON round-trip.
- List filtering and cursor pagination.
- Get current snapshot.
- Get terminal report.
- Initial event snapshot.
- SSE live events.
- SSE reconnect from sequence.
- Duplicate event prevention after reconnect.
- Cancel queued execution.
- Cancel running execution.
- Delete terminal execution.
- Reject deletion of active execution.
- Interactive session open.
- Enqueue multiple turns.
- Retrieve live snapshot.
- Close and cancel session.
- Execution idempotency retry.
- Turn idempotency retry.
- Conflicting idempotency key returns `409`.
- Validation error envelope.
- Readiness/startup error envelope.
- Protocol, timeout, cancellation, and internal error envelopes.
- Secrets and raw unredacted data absent from JSON.
- Unregistered callable/profile rejected.
- OpenAPI examples validate against shared models.
- SDK model → JSON → SDK model round-trip.
- `/api/v1` absent.
- Non-loopback bind warning.

### M. Streamlit application tests

Cover:

- UI starts with a directly injected persistent toolkit and no FastAPI.
- Browser sessions keep independent form/draft state.
- Server/harness profile creation and revision display.
- Probe results and readiness errors.
- Submit one-turn execution.
- Goal remains required in the UI.
- Selected server maps to the shared specification.
- Successful run history and trace display.
- Failed run history and trace display.
- Timed-out run trace display.
- Cancelled run trace display.
- Interrupted run trace display.
- Startup failure partial trace display.
- Clone preserves resolved profile revision.
- Clone with explicit latest uses the new revision.
- Cancel updates through durable state.
- Delete terminal run.
- Reject/delete-disabled active run.
- Lifecycle card separate from activity health.
- `no_calls`, `all_succeeded`, `mixed`, and `all_failed` render correctly.
- Evaluation result renders separately.
- Large/redacted evidence loads from artifacts safely.
- No UI code performs HTTP requests to the local API.
- In-memory fake harness makes the UI suite deterministic.

### N. Pytest plugin and CLI tests

Cover:

- Fixture construction and cleanup.
- Sync and async fixtures.
- Explicit reusable session factory.
- Marker registration without warnings.
- Markers do not alter skip behavior.
- Missing requested harness fails only the requesting test.
- Artifact export on failed test.
- No default export on success.
- Always/never policy.
- Terminal failure summary.
- JUnit properties contain identifiers/paths but no trace payload or secrets.
- xdist isolation.
- `mcp-pal test` forwards file selection.
- Forwards `-k`, `-m`, parametrization, verbosity, capture, maxfail, and plugin options.
- Works with coverage.
- Works with JUnit.
- Works with xdist.
- Preserves pytest exit codes for success, failure, collection failure, no tests, interruption, and internal error.
- Missing pytest extra produces an installation hint.
- Readiness summary does not fail due to unused harnesses.
- No scenario-file discovery.
- No implicit retry, skip, or harness selection.
- `uv run pytest` and `uv run mcp-pal test` execute equivalent tests.
- `doctor` strict success/failure behavior.

### O. Documentation and public-reference E2E corpus

Execute actual pytest reference modules covering at minimum:

- Minimal in-process direct tool test.
- Stdio server test.
- HTTP transport test.
- Transport matrix.
- Tool error assertion.
- Resource read and subscription.
- Prompt retrieval.
- Sampling and elicitation callback.
- Stateful mock server.
- Deterministic fault injection.
- Strict trace replay.
- Schema-derived property test.
- First deterministic ACP agent test.
- Multi-turn memory test.
- Three-turn recovery after tool error.
- Multi-server routing with duplicate tool names.
- Typed multimodal user message.
- Tool-policy denial.
- Explicit full-tool acknowledgement.
- Workspace diff and artifact collection.
- Cancellation and partial trace.
- Timeout and partial trace.
- Required deterministic evaluator.
- Soft grouped assertions.
- Canonical snapshot integration.
- Persistent execution and reload.
- Pytest artifact export configuration.
- Expected readiness failure.
- Expected protocol failure.
- Expected ambiguous assertion failure.
- Live Claude Code example.
- Live OpenCode example.
- Live ACP/provider example.
- Remote authenticated server example.

CI must run documentation snippets and this corpus from the installed wheel. Tests must fail if an example imports an internal module, depends on repository-relative source imports, or diverges from its rendered documentation.

### P. Release and quality gates

Run on Python 3.10–3.13 on Linux, with focused macOS and Windows coverage for process, path, and transport behavior.

Required gates:

- Ruff lint and format check.
- Strict type checking.
- Unit and deterministic integration suites.
- Store/harness contract suites.
- Installed-wheel reference E2E corpus.
- API/UI deterministic suites.
- Documentation build with strict links/snippets.
- Public export/API documentation coverage.
- Wheel/sdist build and metadata checks.
- Clean-environment installation of every extra.
- Dependency and vulnerability audit.
- Repository diff check after tests to catch generated-file leaks.
- Critical-path branch coverage tracking.
- Duration reporting without an arbitrary one-minute cutoff.
- No leaked subprocesses, ports, workspaces, databases, or event-loop threads after black-box tests.

Prepare a Trusted Publishing workflow using OIDC, a protected PyPI environment, and release-tag validation. Do not configure the external publisher, create a tag, or execute an upload as part of this change.

## 7. Acceptance criteria and explicit boundaries

The v0.2 work is complete when:

- MCP authors can write direct and multi-turn agent tests entirely as normal Python/pytest.
- Sync and async APIs offer equivalent supported capabilities.
- True multi-turn behavior is demonstrated for ACP, Claude Code, and OpenCode without resume-based simulation.
- Multiple simultaneous MCP servers and overlapping tool names are supported.
- UI and API use the same SDK models and execution engine.
- Streamlit no longer calls FastAPI and still stores and displays traces for every outcome.
- Ephemeral pytest runs retain complete in-memory traces while exporting only failures by default.
- The reference E2E corpus covers every public capability with relevant failures and cleanup/trace assertions.
- The project installs with uv and pip on Python 3.10–3.13.
- Documentation, examples, wheel, sdist, and release automation pass all gates.
- No implementation decision is left to scenario files, implicit harness behavior, silent fallbacks, or legacy compatibility.

Explicitly deferred:

- Official MCP conformance wrapper and its planning.
- JSON/YAML scenario authoring.
- LLM-as-judge built-ins.
- Statistical repeat/quorum/experiment semantics.
- Multi-turn or multi-server Streamlit UX.
- Postgres.
- Production authentication and multi-user authorization.
- Automatic retention.
- Execution priority scheduling.
- Legacy database migration or detection.
- Actual TestPyPI/PyPI publication.

## 8. Implementation todo list

Work through this list in order. Each checkbox should be completed as a small reviewable change with its own tests. Do not begin a later milestone while an earlier milestone's gate is failing. Keep compatibility only within the new v0.2 interfaces as they are introduced; do not add v1 compatibility code to make intermediate steps easier.

### Current status — 2026-08-25

- Remediation is in progress; the authoritative issue list and implementation
  sequence are tracked in [python-testing-sdk-v0.2-remediation.md](python-testing-sdk-v0.2-remediation.md).
- The initial remediation baseline was 2 failed, 746 passed, 1 skipped, and 3
  strict xfailed. After the independently reviewed round-1 direct-trace fix,
  the full suite is 749 passed, 1 skipped, and 3 strict xfailed with no
  unexpected failures. The remaining xfails are tracked remediation defects,
  not a completed release gate.
- The opt-in live OpenCode characterization failed before the model request.
- No prior phase or release gate is claimed complete by this status until its
  remediation acceptance evidence is rerun and recorded.

### Phase 0 — Preserve the baseline

- [x] Read section 9 in full and treat it as the implementation contract for every phase.
- [x] Record the current test, lint, type-check, build, API, UI, and CLI commands.
- [x] Run the existing suite and record known warnings and platform-specific behavior.
- [x] Inventory all current public imports, entry points, database locations, environment variables, profile fields, API routes, and UI actions.
- [x] Map current execution, trace, harness, storage, API, and UI modules to their intended v0.2 owner.
- [x] Identify existing tests and behaviors that must be preserved versus intentionally replaced.
- [x] Add a temporary migration checklist linking each current behavior to its future SDK test.
- [x] Confirm the worktree contains no unrelated generated files before structural changes.
- [x] Establish the rule that every phase ends with a clean build and passing deterministic suite.

### Phase 1 — Create the uv workspace and distributable SDK

- [x] Convert the root project to a uv workspace containing the `sdk` project.
- [x] Move the Python package beneath `sdk/src/mcp_pal` without changing runtime behavior yet.
- [x] Move and reorganize tests beneath `sdk/tests` while preserving baseline coverage.
- [x] Configure Hatchling to build only the intended SDK package and runtime data.
- [x] Set package metadata for `mcp-pal`, Python `>=3.10`, Apache-2.0, README, authors, classifiers, keywords, and project URLs.
- [x] Add the Apache-2.0 license file and PEP 639-compatible license metadata.
- [x] Define base, `pytest`, `storage`, `app`, `property`, `docs`, and `all` dependency extras.
- [x] Add `mcp>=2,<3` and validate the actual official SDK API used by the implementation.
- [x] Regenerate the uv lockfile for Python 3.10–3.13.
- [x] Add `py.typed` and configure its wheel inclusion.
- [x] Configure wheel contents to include runtime schemas and license only where appropriate.
- [x] Configure the sdist to include docs, examples, tests needed for source validation, and legal files without unrelated research/build artifacts.
- [x] Obtain the runtime version exclusively through `importlib.metadata`.
- [x] Verify that `import mcp_pal` has no database, subprocess, filesystem-write, network, or event-loop side effects.
- [x] Build wheel and sdist and run metadata/content validation.
- [x] Install the wheel into a clean environment and smoke-test imports and entry points.

### Phase 2 — Define the public domain model

- [x] Establish `mcp_pal`, `sync_api`, `async_api`, `types`, `matchers`, `testing`, and `pytest_plugin` module boundaries.
- [x] Define the public export manifest and a test that rejects undocumented or accidental exports.
- [x] Implement frozen identifiers and metadata types for executions, sessions, turns, servers, connections, artifacts, and events.
- [x] Implement execution lifecycle and outcome enums with validated transitions.
- [x] Implement turn lifecycle and outcome models with validated transitions.
- [x] Implement typed content blocks and the `UserMessage` envelope.
- [x] Implement server definitions for in-process, stdio, Streamable HTTP, and legacy SSE.
- [x] Implement serializable server and harness profile references with immutable revision identity.
- [x] Implement typed harness values for Claude Code, OpenCode, and ACP.
- [x] Implement workspace, tool, permission, elicitation, sampling, filesystem, and terminal policy models.
- [x] Implement `DirectExecutionSpec` and `AgentExecutionSpec` as frozen Pydantic models.
- [x] Implement immutable execution, turn, trace, evaluation, artifact, readiness, and capability results.
- [x] Define stable public exception types and machine-readable error codes.
- [x] Define secret-reference types that cannot serialize resolved secret values.
- [x] Add JSON-schema generation and round-trip tests for every serializable model.
- [x] Add strict type-checking tests for the intended sync and async usage examples.

Phase 2 boundary note: profile references represent either a submission-time `latest` selector or a pinned immutable revision. Execution creation must replace `latest` with a pinned reference before recording effective execution state; resolution, clone provenance, and persistence remain acceptance gates for the later execution/profile-service phases.

### Phase 3 — Implement configuration and capability discovery

- [x] Define all supported `[tool.mcp-pal]` settings and environment-variable names.
- [x] Implement configuration precedence: explicit arguments, environment, project config, defaults.
- [x] Track the source of every effective configuration value for diagnostics.
- [x] Keep `.env` loading out of library code.
- [x] Add explicit `.env` loading options to application and CLI entry points only.
- [x] Implement typed binary, protocol, transport, storage, and harness readiness probes.
- [x] Record detected versions and individual capability results without version allowlists.
- [x] Ensure probing one harness does not initialize or fail unrelated harnesses.
- [x] Implement `kit.capabilities()` and namespaced probe services.
- [x] Implement strict `mcp-pal doctor` behavior for explicitly requested capabilities.
- [x] Add redaction tests for configuration errors, probe output, reprs, and serialized snapshots.

### Phase 4 — Build canonical tracing and ephemeral execution storage

- [x] Define the canonical event taxonomy and versioned event schema.
- [x] Define raw-evidence references separately from normalized semantic events.
- [x] Implement a per-execution sequence allocator.
- [x] Add UTC timestamps and monotonic offsets to events and lifecycle records.
- [x] Implement request/response correlation using connection, typed request ID, sequence, direction, session, and turn.
- [x] Implement the in-memory `ExecutionStore`.
- [x] Implement the temporary/in-memory `ArtifactStore`.
- [x] Implement content-addressed compressed blobs with hash and length validation.
- [x] Implement redaction before persistence, export, logging, API serialization, and UI projection.
- [x] Preserve unredacted values only inside the active process for explicit assertions.
- [x] Implement immutable execution and turn snapshots derived from committed events.
- [x] Guarantee that events are committed before iterators or callbacks receive them.
- [x] Implement complete and partial trace finalization for every terminal outcome.
- [x] Represent explicit reasoning, unavailable reasoning, and encrypted/provider-hidden reasoning without inference.
- [x] Add contract tests for event ordering, correlation, redaction, large blobs, and every terminal outcome.

### Phase 5 — Implement the asynchronous direct MCP client

- [x] Wrap the official MCP v2 client without duplicating its protocol engine.
- [x] Implement in-process MCP connection lifecycle.
- [x] Implement stdio connection and owned-process cleanup.
- [x] Implement Streamable HTTP connection lifecycle.
- [x] Implement legacy SSE connection lifecycle.
- [x] Capture initialization, negotiated revision, identity, instructions, capabilities, extensions, and transport evidence.
- [x] Expose the official response through `.raw` on typed result wrappers.
- [x] Implement tool listing, pagination, `list_all_tools`, and tool calls.
- [x] Implement resource listing, templates, reads, pagination, and subscriptions.
- [x] Implement prompt listing, pagination, and retrieval.
- [x] Implement supported completion, logging, progress, roots, sampling, elicitation, ping, notification, and cancellation paths from the official client.
- [x] Preserve tool `is_error` responses as results rather than infrastructure exceptions.
- [x] Map JSON-RPC errors to `ProtocolError` with partial trace evidence.
- [x] Attach partial evidence to transport and process failures.
- [x] Implement optional structural validation of advertised input/output schemas.
- [x] Implement `raise_server_exceptions=True` for in-process tests and sanitized protocol behavior when disabled.
- [x] Implement headers, bearer tokens, supplied official OAuth implementations, and deterministic auth fixtures.
- [x] Enforce endpoint trust classification for agent exposure.
- [x] Add page-level and convenience method tests for every supported capability.
- [x] Run the same direct-client contract against every supported transport.

**Mandatory escalation before Phase 5 claims protocol-matrix coverage:** the
pinned official MCP v2 high-level `ClientSession` currently constructs its
initialize request with the SDK's `LATEST_HANDSHAKE_VERSION` and validates the
server response against the SDK's fixed handshake-version set. Its public
constructor accepts transport read/write streams rather than an explicit
protocol-revision selector. Validate the pinned API and its supported stdio,
Streamable HTTP, and SSE transport factories before implementing revision
constraints; if an explicit revision matrix cannot be expressed through public
APIs, escalate the dependency/API gap instead of duplicating the protocol
engine or silently claiming unsupported coverage.

Phase 5 escalation resolution: the pinned `mcp==2.0.0` public API was
validated directly. It does not expose an arbitrary handshake-revision
selector, so MCP Pal accepts automatic/current negotiation and rejects other
explicit revision constraints before startup. Revision-matrix coverage is not
claimed. Remote transports validate all resolved addresses and disable
redirects, but connection-level DNS pinning is unavailable through the pinned
official transport API; the remaining DNS-rebinding TOCTOU risk is recorded as
a dependency limitation. Direct traces therefore capture normalized decoded
MCP messages and explicitly report raw-wire capture as incomplete.

### Phase 6 — Add the synchronous API

- [x] Define one authoritative async implementation for direct protocol behavior.
- [x] Implement the lifecycle-safe synchronous portal/proxy.
- [x] Generate or mechanically maintain sync wrappers from the async surface.
- [x] Prevent async client objects from escaping onto the caller's thread.
- [x] Match result, exception, cancellation, timeout, and cleanup behavior between sync and async APIs.
- [x] Add a public-surface parity test that fails when only one twin exposes a supported method.
- [x] Test nested and repeated kit lifecycles without leaking threads or event loops.
- [x] Test sync use from ordinary pytest and async use from async pytest plugins.

### Phase 7 — Implement assertions, evaluations, snapshots, and test utilities

- [x] Implement `expect(subject)` dispatch for direct results, turns, executions, sessions, traces, and handles.
- [x] Implement exact, contains, regex, ordered-block, and negative content assertions.
- [x] Implement tool-call assertions for server, tool, arguments, result, status, count, range, turn, and predicate.
- [x] Reject ambiguous serverless assertions when duplicate tool names exist.
- [x] Implement lifecycle, outcome, protocol, transport, capability, artifact, workspace, and trace assertions.
- [x] Implement event-driven eventual assertions without polling sleeps.
- [x] Implement grouped soft assertions through `check()`.
- [x] Add structural diffs, candidate summaries, scope, identifiers, redacted excerpts, and artifact paths to failures.
- [x] Implement canonical snapshot normalization and field opt-ins.
- [x] Verify canonical values work with established Python snapshot plugins.
- [x] Define `EvaluationContext`, evaluator protocol, registrations, and all evaluation statuses.
- [x] Implement persisted optional and required deterministic evaluations.
- [x] Ensure evaluation cannot mutate execution lifecycle, results, trace, or artifacts.
- [x] Implement decorator-based and stateful `MockMCPServer` APIs.
- [x] Implement strict, optional, repeated, subset, unordered, and fallback expectations.
- [x] Implement deterministic gates and clocks.
- [x] Implement all planned protocol, process, payload, and cancellation fault injectors.
- [x] Implement strict record/replay with explicit redaction bindings and relaxation provenance.
- [x] Add optional Hypothesis schema strategies behind `[property]`.

### Phase 8 — Complete the `0.2.0a1` gate

- [x] Run all package, model, configuration, trace, direct-client, sync/async, assertion, evaluation, and testing-utility tests.
- [x] Run transport matrices and deterministic fault/replay scenarios.
- [x] Build and install the wheel on Python 3.10–3.13.
- [x] Confirm no application behavior has been partially redirected to unfinished SDK paths.
- [x] Confirm no v1 database compatibility or detection code has been introduced.
- [x] Record remaining public API gaps before starting agent runtime work.
- [x] Mark the package internally as `0.2.0a1` only when this gate passes.

### Phase 9 — Build the shared multi-turn agent runtime

- [x] Implement `MCPTestKit`, `AsyncMCPTestKit`, context management, and explicit close methods.
- [x] Implement one-shot `kit.run(spec)` for direct and agent specifications.
- [x] Implement `kit.submit(spec)` and sync/async execution handles.
- [x] Implement live snapshots, event iterators, callbacks, cancellation, and terminal result retrieval.
- [x] Implement the common harness adapter protocol and capability contract.
- [x] Implement `AgentSession` and its lifecycle state machine.
- [x] Preserve one agent conversation and the same MCP processes/connections across turns.
- [x] Keep model, harness, servers, workspace, tool policy, and permission policy immutable after opening.
- [x] Allow message, timeout, and metadata to vary per turn.
- [x] Implement typed content conversion and reject unsupported attachments during preflight.
- [x] Implement `SessionStillOpen`, `SessionBusy`, sequential `send`, and explicit `enqueue_turn`.
- [x] Keep sessions usable after tool-level MCP errors.
- [x] Make timeout, cancellation, harness loss, and transport loss terminal.
- [x] Implement live snapshots and terminal-only results.
- [x] Implement new-execution replay/fork provenance without claiming live-session identity.
- [x] Implement multiple required and optional servers per session.
- [x] Implement SDK-hosted loopback access for in-process MCP servers.
- [x] Add duplicate-tool-name routing and assertion tests.
- [x] Implement MCP activity health independently of lifecycle and evaluations.

### Phase 10 — Implement workspace and policy enforcement

- [x] Implement fresh temporary workspace creation and cleanup.
- [x] Implement filtered filesystem-copy workspaces.
- [x] Implement Git-worktree workspaces.
- [x] Implement read-only workspaces.
- [x] Implement acknowledged in-place workspaces.
- [x] Enforce default exclusions for repositories, environments, caches, secrets, and artifacts.
- [x] Require explicit acknowledgement for risky inclusion.
- [x] Capture structured added, modified, and deleted workspace entries.
- [x] Collect declared artifacts after execution and before workspace cleanup.
- [x] Implement portable typed tool-policy evaluation.
- [x] Fail preflight when a harness cannot enforce requested portable policy.
- [x] Implement the marked nonportable native-policy escape hatch.
- [x] Require acknowledgement for unrestricted tools.
- [x] Implement typed permission, elicitation, sampling, filesystem, and terminal handlers.
- [x] Default filesystem and terminal requests to denied.
- [x] Test policy and workspace isolation under concurrent sessions.

### Phase 11 — Implement deterministic and real harness adapters

- [x] Build a deterministic local ACP agent that exercises the complete harness contract without model/network access.
- [x] Build fake Claude Code and OpenCode adapters for deterministic contract testing.
- [x] Implement the real ACP adapter with one process, connection, session ID, server configuration, and capture context across turns.
- [x] Implement the real Claude Code adapter using one continuous stream-JSON process.
- [x] Treat missing Claude streaming support as not ready; do not add a resume fallback.
- [x] Implement the real OpenCode adapter using one isolated serve instance and attached session.
- [x] Isolate HOME, config, data, plugin discovery, and project discovery for each harness.
- [x] Pass through only explicit credentials and environment values.
- [x] Apply deterministic locale and timezone settings where supported.
- [x] Record requested, enforced, observed, and unavailable usage data without estimation.
- [x] Implement structured process, transport, MCP, tool-call, and content capture for every adapter.
- [x] Implement cancellation and timeout escalation for each owned process tree.
- [x] Prove that terminal results are returned only after processes, proxies, and readers are reaped or cleanup failure is recorded.
- [x] Run the common harness contract against deterministic adapters on every change.
- [x] Define separate live characterization commands for real installed harnesses.

### Phase 12 — Complete the `0.2.0a2` gate

- [x] Run deterministic one-turn and multi-turn harness contracts.
- [x] Prove state persists across at least three turns in the same agent and MCP session.
- [x] Prove sessions and workspaces remain isolated under concurrency.
- [x] Prove multiple servers and overlapping tool names are routed and traced correctly.
- [x] Prove all policy, permission, attachment, timeout, cancellation, and cleanup behaviors.
- [x] Run explicitly configured live characterization without making it a deterministic CI dependency.
- [x] Confirm no harness silently falls back to another harness or weaker multi-turn mechanism.
- [x] Mark the package internally as `0.2.0a2` only when this gate passes.

### Phase 13 — Implement persistent storage, leases, and workers

- [x] Define the fresh v0.2 SQLite metadata schema without a legacy version detector.
- [x] Implement SQLite profile and immutable revision persistence.
- [x] Implement execution, turn, event, evaluation, artifact, and blob-reference persistence.
- [x] Implement SQLite foreign keys, transactions, WAL where supported, and busy timeout.
- [x] Implement filesystem blobs and reference-counted integrity-aware garbage collection.
- [x] Implement atomic execution claims and worker leases.
- [x] Implement heartbeats and stale-owner interruption.
- [x] Implement durable cancellation flags.
- [x] Implement persistent FIFO execution and interactive-turn command queues.
- [x] Ensure any persistent toolkit may enable an embedded worker.
- [x] Prevent two workers from owning one execution.
- [x] Preserve committed partial evidence after worker loss.
- [x] Mark abandoned work interrupted without attempting agent-session resume.
- [x] Implement archive, clone-original, explicit clone-latest, terminal deletion, and active-deletion rejection.
- [x] Keep automatic retention disabled.
- [ ] Add store contract tests shared by memory and SQLite implementations.
- [ ] Add cross-process submit, claim, stream, cancel, and recovery tests.
- [x] Add `just dev-db-reset CONFIRM=reset` with exact-path and sidecar safety checks.
- [x] Test that fresh startup recreates the schema after the guarded reset.
- [ ] Confirm no production database reset CLI, migration framework, table purge, backup, or v1/v2 detection exists; the guarded development-only reset command is the explicit exception, and the legacy `/api/v1` purge path must be removed in Phase 14.

### Phase 14 — Replace the API with the v2 adapter

- [ ] Implement `/api/v2` entirely as an adapter over shared SDK services and models.
- [ ] Implement execution creation with `202` responses.
- [ ] Implement cursor-paginated execution listing and filtering.
- [ ] Implement execution snapshots and terminal reports.
- [ ] Implement event snapshots and sequence-resumable SSE.
- [ ] Implement cancellation and terminal deletion.
- [ ] Implement interactive session open, enqueue, snapshot, close, and cancel routes.
- [ ] Implement optional execution and turn idempotency keys and `409` conflicts.
- [ ] Implement the stable typed error envelope.
- [ ] Restrict API specifications to registered serializable references.
- [ ] Reject arbitrary callables, factories, and pickled data.
- [ ] Generate OpenAPI from the shared models.
- [ ] Add JSON/model/OpenAPI round-trip contract tests.
- [ ] Emit a warning for non-loopback binding.
- [ ] Remove `/api/v1` routes and their compatibility code.

### Phase 15 — Migrate Streamlit to direct SDK use

- [ ] Construct and cache one persistent toolkit for the Streamlit process.
- [ ] Enable an embedded worker using database leases.
- [ ] Remove local FastAPI HTTP calls and request-client abstractions from the UI.
- [ ] Preserve browser-session isolation for draft and selection state.
- [ ] Map current server and harness profile forms to SDK profile services.
- [ ] Map probes to SDK capability/readiness services.
- [ ] Map one-turn submissions to `AgentExecutionSpec` with one selected server.
- [ ] Rename the SDK field to optional `goal` while keeping the UI field required and correctly labelled.
- [ ] Preserve clone, cancel, history, deletion, and report behavior.
- [ ] Render lifecycle, MCP activity health, and evaluations as separate concepts.
- [ ] Display persisted traces for completed, failed, timed-out, cancelled, interrupted, and startup-failed runs.
- [ ] Load large evidence safely from redacted artifacts.
- [ ] Replace request mocks with injected in-memory kits and deterministic fake harnesses.
- [ ] Verify Streamlit and a separately running FastAPI process can share the store without duplicate execution.
- [ ] Keep multi-turn and multi-server controls out of this UI milestone.

### Phase 16 — Complete the `0.2.0a3` gate

- [ ] Run all SQLite, lease, worker, API v2, and Streamlit tests.
- [ ] Run cross-process UI/API ownership and cancellation tests.
- [ ] Confirm `/api/v1` and local UI HTTP dependencies are absent.
- [ ] Confirm every persistent UI outcome has a reopenable trace.
- [ ] Confirm fresh database startup and guarded development reset work from documented commands.
- [ ] Confirm no legacy data is inspected, imported, backed up, or migrated.
- [ ] Mark the package internally as `0.2.0a3` only when this gate passes.

### Phase 17 — Implement the pytest plugin and thin CLI wrapper

- [ ] Implement function-scoped `mcp_test` and `async_mcp_test` fixtures.
- [ ] Implement explicit reusable session factories without hidden session-scoped ownership.
- [ ] Register all agreed pytest markers as classification only.
- [ ] Implement per-test ephemeral stores, artifacts, workspaces, portals, and port isolation.
- [ ] Implement xdist worker isolation.
- [ ] Implement failure-only artifact export by default and `always`/`never` settings.
- [ ] Add concise trace and artifact references to failure output.
- [ ] Add safe identifiers and paths, but not trace bodies, to JUnit properties.
- [ ] Implement `mcp-pal test` as a transparent pytest argument and exit-code wrapper.
- [ ] Verify compatibility with selection, markers, parametrization, coverage, JUnit, and xdist.
- [ ] Fail with an installation hint when the pytest extra is missing.
- [ ] Ensure unused unavailable harnesses do not make the wrapper fail globally.
- [ ] Ensure selected tests requesting missing prerequisites fail rather than auto-skip.
- [ ] Prove `pytest` and `mcp-pal test` execute equivalent suites.
- [ ] Confirm no JSON/YAML scenario discovery or custom runner behavior exists.

### Phase 18 — Build the industry-standard documentation set

- [ ] Configure MkDocs Material, mkdocstrings, strict links, strict snippets, and versioning.
- [ ] Write the direct-testing tutorial.
- [ ] Write the deterministic ACP agent tutorial.
- [ ] Write the true multi-turn state tutorial.
- [ ] Write the multiple-server routing tutorial.
- [ ] Write transport, authentication, policy, workspace, artifact, replay, storage, API, UI, and CI how-to guides.
- [ ] Generate complete public API and configuration references.
- [ ] Document lifecycle, outcome, activity health, evaluation, tracing, persistence, redaction, and isolation semantics.
- [ ] Write ADRs for all decisions enumerated in the documentation section.
- [ ] Publish machine-readable schemas for serializable models.
- [ ] Generate `llms.txt` and `llms-full.txt` from maintained documentation sources.
- [ ] Write concise uv-first SDK README examples and pip alternatives.
- [ ] Add cost, credential, privacy, and nondeterminism warnings to live examples.
- [ ] Add the official conformance wrapper roadmap item and link only.
- [ ] Confirm no conformance dependency, fixture, CLI command, or partial adapter has entered v0.2.
- [ ] Run all documentation snippets as tests.

### Phase 19 — Build the executable reference E2E corpus

- [ ] Create the complete direct MCP pytest example set listed in section 6.C.
- [ ] Create the complete mock/fault/replay pytest example set listed in section 6.D.
- [ ] Create the complete multi-turn agent pytest example set listed in section 6.E.
- [ ] Create the complete multiple-server pytest example set listed in section 6.F.
- [ ] Create author-facing tool-policy, workspace, assertion, evaluation, trace, persistence, and plugin examples.
- [ ] Make expected-failure examples pass by asserting the exact documented failure.
- [ ] Separate deterministic examples from credentialed/cost-bearing live examples.
- [ ] Build the SDK wheel before running reference E2E tests.
- [ ] Install the wheel into a clean external environment.
- [ ] Run examples without repository source paths on `PYTHONPATH`.
- [ ] Reject any example importing private or repository-internal modules.
- [ ] Validate that rendered documentation and executable example sources remain synchronized.
- [ ] Assert cleanup and trace behavior in every black-box scenario, not only functional output.
- [ ] Map every public capability to at least one success and relevant failure/edge scenario.
- [ ] Add a coverage-manifest test that fails when a public capability lacks its required reference scenario.

### Phase 20 — Add CI, portability, and release readiness

- [ ] Add Linux CI for Python 3.10, 3.11, 3.12, and 3.13.
- [ ] Add focused macOS and Windows process/path/transport jobs.
- [ ] Add Ruff lint and format checks without modifying files in CI.
- [ ] Add strict type checking.
- [ ] Add deterministic unit, integration, contract, API, UI, and E2E jobs.
- [ ] Add installed-wheel and per-extra clean-install jobs.
- [ ] Add strict documentation and public-export coverage jobs.
- [ ] Add wheel/sdist metadata and content checks.
- [ ] Add dependency/vulnerability audit and repository-diff checks.
- [ ] Track critical-path branch coverage without setting an arbitrary 100% target.
- [ ] Report slow tests without imposing an arbitrary one-minute suite cutoff.
- [ ] Detect leaked subprocesses, ports, workspaces, databases, threads, and event loops.
- [ ] Keep live harness/model jobs explicit, credentialed, and separate from deterministic required CI.
- [ ] Prepare the OIDC Trusted Publishing workflow behind a protected environment and validated release tags.
- [ ] Do not configure external PyPI trust, create tags, or publish artifacts without separate authorization.

### Phase 21 — Complete the release-candidate and stable gates

- [ ] Run every deterministic test category in section 6.
- [ ] Run the complete installed-wheel reference E2E corpus.
- [ ] Run explicitly authorized live characterization for Claude Code, OpenCode, and ACP.
- [ ] Verify sync/async public-surface parity.
- [ ] Verify all public capabilities have success, failure/edge, trace, cleanup, and documentation coverage.
- [ ] Verify Streamlit persists and displays traces for every outcome.
- [ ] Verify API v2 and direct UI usage share the same models, stores, and workers.
- [ ] Verify uv and pip installation on all supported Python versions.
- [ ] Verify every optional extra independently.
- [ ] Verify no scenario-file system, legacy database compatibility, conformance wrapper, hidden LLM judge, or deferred feature was added.
- [ ] Review public names, docstrings, schemas, exceptions, and stability commitments.
- [ ] Produce changelog, migration note, support matrix, and release notes.
- [ ] Mark `0.2.0rc1` only after all release-candidate gates pass.
- [ ] Resolve every release-blocking defect without weakening tests or guarantees.
- [ ] Mark `0.2.0` only after the release candidate remains green across required platforms.
- [ ] Leave actual TestPyPI/PyPI publishing and release-tag creation for a separately authorized release action.

## 9. Implementation contract and assumption ledger

This section makes implicit design context explicit. An implementation agent must follow it before filling in an unspecified detail from habit. If an upstream dependency makes one of these guarantees impossible, stop that slice, record the concrete capability mismatch, and resolve the plan rather than silently weakening the guarantee.

### 9.1 Instruction hierarchy for implementation agents

When two sources appear inconsistent, use this order:

1. The v0.2 decisions and invariants in this plan.
2. Public contract tests and schemas added for v0.2.
3. Existing observable behavior that this plan explicitly says to preserve.
4. Existing implementation code as a source of working process/capture techniques.
5. Dependency examples and documentation for the actually pinned version.

Existing v1 APIs, table layouts, names, and compatibility tests do not override this plan. Conversely, “clean break” does not mean discarding proven redaction, subprocess, capture, proxy, isolation, or cleanup behavior before equivalent v0.2 tests pass.

Do not improvise a simpler fallback when a requirement is difficult. In particular, do not replace true multi-turn sessions with repeated one-shot invocations, replace wire capture with model-reported tool calls, replace persisted events with logs, or replace pytest tests with serialized scenarios.

### 9.2 Non-negotiable product invariants

- Python and pytest are the authoring format. There are no user-authored scenario JSON/YAML files.
- The SDK owns all execution behavior. FastAPI and Streamlit contain no parallel execution engine.
- Direct MCP tests and agent-driven tests are equally supported public surfaces.
- Multi-turn means one continuing agent conversation and the same living MCP server processes/connections.
- Multiple MCP servers are first-class in the SDK even though the initial migrated UI selects one.
- Every execution produces a complete or partial canonical trace.
- Persistent mode stores traces for every outcome, including successes.
- Failed-only is solely the default pytest file-export policy; it is never the trace-generation or persistent-storage policy.
- Canonical semantic evidence and raw provider/wire evidence are separate layers.
- A harness-reported MCP call without correlated MCP wire evidence remains visible but cannot satisfy an assertion that requires an actual MCP call.
- A wire MCP call without a matching harness event remains visible and assertable as wire evidence.
- Hidden reasoning is never inferred, synthesized, or presented as captured reasoning.
- The application database is disposable development state for this release. There is no runtime legacy detection or migration.
- The official conformance suite is a separately planned future wrapper, not a partially implemented v0.2 feature.
- No operation silently retries, switches harnesses, downgrades policy, drops content, resumes a broken session, or broadens permissions.

### 9.3 Default-behavior ledger

Unless the caller explicitly overrides it, use these defaults:

| Concern | Default |
|---|---|
| SDK persistence | Ephemeral in-memory metadata plus temporary blobs |
| App/UI persistence | SQLite metadata plus filesystem blobs |
| Trace generation | Always |
| Persistent trace retention | Every outcome until explicit deletion |
| Pytest artifact-file export | Failed tests only |
| Protocol revision | Automatic negotiation |
| In-process exception behavior | Raise original server exceptions |
| Server requirement | Required unless explicitly marked optional |
| Direct in-process exposure to agents | SDK-created trusted loopback |
| Workspace | Fresh temporary workspace |
| Filesystem/terminal access | Denied |
| Tool permissions | Restrictive portable policy |
| Unrestricted tools | Rejected without explicit risk acknowledgement |
| Harness/model/server fallback | None |
| Queue scheduling | Strict FIFO |
| Automatic retention or deletion | Disabled |
| `.env` loading in library code | Disabled |
| OpenTelemetry | Disabled unless configured |
| Telemetry content | Metadata only; no prompts, arguments, results, reasoning, headers, or raw frames |
| UI form | One goal, one selected server, one submitted turn |
| UI trace timing in v0.2 | Render after terminal state; persistence may be incremental |
| User-authored test format | Normal Python/pytest |

Preserve the current application’s 120-second run timeout as the migrated UI default unless an explicit app setting overrides it. Do not treat this UI default as a protocol-wide constant: SDK specifications and direct operations expose explicit timeout configuration, and live harnesses report which limits were requested, enforced, observed, or unavailable.

### 9.4 Package dependency direction

Use dependency direction to prevent the SDK from collapsing back into an application library:

```text
types/config/errors
        ↓
events/results/assertions
        ↓
direct runtime       agent runtime
        ↓                 ↓
      execution orchestration
        ↓
storage protocols and optional implementations
        ↓
pytest integration / FastAPI adapter / Streamlit application / CLI
```

Rules:

- Core types, errors, events, results, assertions, and configuration cannot import FastAPI, Streamlit, pytest, SQLAlchemy, or application settings.
- Direct and agent runtimes depend on core contracts, not API/UI models.
- In-memory storage remains usable without the `storage` extra.
- SQLAlchemy code is imported lazily behind the storage extra.
- The pytest plugin adapts public SDK objects; it cannot reach into private worker or harness internals.
- FastAPI serializes public SDK models and calls public services.
- Streamlit uses public services and result projections, never storage sessions or API route functions directly.
- CLI subcommands invoke the same public services as Python callers.
- Application-specific defaults are composed at the application boundary, not embedded in core SDK defaults.

Recommended internal ownership is `core`, `direct`, `agent`, `trace`, `storage`, `testing`, `integrations/pytest`, and `app` packages. Exact filenames may evolve, but violating the dependency direction requires an explicit architectural review.

### 9.5 Current-to-v0.2 concept mapping

Use this mapping while replacing the existing implementation:

| Current concept | v0.2 concept | Migration rule |
|---|---|---|
| `Run` | `Execution` plus optional `AgentSession` and `Turn` records | Replace; do not maintain a compatibility model |
| `prompt` | First turn’s `UserMessage` | UI still accepts text initially |
| `expected_output` | Optional SDK `goal` | UI keeps the field required and relabels it |
| `enabled_server` | Ordered server bindings on the specification | Migrated UI supplies a one-item list |
| `claude_result` / `final_output` | Ordered response content blocks plus `.text` | Remove provider-named result aliases from the new API |
| One status field | Lifecycle plus terminal outcome | Never overload “completed” to mean assertion success |
| `RunTrace` JSON | Canonical event stream plus derived immutable trace/report | Do not store one mutable provider-shaped trace as authority |
| `claude.v1`, `opencode.v1`, `acp.v1` | Raw evidence adapters feeding one canonical v0.2 event schema | Vendor raw formats remain evidence, not public semantic authority |
| `mcp.v1` normalized calls | Canonical MCP request/call/result events | Preserve correlation and provenance behavior |
| `RunManager` FIFO worker | Execution service plus lease-owning worker | Reuse proven cleanup logic behind new contracts |
| API-created runs | SDK-created execution specifications | API is serialization only |
| Streamlit HTTP client | Cached persistent `MCPTestKit` | Delete the local HTTP dependency after parity tests pass |

The old provider trace schemas do not need a reader or migration. New executions use one canonical schema identifier and schema version. Event-schema versioning is allowed and required for artifacts/API consumers; it is not database-version detection and must not be used to branch on a legacy database.

### 9.6 Public API contract details

The intended synchronous shape is:

```python
with MCPTestKit(config) as kit:
    with kit.direct(server, protocol=None, timeout=None) as client:
        direct_result = client.call_tool("tool", {"key": "value"})

    execution = kit.run(agent_spec)

    with kit.agent_session(agent_spec) as session:
        turn = session.send("message", timeout=None, metadata=None)
        snapshot = session.snapshot()
    terminal = session.result

    handle = kit.submit(agent_spec)
    for event in handle.events(after_sequence=0):
        ...
    handle.cancel()
    terminal = handle.result(timeout=None)
```

The async shape mirrors names and return models, changing only lifecycle and blocking operations to `await`/async iteration. Do not introduce different async-only domain models.

Behavioral rules:

- `kit.direct(...)` owns the connection it opens and closes it at context exit.
- The kit owns every child controller created through it unless ownership is explicitly transferred by a documented API.
- Closing a child removes it from the kit’s active-child registry only after cleanup completes.
- Closing the kit is idempotent.
- Closing the kit with active work initiates cancellation and bounded cleanup; it does not abandon subprocesses.
- A direct protocol operation returns a typed result for protocol-level success or MCP tool `is_error` and raises a typed exception for client misuse, JSON-RPC failure, transport loss, timeout, or infrastructure failure.
- Agent executions always retain a terminal `ExecutionResult`, including failed, timed-out, cancelled, and interrupted outcomes, so authors can assert failure behavior and inspect partial traces.
- `ExecutionHandle.result()` returns that terminal result; timeout while waiting raises a wait-timeout without changing the execution unless the caller explicitly cancels.
- `AgentSession.send()` returns a terminal `TurnResult` for that turn. Session-terminal infrastructure failure is represented in both the turn and session/execution result.
- `session.result` is a property, not a blocking wait; it raises `SessionStillOpen` until terminal.
- Snapshots and results are immutable values. Calling `.snapshot()` twice may return different values, but an earlier snapshot never mutates.
- `expect()` never writes evaluation records. `kit.evaluate()` is the only built-in persisted evaluation path.
- Required evaluation failure is surfaced by pytest/API integration while preserving the underlying execution outcome.

Do not add implicit fixture-teardown failure merely because an author created and ignored a failed execution. Python exceptions, explicit expectations, and required evaluations determine pytest failure. This preserves the ability to write tests whose expected result is a failed or interrupted execution.

### 9.7 Event and trace envelope

Each canonical event must contain at least:

- Schema identifier and schema version.
- Execution ID and monotonically increasing execution sequence.
- Event ID and event kind.
- UTC timestamp and monotonic offset from execution start.
- Session ID and turn ID when applicable.
- Server binding and connection identity when applicable.
- Typed JSON-RPC ID, direction, and per-connection request sequence when applicable.
- Lifecycle phase.
- Redacted semantic payload or artifact/blob reference.
- Provenance describing normalized, wire-observed, harness-reported, or derived origin.
- Raw-evidence reference when captured.

Rules:

- The canonical sequence is assigned once and never renumbered.
- A transaction may append multiple events, but consumers see none of them before commit.
- Canonical events are append-only. Corrections are new events or derived projections, not row updates that rewrite history.
- Derived snapshots/reports use the highest committed sequence and declare that sequence.
- Provider timestamps are payload evidence; local receipt time controls canonical ordering.
- Unknown valid provider events are retained as raw evidence and may produce a generic canonical provider event.
- Malformed provider events are captured as evidence and diagnostics without crashing the trace recorder.
- Trace completeness is `complete` only after owned cleanup and final persistence succeed. Otherwise it is `partial` with limitations.
- Raw stdout protocol channels and stderr diagnostics remain separate. Never parse diagnostic stderr as protocol data.

### 9.8 Persistence schema contract

The fresh database should model these logical records, regardless of exact SQLAlchemy class names:

- Server profiles and immutable server-profile revisions.
- Harness profiles and immutable harness-profile revisions.
- Executions and their resolved server/harness bindings.
- Interactive sessions and ordered turns.
- Append-only canonical events with unique `(execution_id, sequence)`.
- Raw-evidence metadata and blob references.
- Evaluation results.
- Collected artifacts and content-addressed blobs.
- Execution leases and heartbeats.
- Durable cancellation state.
- FIFO execution and interactive-turn commands.

Required constraints:

- Profile names follow one documented uniqueness policy; revisions are immutable after creation.
- An execution stores resolved revision IDs rather than a mutable “latest” pointer.
- Turn order is unique within a session.
- Event sequence is unique within an execution.
- Artifact/blob relationships use referential integrity.
- Only one active lease may own an execution.
- Commands have stable IDs so a claimed command cannot be executed twice after a retry.
- Terminal outcome and finish time are written atomically with final committed events where practical.
- Active executions cannot be hard-deleted.
- Deleting a terminal execution decrements blob references transactionally.

SQLite owns searchable metadata, not large payloads. Blob writes use write-to-temporary, flush, hash/length verification, and atomic placement before metadata commit. An uncommitted orphan blob is safe for later garbage collection; committed metadata must never point at a partial file.

The development reset recipe deletes the entire configured app-owned database and exact sidecars because the schema is fresh-only. It must not issue table-specific deletes, examine a schema version, or attempt recovery.

### 9.9 Execution and worker data flow

For submitted persistent execution:

1. Validate the public specification without resolving secrets.
2. Resolve `latest` profile references to immutable revisions.
3. Create the execution snapshot and persist `created` events.
4. Enqueue one durable FIFO execution command.
5. A worker atomically claims the command and execution lease.
6. Resolve secrets into process-local configuration.
7. Run capability/readiness preflight for only the requested components.
8. Create the isolated workspace, home/config/data roots, capture, proxies, and required MCP servers.
9. Start the selected harness and open one continuing session.
10. Process turns sequentially, persisting each semantic event before streaming it.
11. Run registered deterministic evaluations at their declared boundary.
12. Stop accepting turns and enter `closing`.
13. Collect workspace diff and declared artifacts.
14. Reap the harness, proxies, MCP servers, protocol readers, and other owned resources.
15. Persist cleanup evidence, final lifecycle, outcome, trace completeness, and report.
16. Release the lease only after terminal persistence.

For direct in-process execution, use the same event/result pipeline without queue/worker overhead unless the caller explicitly submits it to a persistent worker.

Do not resolve secrets during specification serialization, profile listing, cloning, API validation, or UI rendering. Do not persist the resolved child environment.

### 9.10 Turn, cancellation, and cleanup semantics

A turn command is accepted only while its session is nonterminal and not closing. `send()` additionally requires no in-flight turn. `enqueue_turn()` persists commands in order and returns a handle/identifier without pretending the turn has started.

Cancellation rules:

- Cancellation is idempotent.
- Cancelling queued work marks it cancelled without launching servers or a harness.
- Cancelling an active turn requests protocol/native graceful cancellation where supported.
- Cancellation then closes the entire agent session; do not promise the same conversation remains safe after cancellation.
- A request finishing before the cancellation boundary may complete normally; record the race outcome and both observed events.
- A caller wait timeout does not automatically cancel background work.
- Worker lease loss produces `interrupted`, not `cancelled` or `failed`.

Owned cleanup order is conceptually:

1. Stop accepting new turns and commands.
2. Request graceful cancellation of the in-flight turn/session.
3. Close harness protocol input and allow bounded graceful exit.
4. Terminate, then force-kill, the owned process group if necessary.
5. Stop capture proxies and close MCP client connections.
6. Stop owned MCP servers and readers.
7. Collect remaining diagnostics, workspace diff, and declared artifacts.
8. Verify process/thread/task reaping.
9. Persist cleanup limitations and terminal evidence.

Use configurable bounded grace periods, never unbounded waits. Cleanup errors must not erase the original failure. Preserve a primary error plus structured cleanup limitations.

### 9.11 Harness-specific preservation rules

#### ACP

- Reuse the existing stable ACP-v1 integration knowledge and deterministic subprocess fixtures.
- Treat stdout as ACP NDJSON only and stderr as diagnostics.
- Preserve raw ACP frames with direction, local receipt time, and monotonic offset.
- Preserve ACP-reported activity separately from captured MCP wire traffic.
- Tolerate unknown valid notifications and `_meta` fields while retaining them as raw evidence.
- Configure MCP servers once when opening the ACP session and retain one ACP session ID across sends.
- Do not call ACP authentication as a generic fallback. If the agent requires an unsupported interactive authentication flow, return a structured out-of-band-auth readiness failure.
- Preserve protocol and full probes, manifest validation, advertised modes/options, agent identity, and verification provenance through the new capability/profile services.
- A changed or missing optional identity becomes explicit readiness/provenance evidence; it does not silently select another agent.

#### Claude Code

- Replace the current one-shot runner with a single stream-JSON process capable of receiving multiple inputs.
- Keep the current strengths: isolated environment, explicit executable/model, structured event capture, wire capture, redaction, partial traces, and process-group cleanup.
- Verify required stream input/output and MCP configuration behavior against the installed binary during readiness.
- Do not use provider session resume as the portable multi-turn implementation.
- Report cost/usage only when emitted by Claude; do not estimate missing values.

#### OpenCode

- Replace one-shot `--pure run` execution with one isolated `opencode serve` instance and one attached session.
- Preserve hostile-home isolation, provider-aware saved-auth copying, explicit credential injection, MCP configuration rewriting, remote proxy cleanup, structured event capture, and incomplete-trace detection.
- Never copy arbitrary user OpenCode configuration or permission rules into the isolated environment.
- Stop and reap export/session/helper processes as well as the main server.
- Do not report max-turn or budget limits as enforced when OpenCode cannot enforce them.

#### All harnesses

- Use argument arrays with no shell invocation.
- Use fresh process groups/sessions so descendants can be terminated safely.
- Capture and redact stdout/stderr without blocking on full pipes.
- Disable ambient plugins, project discovery, and unrequested credentials.
- Record the exact executable, detected version, effective model, requested policy, observed policy, transport, and capability limitations.
- Preserve a partial trace even when setup fails after execution creation.

### 9.12 Tool-call evidence and assertion semantics

Represent at least three evidence cases distinctly:

1. Correlated wire request and response: authoritative observed MCP call with latency and result/error.
2. Wire request without response: authoritative attempted call with incomplete outcome.
3. Harness/provider claim without wire frame: reported activity, not authoritative MCP evidence.

Default `to_have_tool_call(...)` matches authoritative wire evidence. Authors may explicitly request reported-only evidence through a separate matcher or evidence selector; never broaden the default matcher silently.

Argument matching defaults to exact structural equality after model normalization. Partial, predicate, unordered-list, regex, or tolerant numeric matching must be explicitly selected.

Result matching distinguishes:

- Successful MCP response.
- MCP tool result with `is_error=True`.
- JSON-RPC error response.
- Cancelled/incomplete request.
- Transport failure without response.

Negative assertions search the complete declared scope and must not pass while relevant live work can still append matching events unless used through an explicit snapshot boundary.

### 9.13 Profile, revision, clone, and secret semantics

- Profiles are friendly mutable identities; their revisions are immutable snapshots.
- Editing a profile creates a new revision and advances its current pointer.
- Archiving hides a profile from normal selection but does not break historical executions.
- Execution creation resolves every profile reference and stores exact revision IDs.
- Cloning uses the original resolved revisions by default.
- Clone-with-latest is an explicit per-profile choice and is recorded in provenance.
- In-process factories and Python callbacks are runtime registrations, not serialized profile content.
- API/UI callers refer to registered names and revisions; Python callers may supply runtime-only values directly.
- Secret references contain provider/key identity or environment-variable name, never values.
- Profile validation may verify a secret reference exists only during explicit readiness/probe operations, not ordinary listing.

### 9.14 API route contract

Use resource-oriented `/api/v2` routes with shared model serialization. The minimum route families are:

```text
POST   /api/v2/executions
GET    /api/v2/executions
GET    /api/v2/executions/{execution_id}
GET    /api/v2/executions/{execution_id}/events
GET    /api/v2/executions/{execution_id}/events/stream
GET    /api/v2/executions/{execution_id}/report
POST   /api/v2/executions/{execution_id}/cancel
DELETE /api/v2/executions/{execution_id}

POST   /api/v2/sessions
GET    /api/v2/sessions/{session_id}
POST   /api/v2/sessions/{session_id}/turns
POST   /api/v2/sessions/{session_id}/close
POST   /api/v2/sessions/{session_id}/cancel

GET/POST and revision operations for server profiles
GET/POST and revision operations for harness profiles
POST probe operations for registered profiles/capabilities
```

Exact profile route nesting should follow one consistent resource pattern generated from shared service operations. Do not preserve `/api/v1` route shapes merely to reduce UI changes.

SSE rules:

- Send canonical sequence numbers as SSE IDs.
- Accept a cursor/last-event sequence and resume after it.
- Send committed events only.
- Avoid duplicate delivery across a normal reconnect.
- End after the terminal event and all prior committed events are delivered.
- Redact before serialization.

Idempotency applies only when a key is supplied. Reuse with an identical normalized request returns the original resource; reuse with a different normalized request returns `409`.

### 9.15 UI ownership and rendering rules

- Streamlit session state owns only browser draft/selection state and references to persisted executions.
- The cached toolkit owns worker/service infrastructure, not individual browser state.
- SDK/store state is authoritative after submission.
- UI reruns must not resubmit an execution or turn without a new user action/idempotency key.
- Polling or event consumption reads committed snapshots/events only.
- The UI never constructs provider-specific trace semantics; it renders backend-normalized models.
- Terminal trace rendering works for all outcomes and does not condition visibility on success.
- “Execution completed” means the lifecycle ended, not that tools succeeded or evaluations passed.
- MCP activity health derives only from authoritative MCP call results.
- Evaluations are separately labelled and never overwrite lifecycle or MCP health.
- Keep the initial one-turn/one-server form intentionally narrow without narrowing the underlying specification.

### 9.16 Pytest and artifact rules

- The plugin registers fixtures, markers, reporting hooks, and artifact policy only.
- Pytest owns collection, selection, parametrization, fixture scheduling, assertion rewriting, plugin loading, reporting, and exit codes.
- `mcp-pal test` invokes pytest in-process or through its official entry point and returns the exact pytest exit code.
- Wrapper arguments are not re-parsed except MCP Pal’s own non-conflicting wrapper options.
- Markers never cause automatic skip or retry.
- Deterministic repository CI explicitly excludes the separate live-test location/selection.
- When a live test is explicitly selected, readiness failure fails that test with diagnostics.
- Every pytest test gets isolated ephemeral state unless it explicitly requests a persistent/shared factory.
- Failure artifact directories use stable test-node-derived names plus collision-safe IDs.
- Artifact export happens after trace finalization and redaction.
- Failed-only export includes setup, call, teardown, timeout, cancellation, and cleanup failures associated with the test.
- JUnit contains only safe IDs, outcomes, and artifact paths; full trace payloads remain separate files.

### 9.17 Security, isolation, and redaction rules

- Never use `shell=True` or construct harness/server commands as shell strings.
- Treat profile commands, endpoints, attachments, workspace paths, and artifacts as untrusted input.
- Validate that collected artifact paths remain within the owned/allowed workspace after resolving symlinks.
- Do not follow workspace-copy symlinks outside the allowed source root by default.
- Bind SDK-hosted loopback servers to loopback only and use unpredictable per-execution authorization where feasible.
- Prevent remote agent-access endpoints from reaching private/local networks unless explicitly trusted.
- Pass a minimal child environment assembled from deterministic defaults and explicit allowlists.
- Do not inherit `.env`, ambient provider keys, user plugins, or project permission configuration accidentally.
- Redact recursively in mappings, lists, model objects, protocol frames, errors, reprs, logs, traces, artifacts, API responses, UI output, and telemetry.
- Redaction must handle exact secret values, configured sensitive keys, authorization headers, URLs with credentials/query tokens, and known credential-file content.
- Redaction failures fail persistence/export safely; they do not fall back to storing raw content.
- Raw evidence is subject to the same persistence redaction guarantee as canonical events.

### 9.18 Observability rules

- Internal logs may describe lifecycle, IDs, timings, component names, and redacted failures.
- Do not log full prompts, messages, tool arguments/results, protocol headers, raw frames, reasoning, or resolved child environments by default.
- Optional OpenTelemetry emits only lifecycle, timing, transport, harness, model identifier, server/tool identifier, status, and correlation metadata by default.
- Trace/artifact identifiers link operational telemetry to local evidence without copying sensitive payloads.
- Telemetry initialization is opt-in and cannot make SDK import or execution fail when unavailable.

### 9.19 Lower-capability-agent work protocol

For each todo item:

1. Read the relevant public contract, existing implementation, and existing tests before editing.
2. State the one observable behavior being added or replaced.
3. Add or update the smallest contract test that proves it.
4. Implement through the intended ownership layer without importing a higher integration layer.
5. Run the narrow test, its subsystem contract suite, and `git diff --check`.
6. Inspect process/filesystem/database side effects after lifecycle-related tests.
7. Record any upstream-version-specific behavior in capability evidence and documentation.
8. Stop after the checkbox is green; do not opportunistically implement later phases.

At every phase gate:

- Run all prior phase tests, not only the newest tests.
- Build and install the wheel when public imports or dependencies changed.
- Search for forbidden legacy/API/UI dependencies and accidental private imports.
- Check for leaked secrets, processes, threads, ports, workspaces, SQLite files, and generated files.
- Review new public names and schemas before treating them as stable.
- Update the capability-to-test/documentation manifest.
- Leave a runnable checkpoint suitable for another agent to continue.

Mandatory escalation conditions:

- The installed official MCP SDK lacks a required public capability or exposes materially different semantics.
- A selected harness cannot maintain one real conversation/process across turns.
- Wire capture cannot distinguish authoritative MCP calls from provider claims.
- Cross-process SQLite ownership cannot be made atomic under the chosen design.
- Redaction cannot be guaranteed before persistence/export.
- Cleanup cannot prove owned descendant processes are gone.
- A dependency cannot support Python 3.10 within the agreed bounds.
- A proposed shortcut would require a v1 compatibility layer, serialized scenario format, automatic skip/fallback, or deferred conformance functionality.

Do not resolve these conditions by weakening tests. Produce a focused feasibility note and revise the affected design branch explicitly.
