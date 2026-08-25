# MCP Pal v0.2 remediation and completion plan

Status: proposed implementation plan, 2026-08-25

This plan supersedes the completion claims in `python-testing-sdk-v0.2.md` until
the corresponding gates below pass. It does not discard that document's public
SDK design; it corrects the implementation sequence, package boundary, safety
properties, and evidence required to claim each milestone.

## 1. Outcome

Deliver a Python-native MCP testing SDK that:

- lets authors write ordinary sync or async pytest tests without scenario files;
- supports direct MCP testing and real multi-turn agent sessions;
- persists a complete redacted trace for every terminal outcome;
- safely supports multiple processes sharing SQLite-backed executions;
- uses real Claude Code, OpenCode, and ACP adapters without fixture-only behavior;
- exposes a thin pytest CLI wrapper without replacing pytest discovery;
- provides copyable, executable examples and complete SDK documentation;
- keeps the application layered on the SDK instead of shipping the application
  as part of the SDK package; and
- has truthful milestone status backed by deterministic and opt-in live gates.

The official MCP conformance wrapper remains a separately planned follow-up.
No conformance implementation, JSON/YAML scenario runner, legacy database
migration, or production database-reset command belongs in this remediation.

## 2. Non-negotiable implementation rules

- Never turn a confirmed regression test into a skip or non-strict xfail.
  Implement the fix, observe an XPASS, remove the marker, and rerun the gate.
- Do not mark a checkbox complete from unit tests alone when its acceptance test
  requires a real subprocess, separate OS process, installed wheel, or live
  harness.
- Preserve public exceptions and original causes. Cleanup failures may be added
  as secondary evidence but must not replace the primary failure.
- Redact before every persistence, export, log, API, and UI boundary. Secret
  reference descriptors are configuration, not secret values, and must survive
  durable round trips.
- Every terminal outcome—completed, failed, timed out, cancelled, interrupted,
  startup failed, and cleanup failed—must commit a reopenable trace.
- No native adapter may silently change the selected provider, model, MCP
  configuration, tool policy, workspace, or conversation mechanism.
- UI and API code may import the SDK. The SDK must never import UI or API code.
- Keep the deterministic suite credential-free. Live tests require an explicit
  marker and environment opt-in.
- Keep Python support at 3.10–3.13 and use uv-first commands with pip-compatible
  packaging.

## 3. Corrected repository and dependency boundary

Use two uv workspace projects:

```text
pyproject.toml                 # workspace and project-wide test orchestration
justfile                       # development commands only
sdk/
  pyproject.toml               # published mcp-pal distribution
  src/mcp_pal/                 # SDK only
  tests/
  examples/                    # copyable pytest modules
  docs/                        # complete SDK documentation
app/
  pyproject.toml               # private/non-published application project
  src/mcp_pal_app/
    api/                       # /api/v2 adapter over SDK services
    ui/                        # Streamlit client using SDK services directly
    settings.py                # explicit application configuration/.env entry
  tests/
```

Required moves and dependency changes:

- Move `mcp_pal.api`, `mcp_pal.ui`, `mcp_pal.main`, legacy application settings,
  and application-only persistence helpers into `mcp_pal_app`.
- Remove FastAPI, Streamlit, requests, Uvicorn, and application-specific settings
  dependencies from the SDK base and SDK extras.
- Keep `mcp-pal[storage]`, `[pytest]`, `[property]`, `[docs]`, and `[all]`.
  `[all]` means all SDK capabilities, not the separate application.
- Make the private app project depend on `mcp-pal[storage]` through the uv
  workspace during development.
- Build the SDK wheel and assert that it contains no `mcp_pal/api`,
  `mcp_pal/ui`, application entry point, Streamlit import, FastAPI import, or
  requests import.
- Keep root orchestration able to run `just api`, `just ui`, and combined tests.
- Do not preserve `/api/v1` compatibility. This repository is pre-production;
  start from the fresh v0.2 schema.

## 4. Remediation milestones

Each milestone ends with its listed gate. Later milestones must not begin while
an earlier required gate is red, except for documentation work that cannot
affect runtime behavior.

### R0 — Correct status, freeze regressions, and establish reproducibility

