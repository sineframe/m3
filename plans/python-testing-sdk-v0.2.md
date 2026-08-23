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