- [ ] Change the primary plan's status from “phases 0–12 complete” to a factual
  remediation status and link to this document.
- [ ] Record the exact failing full-suite result and live OpenCode result.
- [ ] Keep the new deterministic E2E regressions as strict xfails:
  ACP tool identity/policy, persistent artifacts, and durable cancellation.
- [ ] Keep the live OpenCode test opt-in and un-xfailed so live breakage is loud.
- [ ] Register `e2e` and `live` markers in both repository-root and SDK-local
  pytest configurations.
- [ ] Add a test manifest mapping every finding in section 7 to a regression
  test path and expected gate.
- [ ] Ensure test processes have bounded cleanup so a failed cancellation test
  cannot leave MCP or harness children alive.

Gate:

```bash
git diff --check
uv run --project sdk --all-extras pytest -q sdk/tests/e2e/test_sdk_workflows.py
```

Expected interim result: passing tests plus exactly the documented strict
xfails. Any additional failure blocks the next milestone.

### R1 — Restore direct-client and execution correctness

#### Direct trace shutdown

- [ ] Make `_ObservedReadStream` translate AnyIO `EndOfStream` to normal async
  iterator exhaustion.
- [ ] Preserve the original in-process server exception when
  `raise_server_exceptions=True`.
- [ ] If cleanup also fails, record cleanup evidence without replacing or
  grouping away the original exception.
- [ ] Cover server failure during initialization, list, tool call, and shutdown.
- [ ] Assert no “Future exception was never retrieved” log is emitted.

#### Make `DirectExecutionSpec` an actual execution

Add a serializable discriminated `DirectOperation` union. Initial operations:

- `ListToolsOperation`, `ListResourcesOperation`, `ListPromptsOperation`;
- `CallToolOperation(name, arguments)`;
- `ReadResourceOperation(uri)`;
- `GetPromptOperation(name, arguments)`; and
- `PingOperation`.

Implementation requirements:

- [ ] Add `operation: DirectOperation` to `DirectExecutionSpec`; do not allow a
  setup-only spec to validate as an execution.
- [ ] Execute the operation against an explicitly selected server binding.
- [ ] Persist a typed operation result in `ExecutionResult` without discarding
  the official MCP response available through `.raw` in-process.
- [ ] Support one operation per serializable execution for v0.2. Authors needing
  arbitrary sequences use `kit.direct(...)` in normal Python tests; do not add
  a scenario mini-language.
- [ ] Validate duplicate server aliases and require an explicit server selector
  when more than one server is bound.
- [ ] Test success, MCP tool `is_error`, JSON-RPC error, timeout, cancellation,
  schema validation failure, startup failure, and cleanup failure.
- [ ] Test sync/async parity and JSON round trips for every operation variant.

Gate:

- The previously failing in-process exception test passes.
- A black-box `kit.run(DirectExecutionSpec(...CallToolOperation...))` calls a
  real stdio MCP and returns the tool result and complete trace.
- No setup-only direct execution can report `completed`.

### R2 — Make tracing authoritative for every SDK surface

- [ ] Give direct clients, submitted executions, and interactive agent sessions
  one shared canonical recorder/finalization contract.
- [ ] Construct `AgentSession` terminal results only after the terminal event is
  committed and the trace is finalized.
- [ ] Set `ExecutionResult.trace` for direct `agent_session()` use, not only for
  `kit.submit()` wrappers.
- [ ] Ensure the execution ID used by the recorder, workspace, artifacts,
  evaluations, and outer submitted handle is identical.
- [ ] Preserve partial traces when adapter startup, MCP startup, a turn,
  cancellation, timeout, or cleanup fails.
- [ ] Make finalization idempotent and reject events appended after the terminal
  event.
- [ ] Reopen every persistent trace through a second `SQLiteExecutionStore`
  instance and compare the canonical event sequence.
- [ ] Add outcome-matrix tests covering completed, failed, timed out, cancelled,
  interrupted, startup failed, and cleanup failed.
- [ ] Assert redaction is identical in SDK results, SQLite, API responses, SSE,
  and UI projections.

Gate: every terminal outcome has a non-null, reopenable trace whose final event
contains the same outcome as its execution snapshot.

### R3 — Repair secret handling before further live execution

#### Durable specifications and profiles

- [ ] Introduce a specification/profile serializer distinct from evidence
  redaction.
- [ ] Preserve `SecretReference(source, name)` descriptors exactly through
  model → SQLite → model round trips.
- [ ] Reject literal values under credential-bearing durable fields where a
  reference is required; never replace them with a runnable `[REDACTED]`
  literal.
- [ ] Resolve references only in the worker that owns execution and keep the
  resolved value out of specs, events, errors, reprs, and database rows.
- [ ] Test stdio environment, HTTP headers, bearer tokens, harness credentials,
  saved profiles, queued commands, and cloned executions in a second process.

#### Capture-time canaries

- [ ] Centralize the sensitive-key predicate and use it for configuration,
  stdio environment, HTTP headers, proxies, logs, and artifacts.
- [ ] Recognize normalized names including `X-API-Key`, `*_API_KEY`, vendor API
  keys, bearer/auth headers, cookies, tokens, passwords, and credentials.
- [ ] Register all resolved reference values and classified literal values as
  process-local redaction canaries before any server or harness starts.
- [ ] Ensure HTTP proxy writers receive the same process-local canary set.
- [ ] For stdio proxy subprocesses, resolve/read secrets inside the proxy from a
  mode-0600 ephemeral handoff, register them in that proxy's writer, and delete
  the handoff before terminal completion. Never place values in argv.
- [ ] Redact secrets echoed in assistant text, MCP results, JSON-RPC errors,
  stderr, raw capture, artifact bytes, API JSON, SSE, and UI previews.
- [ ] Test secrets split across structured fields and confirm documented limits
  for transformed/encoded values.

Gate: repository/database/blob/capture scans find none of the test canaries;
cross-process authenticated executions still receive the original credentials.

### R4 — Fix workspace ownership and persistent artifacts

- [ ] Inject `store.artifacts` into every `WorkspaceManager`; never silently
  create an in-memory artifact store for a persistent execution.
- [ ] Use the outer execution ID for nested agent-session workspace capture.
- [ ] Pass `HarnessLaunch.workspace_root` to every real adapter.
- [ ] Keep HOME/XDG/config isolation in a separate adapter-control directory,
  while launching the harness with the SDK workspace as its working directory.
- [ ] Pass the same workspace to ACP `session/new`, OpenCode's server/session
  context, Claude's continuous process, and all stdio MCP children unless a
  server has an explicit safe cwd.
- [ ] Capture workspace changes and declared artifacts before process teardown
  removes their files.
- [ ] Persist artifact metadata and bytes before publishing terminal results.
- [ ] Reopen artifact bytes using a separate process and store instance.
- [ ] Test COPY, TEMPORARY, GIT_WORKTREE, READ_ONLY, and acknowledged IN_PLACE
  policies against real harness subprocesses.
- [ ] Test exclusions for `.git`, environments, caches, secret files, and the
  artifact store itself.
- [ ] Test successful and failed artifact policies (`always`, `failed`, `never`).

Gate: remove the strict artifact xfail after a separate worker writes an
artifact, exits, and a producer process reopens and verifies its bytes/hash.

### R5 — Make persistence, GC, leases, and cancellation process-safe

#### Durable cancellation

- [ ] Introduce a cancellation token shared through execution runtime, agent
  session, direct transport, harness adapter, workspace capture, and cleanup.
- [ ] Have the persistent owner watch the SQLite cancellation flag while work is
  active, not only before and after the runner.
- [ ] On cancellation, stop accepting turns, cancel pending MCP/model requests,
  terminate owned process groups, retain partial evidence, finalize as
  `cancelled`, complete the queue command, and release the lease.
- [ ] If termination exceeds the grace period, escalate and record cleanup
  failure without changing the cancellation outcome.
- [ ] Treat lease loss similarly but finalize as `interrupted`; never resume the
  same provider conversation under a replacement owner.
- [ ] Make cancellation idempotent before claim, during startup, during MCP
  initialization, during a tool call, during a model turn, and during cleanup.

#### Blob publication and garbage collection

- [ ] Add a cross-process lock or equivalent database-coordinated publication
  protocol covering blob publication, metadata commit, deletion, and GC.
- [ ] Write new blobs to unique temporary files and publish with atomic rename.
- [ ] Never let GC delete a blob that has an active/pending publication.
- [ ] Re-read reference state immediately before unlinking and use an age/grace
  rule for crash leftovers.
- [ ] Remove unconditional synchronous GC from each artifact deletion; expose a
  safe maintenance operation and bounded opportunistic cleanup.
- [ ] Recover stale temporary/pending blobs after simulated process crashes.
- [ ] Force the writer/GC interleaving from two independent store instances for
  artifact and large-event blobs.

#### Shared-store contract

- [ ] Run one behavioral contract against memory and SQLite stores.
- [ ] Cover atomic claims, FIFO queues, idempotent submission, event streaming,
  callbacks, sequence reservations, cancellation, heartbeats, lease expiry,
  stale-owner interruption, clone/delete, artifact references, and recovery.
- [ ] Run the contract across threads and real OS processes where ownership is
  relevant.

Gate: remove the strict cancellation xfail; pass forced blob interleavings and
the complete cross-process store contract without leaked workers or children.

### R6 — Repair real harness adapters

#### Common native-process behavior

- [ ] Change bounded stderr handling to retain at most the configured diagnostic
  amount while continuing to drain until EOF.
- [ ] Preserve a small sanitized tail/reference for diagnostics without placing
  provider output or secrets in exceptions.
- [ ] Make process-group termination tolerate `ESRCH`, `EPERM`, already-reaped
  children, and PID/group races without retrying an unsafe target.
- [ ] Assert the target PID/group belongs to the owned child before signaling.
- [ ] Run 50 repeated cancellation/timeout iterations per native fixture and
  fail on flakiness or leaked children.

#### Portable policy enforcement

- [ ] Enforce portable MCP tool policy in SDK-owned MCP proxies before forwarding
  `tools/call`, independent of whether the harness has a native allowlist.
- [ ] Normalize every observed call to `(server alias, tool name)` at the proxy.
- [ ] Correlate adapter-reported calls with canonical proxy captures; do not
  reject a valid single-server call merely because an ACP update gave only the
  tool name.
- [ ] Reject ambiguous unqualified calls when multiple servers expose the same
  tool name.
- [ ] Record requested, enforced, observed, denied, and unavailable policy
  evidence separately.
- [ ] Advertise `supports_tool_policy=True` only after the common policy contract
  passes for that adapter and transport.

#### ACP

- [ ] Use the real workspace in process cwd and ACP session creation.
- [ ] Preserve one ACP process, connection, session ID, and MCP process set
  across at least three turns.
- [ ] Normalize ACP tool-call updates without trusting them as enforcement.
- [ ] Test tool error recovery, attachments rejection, cancellation, timeout,
  connection loss, duplicate tool names, and complete traces.

#### OpenCode

- [ ] Implement an explicit OpenCode configuration dialect abstraction.
- [ ] Detect supported dialect/capabilities during readiness rather than writing
  Claude `mcpServers` syntax.
- [ ] Render legacy OpenCode `mcp` and current V2 `mcp.servers` forms exactly as
  their official schemas require; unsupported dialects fail readiness.
- [ ] Send the selected provider/model in every session message using the
  official server API model reference.
- [ ] Parse official `{info, parts}` responses, including text, tool parts,
  errors, finish state, tokens, and cost when actually reported.
- [ ] Keep authentication explicit. Add typed credential references to the
  OpenCode harness specification; do not copy ambient global auth unless the
  user selects a documented opt-in saved-auth mode.
- [ ] Verify the selected provider is connected before the first paid turn and
  fail with a sanitized readiness error otherwise.
- [ ] Start `opencode serve` in the SDK workspace while keeping its HOME/XDG and
  config/data directories isolated.
- [ ] Replace fixture-only response shapes with fixtures captured from the
  documented contract for each supported dialect.

#### Claude Code

- [ ] Apply the same workspace, stderr, process ownership, portable policy,
  secret, trace, and multi-turn contracts.
- [ ] Retain only the continuous stream-JSON mechanism; missing streaming remains
  a readiness failure with no resume/one-shot fallback.

Gate:

- Deterministic common harness contract passes for ACP, Claude, and OpenCode.
- Real ACP multi-turn E2E loses its xfail.
- Opt-in live OpenCode calls the test MCP across two turns using the requested
  model, preserves the session, records tool traffic, and finalizes a trace.
- Live gates remain separate from required deterministic CI.

### R7 — Separate and rebuild the application on SDK services

#### Application configuration

- [ ] Create `mcp_pal_app.settings` with one explicit application `.env` loading
  entry point.
- [ ] Define one database setting name and use it consistently in API, UI,
  workers, reset tooling, `.env.example`, README, tests, and Just recipes.
- [ ] Keep SDK library imports free from automatic `.env` loading.
- [ ] Test explicit file, ambient override, missing file, invalid file, and
  redacted error behavior.

#### API v2

- [ ] Implement `/api/v2` exclusively as serialization/streaming adapters over
  SDK models, profile services, execution services, stores, and workers.
- [ ] Remove `/api/v1` and its table-purge/compatibility code.
- [ ] Support execution create/list/get/cancel/delete, terminal reports,
  sequence-resumable SSE, profile revisions, and interactive session operations.
- [ ] Return stable typed error envelopes and reject arbitrary executable Python
  objects or pickled data.
- [ ] Ensure API cancellation reaches work owned by another process.
- [ ] Generate OpenAPI and round-trip it against shared Pydantic models.

#### Streamlit

- [ ] Remove `requests`, `MCP_PAL_API_URL`, and every local HTTP call.
- [ ] Construct/cache one SDK toolkit and use SDK services directly.
- [ ] Preserve one-turn UI behavior, browser-session draft isolation, profile
  forms, clone, cancel, history, deletion, and reports.
- [ ] Display lifecycle, MCP activity health, and evaluations separately.
- [ ] Display persisted traces for every terminal outcome, including successful
  runs, without loading unbounded blobs into the page.
- [ ] Keep multi-turn and multi-server UI controls deferred; their SDK support
  must still be complete and documented.
- [ ] Test UI behavior with injected in-memory/SQLite SDK kits instead of mocked
  HTTP requests.

#### Development reset

- [ ] Keep reset as a development-only Just recipe, not an installed CLI.
- [ ] Use a Just invocation that actually works, preferably
  `just dev-db-reset` with an interactive/repository-local safety guard or the
  conventional `just CONFIRM=reset dev-db-reset` ordering if confirmation is
  retained.
- [ ] Preflight the database and exact `-wal`/`-shm` sidecars completely before
  deleting any file.
- [ ] Refuse symlinks, directories, paths outside the repository, broad paths,
  and non-SQLite suffixes.
- [ ] Test fresh startup, no-file reset, all-sidecar reset, every refusal path,
  and failure atomicity.

Gate: the app project passes independently; the SDK wheel contains no app code;
the UI submits through SDK services and reopens/displays traces for every
outcome; `/api/v1` is absent.

### R8 — Complete pytest integration and thin CLI

- [ ] Implement function-scoped `mcp_test` and `async_mcp_test` fixtures.
- [ ] Give every test isolated ephemeral store, workspace, artifact directory,
  portal, ports, and process ownership.
- [ ] Add explicit reusable session factories without hidden session-scoped
  conversations.
- [ ] Support xdist worker isolation.
- [ ] Export artifacts/traces on failure by default, with `always` and `never`
  configuration.
- [ ] Print concise safe trace/artifact references in pytest failure output.
- [ ] Put only safe IDs and paths—not trace bodies—in JUnit properties.
- [ ] Implement `mcp-pal test` as a transparent `pytest` wrapper that passes
  through arguments and exact exit codes.
- [ ] Do not implement separate discovery, fixtures, parametrization, scenario
  files, or result semantics.
- [ ] Test selection, `-k`, markers, parametrization, xfail, coverage, JUnit,
  xdist, Ctrl-C, missing extras, and missing explicitly selected harnesses.
- [ ] Prove `pytest ...` and `mcp-pal test ...` collect and execute the same tests.

Gate: clean-wheel consumers can use either command with equivalent behavior.

### R9 — Build documentation and executable author examples

Create the complete Diátaxis tree under `sdk/docs/`:

```text
docs/
  index.md
  tutorials/
  how-to/
  reference/
  explanation/
  adr/
  schemas/
```

- [ ] Configure MkDocs Material, mkdocstrings, strict links, strict snippets,
  API export coverage, and versioned documentation.
- [ ] Replace the placeholder SDK README with uv-first and pip alternatives,
  one minimal direct pytest test, one deterministic ACP test, and links onward.
- [ ] Write tutorials for first direct test, deterministic agent test, true
  multi-turn state, multiple servers, tracing, and pytest setup.
- [ ] Write how-to guides for all transports, auth, secret references, harnesses,
  policies, workspaces, artifacts, storage, replay/faults, API deployment, UI
  development, CI, and live tests.
- [ ] Generate complete API, model, exception, configuration, environment,
  marker, CLI, event, trace, and schema references.
- [ ] Explain direct versus agent testing, lifecycle/outcome/activity health,
  persistence, redaction, isolation, and evaluation semantics.
- [ ] Write ADRs for SDK/app separation, official MCP client ownership,
  sync/async twins, pytest-native authoring, no scenarios, trace retention,
  multi-turn guarantees, fresh DB break, SQLite leases, and separate conformance.
- [ ] Generate `llms.txt` and `llms-full.txt` from maintained documentation.
- [ ] Include explicit credential, cost, privacy, and nondeterminism warnings in
  every live example.
- [ ] Add only a linked TODO for the separately planned official MCP conformance
  wrapper.

Executable examples must be ordinary pytest files and include:

- direct initialization and capabilities;
- tools: list/pagination/call/error/schema validation;
- resources, templates, reads, subscription where supported;
- prompts and completion;
- in-process, stdio, Streamable HTTP, and SSE transports;
- auth and SecretReference usage;
- sync and async styles;
- mock server expectations, gates, clocks, faults, record/replay;
- one-turn and true stateful multi-turn ACP;
- multiple servers, duplicate tool names, and routing;
- portable tool policy allow/deny and permission handlers;
- workspace policies, diffs, artifacts, and cleanup;
- assertions, soft checks, snapshots, and evaluations;
- ephemeral and persistent stores;
- separate-worker submission, streaming, cancellation, and recovery;
- successful, failed, timeout, cancellation, interruption, startup failure, and
  cleanup-failure traces; and
- separate opt-in Claude/OpenCode live examples.

Every expected-failure example must pass by asserting the exact documented
failure. Examples must import only public APIs from a freshly installed wheel.

Gate: strict docs build passes; all snippets execute; the installed-wheel E2E
corpus covers every public export/capability recorded in a coverage manifest.

### R10 — CI, portability, and release-candidate gate

- [ ] Add required Linux jobs for Python 3.10, 3.11, 3.12, and 3.13.
- [ ] Add focused macOS and Windows jobs for paths, subprocesses, process groups,
  ports, stdio, and cancellation.
- [ ] Add Ruff format/lint checking and full strict mypy.
- [ ] Run unit, integration, contract, API, UI, deterministic E2E, installed
  wheel, and documentation jobs separately.
- [ ] Install and smoke-test every SDK extra independently.
- [ ] Validate wheel/sdist contents, metadata, schemas, legal files, and absence
  of app modules from the SDK wheel.
- [ ] Detect leaked processes, ports, workspaces, databases, threads, tasks, and
  event loops.
- [ ] Run dependency/vulnerability and repository-diff checks.
- [ ] Keep live harness jobs manual/credentialed and never required for ordinary
  contributor PRs; record their characterization result for release candidates.
- [ ] Prepare but do not activate/publish through PyPI Trusted Publishing without
  separate authorization.

Release candidate acceptance:

- zero unexpected failures, skips, xfails, resource warnings, or leaked owners
  in required deterministic jobs;
- all strict regression xfails removed after their fixes;
- complete traces for every terminal outcome;
- cross-process artifacts and cancellation proven;
- ACP deterministic three-turn session proven;
- live OpenCode characterized successfully with exact requested model/provider;
- SDK wheel contains SDK only and works on Python 3.10–3.13;
- UI uses SDK services directly and displays successful and unsuccessful traces;
- API v2 shares the same SDK store/worker/models;
- pytest and `mcp-pal test` remain equivalent;
- documentation and installed-wheel examples pass; and
- no scenario system, conformance wrapper, legacy DB compatibility, hidden judge,
  or unauthorized publication is present.

## 5. Pull-request slicing

Keep changes reviewable in this order:

1. Status correction and regression-test preservation.
2. Direct trace shutdown and original-exception preservation.
3. Direct operation model and real `DirectExecutionSpec` execution.
4. Shared trace finalization for agent sessions/submitted executions.
5. Secret-reference persistence and transport canaries.
6. Persistent artifact-store injection and execution-ID ownership.
7. Durable cancellation and lease-loss propagation.
8. Blob publication/GC coordination and shared store contract.
9. Native process/workspace/stderr ownership.
10. Portable proxy-level policy enforcement and ACP repair.
11. OpenCode contract/dialect/model/auth/response repair.
12. Claude contract completion and native stress tests.
13. SDK/application workspace split.
14. API v2 adapter and removal of v1.
15. Direct-SDK Streamlit migration and reset/config repair.
16. Pytest fixtures and thin CLI.
17. Documentation and executable example corpus.
18. CI/platform/package/release gates.

Do not combine security/persistence fixes with the repository move. First make
the behavior correct and protected by tests, then move modules mechanically.

## 6. Required verification commands

The exact Just recipes may evolve with the workspace split, but the final plan
must provide equivalents for:

```bash
just test-unit
just test-integration
just test-e2e
just test-app
just typecheck
just lint
just docs-check
just package-check
just check
```

Additional explicit gates:

```bash
uv run --project sdk --all-extras pytest -q sdk/tests/e2e/test_sdk_workflows.py
uv build --project sdk --no-sources
MCP_PAL_RUN_LIVE_OPENCODE=1 \
  uv run --project sdk --all-extras pytest -q sdk/tests/e2e/test_live_opencode.py
```

Packaging verification must install artifacts into clean environments rather
than importing from repository `PYTHONPATH`.

## 7. Finding-to-work mapping

| Confirmed finding | Owner milestone | Mandatory proof |
|---|---:|---|
| ACP rejects real MCP tool identity/policy | R6 | real ACP + real MCP multi-turn E2E |
| Live OpenCode startup/config failure | R6 | opt-in live two-turn E2E |
| OpenCode ignores model/provider | R6 | server request assertion + live trace |
| OpenCode fixture-only response parsing | R6 | official response contract fixtures |
| Persistent artifact refs lose bytes | R4 | separate-process reopen/read |
| Durable cancel does not interrupt owner | R5 | separate-process child termination |
| SecretReference becomes `[REDACTED]` | R3 | SQLite/process round trip |
| API-key literals miss canary registration | R3 | echoed HTTP/stdio secret scans |
| Proxy writer loses resolved canaries | R3 | proxy capture scan |
| DirectExecutionSpec performs no operation | R1 | real stdio operation E2E |
| AgentSession result has no trace | R2 | direct session outcome matrix |
| Native harness ignores workspace | R4/R6 | real-process workspace tests |
| Native stderr drain can deadlock | R6 | output above pipe/retention limits |
| Blob GC races publication | R5 | forced two-store interleaving |
| AnyIO EndOfStream replaces server error | R1 | original-exception regression test |
| Process-group cancellation is flaky | R6 | repeated stress gate |
| Application `.env` setup is broken | R7 | app-entry configuration tests |
| Reset variable/command/atomicity mismatch | R7 | command and refusal matrix |
| UI/API ship in SDK wheel | R7 | wheel-content rejection test |
| UI still calls legacy HTTP API | R7 | injected SDK UI tests; no requests import |
| `/api/v1` remains | R7 | route/source absence tests |
| SDK docs are placeholders | R9 | strict docs/snippet gate |
| Copyable pytest examples are absent | R9 | installed-wheel example corpus |
| Pytest plugin is empty | R8 | fixture/isolation tests |
| `mcp-pal test` is absent | R8 | pytest equivalence tests |
| Full suite/status claims are inaccurate | R0/R10 | truthful recorded green gates |

## 8. Definition of done

This remediation is complete only when every R0–R10 checkbox and gate passes,
the original plan links to the evidence rather than repeating unverified pass
counts, and no confirmed finding in section 7 remains hidden behind an xfail,
skip, fixture-only adapter, or deferred cleanup note.

