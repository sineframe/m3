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

### Current functional priority order

Remediation work is ordered by user-visible runtime impact:

1. ACP real-MCP lifecycle and recovery.
2. Secret pre-spawn registration and redaction safety.
3. Durable cross-process cancellation and API v2 execution behavior.
4. Terminal-result finalization and reopenability.
5. Requested/enforced/observed/denied/unavailable policy evidence.
6. Native workspace and exclusion verification.
7. Application settings, reset behavior, and OpenAPI correctness.
8. Direct-SDK UI migration and API v1 removal.
9. Thin CLI and documentation polish.

This order is intentionally functional: CLI and documentation work must not
displace adapter, persistence, policy, or application runtime correctness.

Each milestone ends with its listed gate. Later milestones must not begin while
an earlier required gate is red, except for documentation work that cannot
affect runtime behavior.

### R0 — Correct status, freeze regressions, and establish reproducibility

- [x] Change the primary plan's status from “phases 0–12 complete” to a factual
  remediation status and link to this document.
- [x] Record the exact failing full-suite result and live OpenCode result.
- [x] Keep the new deterministic E2E regressions as strict xfails during the
  initial baseline, then remove each marker only after its acceptance gate
  passes. ACP tool identity/policy, persistent artifacts, and durable
  cancellation are now un-xfailed; the manifest records their current status.
- [x] Keep the live OpenCode test opt-in and un-xfailed so live breakage is loud.
- [x] Register `e2e` and `live` markers in both repository-root and SDK-local
  pytest configurations.
- [x] Add a test manifest mapping every finding in section 7 to a regression
  test path and expected gate.
- [x] Ensure test processes have bounded cleanup so a failed cancellation test
  cannot leave MCP or harness children alive.

  Evidence (R0 manifest): `sdk/tests/regression_manifest.json` contains one
  unique entry for each exact section-7 finding, with milestone, factual
  status, regression path, and gate expectation. The manifest integrity test
  enforces one-to-one finding/milestone coverage, existing nodes for current
  entries, no remaining strict xfails, and one live OpenCode entry that remains
  un-xfailed. The manifest test passed;
  deterministic E2E and live E2E modules collected successfully without
  executing the live call.

Round 1 evidence, independently reviewed:

- Initial remediation baseline: 2 failed, 746 passed, 1 skipped, 3 xfailed.
- Original in-process exception regression: 20 consecutive passes.
- Direct/trace/cancellation review set: 26 passed.
- Full post-fix suite: 749 passed, 1 skipped, 3 strict xfailed, 38 expected
  pinned-MCP deprecation warnings.
- Live OpenCode remained opt-in and made no model request during the recorded
  failed characterization.

Gate:

```bash
git diff --check
uv run --project sdk --all-extras pytest -q sdk/tests/e2e/test_sdk_workflows.py
```

Expected interim result: passing tests plus exactly the documented strict
xfails. Any additional failure blocks the next milestone.

### R1 — Restore direct-client and execution correctness

#### Direct trace shutdown

- [x] Make `_ObservedReadStream` translate AnyIO `EndOfStream` to normal async
  iterator exhaustion.
- [x] Preserve the original in-process server exception when
  `raise_server_exceptions=True`.
- [x] If cleanup also fails, record cleanup evidence without replacing or
  grouping away the original exception.
- [x] Cover server failure during initialization, list, tool call, and shutdown.
- [x] Assert no “Future exception was never retrieved” log is emitted.

  Evidence (round 2C/R1 follow-up): in-process server-task settlement now uses
  an explicit bounded signal instead of scheduler-checkpoint polling. Cancelled
  lifecycle startup also consumes or cancels its owner future. The original
  exception stress case ran 50 iterations under scheduler noise, and the
  cancelled-enter test has a local log-drain assertion.

  Evidence (round 2F): deterministic official in-process `Server` fixtures
  cover initialization-option, `list_tools`, `call_tool`, and post-run
  shutdown failures. Focused tests prove `raise_server_exceptions=True`
  preserves the original canary and `False` returns sanitized public errors;
  each path closes with a terminal partial trace and no unretrieved-future
  log. Cleanup tests prove a primary server exception is preserved while
  `cleanup_failed` evidence is recorded, and standalone cleanup failures keep
  their original exception. The direct/local/integration focus passed 42
  tests; the scheduler-load, cancellation, and shutdown subset passed 12
  tests; strict mypy and `git diff --check` passed. The final independent R1
  acceptance run passed 789 tests, with 1 expected skip and exactly the 3
  documented strict xfails.

#### Make `DirectSpec` an actual execution

Add a serializable discriminated `DirectOperation` union. Initial operations:

- `ListTools`, `ListResources`, `ListPrompts`;
- `CallTool(name, arguments)`;
- `ReadResource(uri)`;
- `GetPrompt(name, arguments)`; and
- `Ping`.

Implementation requirements:

- [x] Add `operation: DirectOperation` to `DirectSpec`; do not allow a
  setup-only spec to validate as an execution.

  Evidence note (round 2A): the frozen discriminated operation and typed result
  models, JSON schemas, required-spec field, single-/multi-server selector and
  effective-alias validation, stable value-model identities, typed result
  variants with excluded in-process `raw` responses, and checked public exports
  are implemented and covered by focused model/client tests
  (`sdk/tests/unit/test_direct_operation_models.py`). Luna's focused set and
  the independent expanded review gate passed 63 and 108 tests respectively.
  Runtime execution was intentionally left open for the following round.

  Evidence note (round 2B): in-process async/sync dispatch now covers all
  eight operation variants, cursor/all-pages pagination, explicit binding
  selection, typed `ExecutionResult.direct_result`, official response
  retention in `.raw`, and MCP tool `is_error` as a completed typed result.
  Focused dispatch tests (including selection of a requested second binding),
  real-stdio sync/async `kit.run`, strict mypy, and parity checks pass. The
  independently reviewed full gate passed 760 tests, with 1 expected skip and
  exactly the 3 documented strict xfails. Profile bindings remain explicitly
  unsupported until resolution exists.
- [x] Execute the operation against an explicitly selected server binding.
- [x] Persist a typed operation result in `ExecutionResult` without discarding
  the official MCP response available through `.raw` in-process.

  Evidence (round 2D): terminal `execution.finished` evidence now carries the
  redacted JSON direct result with excluded `raw`; persistent producer handles
  reconstruct the discriminated typed result with `raw=None`. The embedded
  worker and separate-OS-worker stdio tests cover success, MCP `is_error`, and
  failed JSON-RPC operations, and a reopened SQLite store validates the
  persisted discriminator and fields (`sdk/tests/e2e/test_sdk_workflows.py`,
  `sdk/tests/unit/test_direct_operation_outcomes.py`). A runtime redaction test
  proves official `raw` remains process-local while canaries in content and
  structured content are absent from terminal JSON. The independent full
  acceptance run passed 770 tests, with 1 expected skip and exactly the 3
  documented strict xfails.
- [x] Support one operation per serializable execution for v0.2. Authors needing
  arbitrary sequences use `kit.direct(...)` in normal Python tests; do not add
  a scenario mini-language.
- [x] Validate duplicate server aliases and require an explicit server selector
  when more than one server is bound.
- [x] Test success, MCP tool `is_error`, JSON-RPC error, timeout, cancellation,
  schema validation failure, startup failure, and cleanup failure.

  Evidence (round 2C): the real-stdio outcome cases use the wire fault fixture
  for JSON-RPC error, operation delay, and invalid output schema, plus a real
  missing executable for startup; the bounded runtime outcome tests cover
  active cancellation and workspace cleanup failure while retaining typed
  direct results where appropriate (`sdk/tests/e2e/test_sdk_workflows.py`,
  `sdk/tests/unit/test_direct_operation_outcomes.py`). Focused outcome,
  dispatch, model, and wire-fault tests passed (18); the five real-stdio
  outcome/dispatch tests passed (5). After two full-suite runs exposed and
  fixed the scheduler race and abandoned lifecycle future, the independent
  acceptance run passed 769 tests, with 1 expected skip and exactly the 3
  documented strict xfails.
- [x] Test sync/async parity and JSON round trips for every operation variant.

  Evidence (round 2E): a strictly typed, real-stdio E2E matrix runs all eight
  operation variants through both `MCPTestKit.run` and
  `AsyncMCPTestKit.run`, compares raw-excluded typed JSON, and validates each
  result through the discriminated adapter. A ninth case covers an explicit
  cursor with `all_pages=False`. The matrix, strict mypy on the E2E module,
  and the public parity checker all pass.

Gate:

- The previously failing in-process exception test passes.
- A black-box `kit.run(DirectSpec(...CallTool...))` calls a
  real stdio MCP and returns the tool result and complete trace.
- No setup-only direct execution can report `completed`.

### R2 — Make tracing authoritative for every SDK surface

- [x] Give direct clients, submitted executions, and interactive agent sessions
  one shared stable recorder/finalization contract.
- [ ] Construct `AgentSession` terminal results only after the terminal event is
  committed and the trace is finalized.
- [x] Set `ExecutionResult.trace` for direct `agent_session()` use, not only for
  `kit.submit()` wrappers.
- [x] Ensure the execution ID used by the recorder, workspace, artifacts,
  evaluations, and outer submitted handle is identical.
- [x] Preserve partial traces when adapter startup, MCP startup, a turn,
  cancellation, timeout, or cleanup fails.
- [x] Make finalization idempotent and reject events appended after the terminal
  event.
- [ ] Reopen every persistent trace through a second `SQLiteExecutionStore`
  instance and compare the stable event sequence.
- [x] Add outcome-matrix tests covering completed, failed, timed out, cancelled,
  interrupted, startup failed, and cleanup failed.
- [ ] Assert redaction is identical in SDK results, SQLite, API responses, SSE,
  and UI projections.

  Evidence (round 2A trace-authority slice): `AsyncAgentSession` now receives
  one explicit `ExecutionTraceRecorder`, execution ID, and ownership flag. A
  direct session creates and owns that recorder; a submitted agent execution
  injects the outer handle's recorder and event sink, so it does not create a
  second execution or terminal event. Direct async/sync and submitted happy
  paths prove snapshot, trace, workspace, event, and outer execution IDs agree;
  completed direct-session results are finalized after cleanup and the
  `execution.finished` commit. Existing recorder tests plus the focused trace
  set prove idempotent finalization and post-terminal append rejection. The
  existing workspace integration regression now also asserts declared artifact
  execution IDs. After review fixes for failure-path evidence collection,
  startup/close races, and never-entered sessions, the expanded lifecycle gate
  passed 96 tests with strict mypy. The latest full run reached 792 passed,
  1 skipped, and the 3 documented xfails, with only the already-mapped R5
  process-group cancellation stress test failing intermittently on macOS.
  Failure-path provisional results, evaluation linkage, persistent reopen,
  redaction projections, and the full R2 gate remain open for later rounds.

  Evidence note (round 2B): deterministic direct-session and submitted-agent
  trace coverage now exercises adapter startup failure, independently reachable
  MCP/server startup failure, terminal turn failure, timeout, active
  cancellation, close cancellation race, and retryable cleanup failure
  (`sdk/tests/integration/test_agent_session_trace.py`). Every successful
  cleanup publishes exactly one `execution.finished` event whose outcome and
  completeness match the final snapshot/result; provisional in-context
  terminal-turn results remain trace-less until close. Submitted terminal
  failures retain the outer execution ID/event authority without duplicate
  terminal IDs, and a persistent submitted trace reopens from a second
  `SQLiteExecutionStore` with an identical stable event sequence. Cleanup
  retry finalizes once as partial with `cleanup_failed` retained as a
  limitation. The focused R2B set passed 13 tests and strict mypy passed for
  the touched runtime/session/test files. Independent review added submitted
  active-cancellation coverage and prevented the session's close outcome from
  overriding the outer cancellation. The expanded focused gate passed 109
  tests, strict mypy passed, and the complete SDK suite passed 803 tests with
  1 expected skip and exactly the 3 documented strict xfails. Interrupted
  lease-loss coverage, evaluation linkage, and cross-API/UI redaction remain
  open; no persistent reopen claim is made beyond the submitted trace case
  above.

  Evidence note (round 2C): SQLite-owned cancellation and lease-interruption
  terminalization now shares a stable partial payload containing outcome,
  completeness, and bounded limitations. Reopen tests cover queued and
  worker-observed cancellation plus stale, heartbeat, and explicit lease-loss
  interruption, proving one terminal event, snapshot/event outcome agreement,
  identical events from a second store, and matching `TraceResult` projection.
  Evaluation context construction now resolves one execution identity across
  explicit input, result snapshot, trace, subject, and every artifact, and
  rejects conflicts with a value-free typed error; `evaluation_id` remains
  independent. Together with R2A/R2B, the deterministic matrix now covers all
  listed outcomes. Independent focused acceptance passed 80 tests and strict
  mypy passed for the touched source plus evaluation tests. The universal
  persistent runtime-outcome matrix remains unchecked because worker command
  failure still delegates terminalization to its runner. Full-suite review
  additionally exposed cross-process monotonic clock reset: an attached
  recorder could make an otherwise durable trace fail validation. Recorders
  now seed from committed offsets and SQLite-owned terminal events inherit the
  latest offset. The separate-worker regression passed three independent
  repetitions, and the clean complete SDK suite passed 811 tests with 1
  expected skip and exactly the 3 documented strict xfails.

Gate: every terminal outcome has a non-null, reopenable trace whose final event
contains the same outcome as its execution snapshot.

### R3 — Repair secret handling before further live execution

#### Durable specifications and profiles

- [x] Introduce a specification/profile serializer distinct from evidence
  redaction.
- [x] Preserve `SecretReference(source, name)` descriptors exactly through
  model → SQLite → model round trips.
- [x] Reject literal values under credential-bearing durable fields where a
  reference is required; never replace them with a runnable `[REDACTED]`
  literal.
- [ ] Resolve references only in the worker that owns execution and keep the
  resolved value out of specs, events, errors, reprs, and database rows.
- [x] Test stdio environment, HTTP headers, bearer tokens, harness credentials,
  saved profiles, queued commands, and cloned executions in a second process.

The durable serializer and process-round-trip regression cover the SQLite
specification/profile/command boundary and scan SQLite/blob bytes for known
canaries. The child process validates preserved descriptors but intentionally
does not resolve a credential. Runtime ownership of reference resolution and
the absence of resolved values from live worker evidence remain open for the
later capture-time canary and ephemeral-handoff work.

Evidence note (round 3A): executable specifications, profile revisions,
submission-time bindings, cloned executions, and queued command payloads now
use a dedicated fail-closed durable serializer. It preserves typed environment
and provider secret references, permits only the two documented exact legacy
placeholder forms, rejects credential literals and credential-bearing URLs,
redacts configured canaries in ordinary fields, and bounds/cycle-checks hostile
containers. A second process reopens the SQLite store and validates references
across stdio, HTTP, harness, command, revision, and clone surfaces while raw
database/blob scans prove the configured canary was not persisted. Independent
acceptance passed 62 focused tests and strict mypy; the complete SDK suite
passed 826 tests with 1 expected skip and exactly the 3 documented strict
xfails. Runtime worker-only resolution and cross-surface capture canaries remain
open and are not claimed by this round.

#### Capture-time canaries

- [ ] Centralize the sensitive-key predicate and use it for configuration,
  stdio environment, HTTP headers, proxies, logs, and artifacts.
- [ ] Recognize normalized names including `X-API-Key`, `*_API_KEY`, vendor API
  keys, bearer/auth headers, cookies, tokens, passwords, and credentials.
- [ ] Register all resolved reference values and classified literal values as
  process-local redaction canaries before any server or harness starts.
- [x] Ensure HTTP proxy writers receive the same process-local canary set.
- [x] For stdio proxy subprocesses, resolve/read secrets inside the proxy from a
  mode-0600 ephemeral handoff, register them in that proxy's writer, and delete
  the handoff before terminal completion. Never place values in argv.
- [x] Route the legacy Claude, OpenCode, and ACP `stdio_proxy` wrappers through
  the same strict handoff, preserving configured transport and cwd while
  excluding credentials from harness config and result evidence.
- [ ] Redact secrets echoed in assistant text, MCP results, JSON-RPC errors,
  stderr, raw capture, artifact bytes, API JSON, SSE, and UI previews.
- [ ] Test secrets split across structured fields and confirm documented limits
  for transformed/encoded values.

This round proves canary classification and propagation for the
`McpCaptureManager`-owned HTTP and stdio proxy paths only. Direct transport
classification is wired to the shared predicate, but legacy harness-specific
proxy wrappers and broad log/artifact/API/SSE/UI propagation remain open.

Evidence note (round 3B): manager-owned HTTP and stdio capture paths now
classify literal API-key/header/environment names with the shared predicate,
carry resolved reference values only in process-local canary sets, and give
each actual proxy writer the ambient-plus-explicit union. The stdio child reads
one strict mode-0600 structured handoff without following symlinks, removes it
before launching the MCP server, and rejects legacy/malformed/non-private
handoffs. Real HTTP forwarding and a real stdio proxy subprocess echo literal,
resolved, and ambient canaries in result/error payloads while raw capture/event
scans remain clean. Independent acceptance passed 87 focused tests and strict
mypy; the complete SDK suite passed 833 tests with 1 expected skip and exactly
the 3 documented strict xfails. Legacy harness-owned wrappers and end-to-end
API/SSE/UI/artifact projections remain explicitly open.

Gate: repository/database/blob/capture scans find none of the test canaries;
cross-process authenticated executions still receive the original credentials.

Evidence note (round 3C): Claude, OpenCode, and ACP fake-harness regressions
verify strict one-shot stdio handoffs, safe baseline environment inheritance,
credential-free harness configuration/ACP payloads, cleanup after completion,
and redaction of canaries in assistant output, normalized events, stderr, and
HTTP capture. The non-sensitive-header HTTP regression verifies that exact
environment placeholders update the caller canary set before forwarding.
The focused legacy harness/transport set passed 73 tests. The typed transport
helpers pass strict mypy; checked-body mypy also passes for the three legacy
adapter modules with their pre-existing untyped-definition/call categories
allowed. Broad assistant/stderr/artifact/API/SSE/UI propagation remains
intentionally open. The complete SDK suite passed 839 tests with 1 expected
skip and exactly the 3 documented strict xfails.
`AcpHarnessAdapter._servers()` consumes ServerGroupManager-instrumented
configurations and is not an additional `stdio_proxy` wrapper launch.

Evidence note (round 3D): the RunManager now applies explicit shared
`RedactionConfig` projections at event, trace, result, stderr, and error
persistence boundaries; run/event/report API responses apply the shared API
projection (including POST /runs, GET /runs, GET /runs/{id}, clone, and
cancel); and UI trace previews expose a pure shared UI projection helper.
Deterministic tests cover exact literal/reference substrings, complete
canaries in separate structured fields, documented non-support for transformed
values, ephemeral artifact bytes, SQLite event payloads including a large
payload blob reopened through a second store, and actual ASGI API run/event/
report responses. The focused legacy/transport/trace/API/UI set passed 156
tests with zero failures; strict mypy passes for the typed redaction and UI
view-model modules, and `git diff --check` passes. The broader
`--check-untyped-defs` audit still reports pre-existing/current errors in the
untyped API, RunManager, and Streamlit application modules. No API SSE endpoint
exists to exercise, and the R4 persistent artifact ownership xfail plus the
full R3 cross-process authenticated-worker gate remain open.

The independent complete SDK gate after round 3D passed 843 tests, with 1
expected skip, exactly the 3 documented strict xfails, and 38 pinned-MCP
deprecation warnings.

Evidence note (round 3E): the ACP contract session now resolves and registers
server reference values and conventionally sensitive literal values before
creating the ACP subprocess. Credential-bearing environment/header fields are
omitted from the ACP ``session/new`` descriptor so registration does not leak
values into ACP configuration. Deterministic startup-race and unresolved-
reference regressions pass, and a public persistent SDK execution proves a
real ACP subprocess reaches a credential-requiring MCP subprocess through the
SDK stdio handoff. A second ``SQLiteExecutionStore`` reopen and raw SQLite
database/WAL/SHM, blob, and capture scans remain canary-free; strict mypy for
the touched ACP source/test modules and ``git diff --check`` pass. This is an
ACP ordering fix only; the broader capture-time canary checklist remains open.

### R4 — Fix workspace ownership and persistent artifacts

- [x] Inject `store.artifacts` into every `WorkspaceManager`; never silently
  create an in-memory artifact store for a persistent execution.
- [x] Use the outer execution ID for nested agent-session workspace capture.
- [x] Pass `HarnessLaunch.workspace_root` to every real adapter.
- [x] Keep HOME/XDG/config isolation in a separate adapter-control directory,
  while launching the harness with the SDK workspace as its working directory.
- [x] Pass the same workspace to ACP `session/new`, OpenCode's server/session
  context, Claude's continuous process, and all stdio MCP children unless a
  server has an explicit safe cwd.
- [x] Capture workspace changes and declared artifacts before process teardown
  removes their files.
- [x] Persist artifact metadata and bytes before publishing terminal results.
- [x] Reopen artifact bytes using a separate process and store instance.
- [ ] Test COPY, TEMPORARY, GIT_WORKTREE, READ_ONLY, and acknowledged IN_PLACE
  policies against real harness subprocesses.
- [ ] Test exclusions for `.git`, environments, caches, secret files, and the
  artifact store itself.
- [x] Test successful and failed artifact policies (`always`, `failed`, `never`).

Gate: remove the strict artifact xfail after a separate worker writes an
artifact, exits, and a producer process reopens and verifies its bytes/hash.

Evidence (round 4A): persistent submitted direct executions now inject the
SQLite store's artifact backend into their workspace owner, and persistent
submitted agent executions pass that same backend through the nested session
while retaining the outer execution ID. Terminal-result hydration reads the
durable refs from the artifact store and surfaces storage failures instead of
silently returning an empty artifact list. Workspace capture stores redacted
bytes and metadata before its workspace-changed and terminal events are
committed. The separate-worker artifact E2E now passes and verifies reopened
bytes and SHA-256; a focused persistent-agent regression verifies backend/ID
identity and reopens bytes through a second SQLite store. Artifact policy unit
coverage exercises `always`, `failed`, and `never` for completed and failed
outcomes. The focused R4A set passed 24 tests, strict mypy passed on the
touched runtime/session/API/test files, and `git diff --check` passed.

Adapter workspace-root propagation, isolated adapter HOME/XDG directories,
pre-teardown native-process capture ordering, real native harness
workspace/policy matrices, and the remaining R4 exclusions and policy gates
remain open for R4B and later rounds.

The independent complete SDK gate after round 4A passed 849 tests, with 1
expected skip, exactly the 2 remaining documented strict xfails, and 38
pinned-MCP deprecation warnings.

Evidence (round 4B): the Claude and OpenCode native process owners now keep
their isolated HOME/XDG/config state under an adapter-control directory while
using the resolved SDK workspace as process cwd. OpenCode also supplies the
official `x-opencode-directory` request header on session create, message, and
delete requests. ACP likewise separates its control directory from both its
process cwd and `session/new` cwd. Its already-instrumented stdio proxy remains
single-layered: a server without `cwd` inherits the ACP workspace, while an
explicit validated `cwd` remains encoded in the proxy arguments. Agent
sessions capture workspace changes and declared artifacts before adapter and
server teardown, including startup-failure cleanup. Deterministic executable
fixtures assert cwd, session context, header context, HOME/config separation,
single proxy instrumentation, explicit-cwd preservation, and pre-close
artifact capture. The independently reviewed focused gate passed 72 tests;
strict mypy passed for the five typed runtime modules and the native-adapter
test module, and `git diff --check` passed. Real live-provider workspace tests
remain opt-in work for R5; the deterministic five-policy native subprocess
matrix and full exclusion matrix below remain open.

The independent complete SDK gate after round 4B passed 853 tests, with 1
expected skip, exactly the 2 remaining documented strict xfails, and 38
pinned-MCP deprecation warnings.

### R5 — Repair real harness adapters

#### Common native-process behavior

- [x] Change bounded stderr handling to retain at most the configured diagnostic
  amount while continuing to drain until EOF.
- [x] Preserve a small sanitized tail/reference for diagnostics without placing
  provider output or secrets in exceptions.
- [x] Make process-group termination tolerate `ESRCH`, `EPERM`, already-reaped
  children, and PID/group races without retrying an unsafe target.
- [x] Assert the target PID/group belongs to the owned child before signaling.
- [ ] Run 50 repeated cancellation/timeout iterations per native fixture and
  fail on flakiness or leaked children.

Evidence (R5A common native-process slice): shared native and ACP stderr readers
retain at most 64 KiB (or the configured limit) while continuing to read until
EOF; legacy Claude/OpenCode runners use the same bounded reader. Deterministic
large-stderr-before-readiness fixtures pass for both legacy native runners;
the ACP runtime uses the same drain-to-EOF primitive. The fixtures retain
bounded redacted diagnostics and complete without a pipe deadlock
(`sdk/tests/integration/test_native_stderr.py`). Native cleanup
now records the group leader identity, validates `getpgid(child) == pgid`,
rejects pgid-only/mismatched/self-group targets, tolerates permission and
already-reaped races, and passes the child PID when ACP cleanup has a captured
pgid (`sdk/src/mcp_pal/harness/process_group.py`,
`sdk/tests/unit/test_process_group.py`). Focused existing native/ACP cancellation
and descendant cleanup tests plus the new ownership tests passed; strict mypy
passed for the typed touched modules and new tests, and `git diff --check`
passed. The complete 50-iteration-per-fixture stress matrix remains open.

The independently reviewed complete SDK gate after R5A passed 866 tests, with
1 expected skip, exactly the 2 remaining documented strict xfails, and 38 pinned-MCP
deprecation warnings. The focused ACP/native ownership gate passed 58 tests;
the dedicated legacy Claude/OpenCode stress matrix also passed 50 consecutive
cancellation and 50 consecutive timeout iterations for each runner. Cleanup
waits remain bounded even when group ownership verification fails closed.

Evidence (R5B portable policy/ACP slice): SDK-owned stdio and HTTP capture
proxies now evaluate portable tool policy before forwarding `tools/call`. The
one-shot stdio policy handoff is created with `O_EXCL` mode `0600` and read
without following symlinks. Qualified identities, duplicate unqualified names,
unknown tools, JSON-RPC batches, notifications, full-policy acknowledgement,
native-policy separation, denied-call JSON-RPC responses, and argument-free
denial captures are covered by `sdk/tests/unit/test_proxy_tool_policy.py`.
ACP policy preflight reports portable enforcement for the real adapter, and
the real two-turn ACP/MCP trace test is green without an xfail. Adapter-only
tool reports remain advisory; stable proxy captures are the enforcement
evidence. Focused R5B policy/ACP/manifest tests passed (29 tests), strict
mypy passed for all seven touched typed runtime modules, and `git diff --check` passed.
The independently reviewed complete SDK gate then passed 883 tests, with 1
expected skip, 1 documented strict xfail, and 38 warnings in 285.69 seconds.
The remaining terminal policy-evidence todo remains open.

#### Portable policy enforcement

- [x] Enforce portable MCP tool policy in SDK-owned MCP proxies before forwarding
  `tools/call`, independent of whether the harness has a native allowlist.
- [x] Normalize every observed call to `(server alias, tool name)` at the proxy.
- [x] Correlate adapter-reported calls with stable proxy captures; do not
  reject a valid single-server call merely because an ACP update gave only the
  tool name.
- [x] Reject ambiguous unqualified calls when multiple servers expose the same
  tool name.
- [ ] Record requested, enforced, observed, denied, and unavailable policy
  evidence separately.
- [x] Advertise `supports_tool_policy=True` only after the common policy contract
  passes for that adapter and transport.

#### ACP

- [x] Use the real workspace in process cwd and ACP session creation.
- [x] Preserve one ACP process, connection, session ID, and MCP process set
  across at least three turns.
- [x] Normalize ACP tool-call updates without trusting them as enforcement.
- [x] Test tool error recovery and attachments rejection on a continuing real
  ACP/MCP session.
- [x] Test ACP connection loss as a typed failed turn with a partial terminal
  trace and owned-child cleanup.
- [ ] Test cancellation, timeout, duplicate tool names, and complete traces.

Evidence (R5D ACP lifecycle slice): the real-MCP ACP E2E sends three distinct
turns and proves one ACP process, one `initialize`, one `session/new`, three
`session/prompt` requests, one MCP initialization lifecycle, and three
`tools/call` request/response exchanges. It also proves a stable session ID,
distinct outputs, a completed/full trace, and a terminal `execution.finished`
event. The stale workspace-cwd/session-creation item is covered by the
existing real-workspace ACP contract evidence. Root review of the broader
reference-bridge/E2E/ACP contract set passed 28 tests with one expected xfail;
strict mypy passed for the two touched typed test files. Round 2 ACP recovery
evidence adds `sdk/tests/e2e/test_sdk_workflows.py` coverage using a real ACP
subprocess and real stdio MCP subprocess: an `isError: true` tool result is
followed by a successful call on the same process/connection/session,
unsupported opaque content raises the typed public `UnsupportedFeature`
exception without poisoning the session, and ACP process loss returns a typed
failed turn, finalizes a `partial_trace`, and reaps the MCP child process
group. The wire trace retains the failed and successful MCP results while the
execution terminal outcome remains completed for recoverable tool errors.
The adapter now carries an explicit immutable `trace_limitations` signal from
the ACP receive-loop process-loss observation; terminal adapter failures with
complete evidence remain complete. The adapter close path tolerates only the
known official connection receive-loop process-loss error after the ACP child
is already reaped; unrelated live-child close errors still remain cleanup
failures. Trace finalization treats an explicit safe limitation such as
`partial_trace` as partial even when cleanup itself succeeds. Recovery
coverage for cancellation, timeout, duplicate tool names, and related cases
remains open and is not marked complete.

#### OpenCode

- [x] Implement an explicit OpenCode configuration dialect abstraction.
- [ ] Detect supported dialect/capabilities during readiness rather than writing
  Claude `mcpServers` syntax.
- [x] Render legacy OpenCode `mcp` and current V2 `mcp.servers` forms exactly as
  their official schemas require; unsupported dialects fail readiness.
- [x] Send the selected provider/model in every session message using the
  official server API model reference.
- [x] Parse official `{info, parts}` responses, including text, tool parts,
  errors, finish state, tokens, and cost when actually reported.
- [x] Keep authentication explicit. Add typed credential references to the
  OpenCode harness specification; do not copy ambient global auth unless the
  user selects a documented opt-in saved-auth mode.
- [x] Deliberately avoid bulk provider/model catalog downloads during startup;
  the first normal `/session/{id}/message` turn is authoritative for selected
  provider/model availability and retains bounded, sanitized endpoint failures.
- [x] Start `opencode serve` in the SDK workspace while keeping its HOME/XDG and
  config/data directories isolated.
- [ ] Replace fixture-only response shapes with fixtures captured from the
  documented contract for each supported dialect.

R5C evidence: OpenCode deterministic coverage passes with stable and V2 startup
checks asserting zero `/provider`, `/api/provider`, or `/api/model` requests.
The adapter detects supported stable OpenCode 1.x dialects, uses dedicated
legacy/V2 renderers, sends official model references on each
`/session/{id}/message` request, parses bounded `{info, parts}` responses, and
resolves typed environment credential references in the isolated child. No
bulk catalog traffic is part of startup; the installed OpenCode 1.18.15
catalog (about 4.7 MiB) is intentionally not downloaded. The opt-in live
inference gate remains separate.

#### Claude Code

- [ ] Apply the same workspace, stderr, process ownership, portable policy,
  secret, trace, and multi-turn contracts.
- [ ] Retain only the continuous stream-JSON mechanism; missing streaming remains
  a readiness failure with no resume/one-shot fallback.

Gate:

- Deterministic common harness contract passes for ACP, Claude, and OpenCode.
- [x] Real ACP multi-turn E2E loses its xfail.
- Opt-in live OpenCode calls the test MCP across two turns using the requested
  model, preserves the session, records tool traffic, and finalizes a trace.
- Live gates remain separate from required deterministic CI.

### R6 — Separate and rebuild the application on SDK services

#### Application configuration

- [ ] Create `mcp_pal_app.settings` with one explicit application `.env` loading
  entry point.
- [x] Define one database setting name and use it consistently in API, UI,
  workers, reset tooling, `.env.example`, README, tests, and Just recipes.
- [x] Keep SDK library imports free from automatic `.env` loading.
- [ ] Test explicit file, ambient override, missing file, invalid file, and
  redacted error behavior.

#### Workspace-separation evidence (mechanical R6 slice)

- [x] Root `pyproject.toml` declares the `sdk` and private `app` uv workspace
  projects; the lock contains both projects.
- [x] UI, API, application entrypoint, application settings, legacy ORM
  persistence, built-in profiles, run orchestration, and one-shot native
  runners live under `app/src/mcp_pal_app`.
- [x] Legacy application event normalization and its regression tests live in
  the app; modern SDK adapters, ACP contracts, stable trace/storage, and
  proxy modules remain in the SDK.
- [x] SDK has no compatibility forwarding modules for the removed application
  paths, and the packaging gate checks both wheel contents and clean-install
  imports.
- [x] App owns FastAPI, Streamlit, requests, pydantic-settings, SQLAlchemy,
  and its private application project dependency; the SDK retains only its
  published/runtime dependencies and the storage extra.

#### API v2

- [x] Implement the round-1 `/api/v2` execution adapter exclusively as typed
  serialization over SDK models, the SDK execution kit, stores, and worker.
- [ ] Remove `/api/v1` and its table-purge/compatibility code.
- [x] Support execution create/list/get/cancel/delete and bounded terminal
  reports through the public SDK execution/store contract.
- [x] Return stable typed error envelopes and accept only typed JSON execution
  specifications; in-process factories are rejected by the SDK spec model.
- [ ] Ensure API cancellation reaches work owned by another process.
- [ ] Generate OpenAPI and round-trip it against shared Pydantic models.

Round-1 evidence: the app adapter submits through `MCPTestKit` with the same
SQLite execution store and embedded SDK worker used by direct SDK callers. It
returns 202 for asynchronous submission, typed v2 status/error envelopes,
bounded report events with cursor/truncation metadata, and uses SDK query/report
methods for SQL-backed listing, cancellation, deletion, and reopen behavior.
The SDK query contract is implemented consistently by in-memory and SQLite
stores. SSE, profiles, interactive sessions, durable cross-process cancellation,
v1 removal, and OpenAPI round-trip remain later R6 work. Accepted verification
results are 40 focused SDK tests, 5 focused app-v2 tests, 762 full SDK tests
passed with 1 skipped, 1 expected xfail, and 38 warnings, plus 178 full app
tests passed; strict mypy, compile, and diff checks are clean.

#### R6 progress evidence — typed profile and spec boundary

The direct-client migration now has an application-owned, transport-neutral
foundation in `mcp_pal_app.services.profile_service` and
`mcp_pal_app.services.spec_builder`. `ProfileService` manages
server and harness profile lifecycle through the SDK's fresh `v2_*` profile
tables, including deterministic listing, immutable revisions, archive/restore,
metadata updates, harness import/export, manifest validation, and explicit
trusted-unsandboxed acknowledgements. `ExecutionSpecBuilder` resolves an
immutable application selection into concrete public SDK server values for
stdio, Streamable HTTP, and SSE, and concrete Claude Code, OpenCode, or ACP
harness values. Environment placeholders remain `SecretReference` values;
runtime profile references are not passed to execution. Its typed one-turn
draft preserves prompt, goal, timeout, selected server, tool mode, metadata,
and first-class typed ACP mode/configuration selections for the later UI
migration. The ACP contract applies and validates those selections after
`session/new` and before the first prompt; they are not metadata-only
provenance.

Evidence: focused profile/spec tests cover the three transports, secret
references, all three harness kinds, policy mapping, lifecycle/import/export,
archive rejection, invalid selections, and ACP trust/options; focused SDK
SQLite tests cover kind validation, archived filtering, deterministic revision
ordering, metadata conflicts, and durable descriptor safety. These tests do
not claim that Streamlit consumes the service yet; the Streamlit and `/api/v1`
checkboxes remain open until UI parity and route-removal gates pass.

#### R6 progress evidence — app-owned runtime seam

`AppRuntimeService` now composes one explicit `Settings`, SQLite v2 execution
store, `MCPTestKit`, `AppExecutionService`, `ProfileService`, and
`ExecutionSpecBuilder`, with injection-friendly ownership and idempotent close.
It exposes typed one-turn submit/history/report/cancel/delete/terminal-history
operations, profile operations, bounded `ExecutionView` projections, pinned
clone-draft reconstruction, and idempotent fresh-v2 Excalidraw seeding. SQLite
and in-memory stores both retain validated defensive execution-spec copies.
Sync toolkit adapter registries preserve deliberately empty injected registries.
Claude/OpenCode/ACP adapters prefer explicit Settings credential values and
register resolved secrets before child output; persisted specs/events/settings
representations retain only references or redacted values.

Accepted evidence for this seam: the focused native/runtime SDK gate passes 154
tests and the focused app runtime/credential gate passes 12 tests; the complete
SDK gate passes 796 tests with 1 skipped and 38 warnings, and the complete app
gate passes 202 tests. Strict mypy on the touched SDK and app modules is clean,
and `git diff --check` is clean. The evidence includes clear-history active-run
preflight (mixed active/terminal history is not partially deleted), typed
defensive spec parity for in-memory and SQLite stores, successful and failed
terminal trace reopen, and sync/async parity for the configured store property.
Streamlit, `/api/v1`, and remaining application configuration/reset work remain
open; this evidence does not mark those milestones complete.

#### R6 progress evidence — typed readiness boundary

`ReadinessService` now provides an application-owned, transport-neutral typed
snapshot over SDK profile storage and shared bounded adapter probe behavior. It reports
storage health, Claude/OpenCode executable capabilities and provider/saved-auth
readiness, and each current ACP profile's local executable, environment, trust,
archive, and profile/revision identity. Settings credentials are overlaid only
for the selected local check; values never appear in descriptors, reprs, probe
environments, or safe errors. Native readiness uses bounded no-shell probes and
current adapter capabilities (`stream-json`, OpenCode `serve`, and supported
1.x/2.x dialect versions), while ACP verification reads only the typed fresh-v2
probe history and does not read legacy probe tables. A ready trusted
local ACP profile remains selectable when both native built-ins are unavailable;
archived profiles are excluded from normal capability listings and are only
inspectable through an explicit profile lookup.

`AppRuntimeService` exposes this facade through a lifecycle-guarded property and
supports an injected typed readiness provider for deterministic clients/tests.
Focused readiness/runtime tests pass (18 tests), strict mypy on touched
production and test modules passes, and `git diff --check` passes. This earlier
readiness slice did not itself claim probe-history migration; the subsequent
durable ACP probe slice below completes fresh-v2 probe history. Streamlit SDK
consumption, `/api/v1` removal, and development reset completion remain open.

#### R6 progress evidence — durable ACP probe service

The typed ACP probe contract now persists protocol and full observations through
the SDK's in-memory and SQLite stores. Dimensions include profile/current
revision, probe kind, transport, mode, and stable session configuration;
protocol mode/config values normalize to a neutral dimension. The application
service validates the live harness profile/revision, requires a verified
protocol probe before full probes, invokes the SDK's real `protocol_probe` and
`full_probe` functions against the current manifest, and owns bounded timeout,
cancellation, identity, capability/config, safe diagnostics, and lifecycle
state. Runner output is explicitly projected into evidence before redaction;
SQLite survives reopen and readiness consumes only the current revision's exact
probe history. A direct `AppRuntimeService` black-box E2E launches a real
executable ACP fixture and the packaged MCP echo server, verifies protocol/full
evidence and readiness, and confirms SQLite close/reopen durability. Focused
ACP persistence/service/readiness tests pass (9 SDK ACP, 31 app ACP/readiness),
with strict mypy and `git diff --check` clean. This evidence does not claim
Streamlit SDK consumption, `/api/v1` removal, or unrelated R6 work.

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

- [x] Keep reset as a development-only Just recipe, not an installed CLI.
- [x] Use a Just invocation that actually works, preferably
  `just dev-db-reset` with an interactive/repository-local safety guard or the
  conventional `just CONFIRM=reset dev-db-reset` ordering if confirmation is
  retained.
- [ ] Preflight the database and exact `-wal`/`-shm` sidecars completely before
  deleting any file.
- [x] Refuse symlinks, directories, paths outside the repository, broad paths,
  and non-SQLite suffixes.
- [ ] Test fresh startup, no-file reset, all-sidecar reset, every refusal path,
  and failure atomicity.

The remaining R6 work is intentionally deferred: the rest of `/api/v2`, removal
of the legacy `/api/v1` surface, direct SDK-backed UI operations, and
documentation. The mixed `harness/base.py`,
legacy ACP runner internals, and harness-specific trace builders remain in the
SDK while v1 behavior is still active; their extraction is an API-v2 cleanup
candidate, not a compatibility-forwarding move. The settings class and its
explicit-file helper are present, but wiring that helper into the application
entrypoint and documenting the selected-file startup path remains open.

Gate: the app project passes independently; the SDK wheel contains no app code;
the UI submits through SDK services and reopens/displays traces for every
outcome; `/api/v1` is absent.

### R7 — Complete pytest integration and thin CLI

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

### R8 — Make persistence, GC, leases, and cancellation process-safe

This milestone intentionally follows the application split and pytest/CLI
surface. The public execution and storage contracts established there are the
boundaries against which cross-process ownership is hardened.

#### Durable cancellation

- [ ] Introduce a cancellation token shared through execution runtime, agent
  session, direct transport, harness adapter, workspace capture, and cleanup.
- [x] Have the persistent owner watch the SQLite cancellation flag while work is
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

Evidence (round 8A durable-owner slice): a persistent execution owner now runs
a bounded SQLite cancellation watcher beside its execution task. A cancellation
requested from a separate producer process cancels the owner task, allowing the
normal direct-transport cleanup path to terminate the owned stdio process and
publish a partial `cancelled` trace. The worker then completes the claimed
command and releases its lease; repeated cancellation is idempotent, and a late
request cannot overwrite a natural terminal result. Focused runtime lifecycle
regressions cover watcher interruption, watcher cleanup, and completion-race
behavior. The separate-process direct-stdio E2E repeats the cancellation ten
times and, through a second SQLite store, verifies one terminal event, partial
trace reopening, command completion, and no reclaimable ownership. A bounded
separate-worker ACP/MCP E2E additionally proves cancellation of a real ACP
owner and its real MCP child across two iterations, with the same terminal
trace and command assertions. Broader cancellation-token propagation across
every adapter phase, escalation after a grace period, and the remaining
blob/store contract gates remain open.

Gate: remove the strict cancellation xfail; pass forced blob interleavings and
the complete cross-process store contract without leaked workers or children.

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
3. Direct operation model and real `DirectSpec` execution.
4. Shared trace finalization for agent sessions/submitted executions.
5. Secret-reference persistence and transport canaries.
6. Persistent artifact-store injection and execution-ID ownership.
7. Native process/workspace/stderr ownership.
8. Portable proxy-level policy enforcement and ACP repair.
9. OpenCode contract/dialect/model/auth/response repair.
10. Claude contract completion and native stress tests.
11. SDK/application workspace split.
12. API v2 adapter and removal of v1.
13. Direct-SDK Streamlit migration and reset/config repair.
14. Pytest fixtures and thin CLI.
15. Durable cancellation and lease-loss propagation.
16. Blob publication/GC coordination and shared store contract.
17. Documentation and executable example corpus.
18. CI/platform/package/release gates.

Do not combine security/persistence fixes with the repository move. Complete
and verify the mechanical application split and pytest/CLI surface first, then
harden their now-stable cross-process storage boundaries in the separate R8
slices.

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
| ACP rejects real MCP tool identity/policy | R5 | real ACP + real MCP multi-turn E2E |
| Live OpenCode startup/config failure | R5 | opt-in live two-turn E2E |
| OpenCode ignores model/provider | R5 | server request assertion + live trace |
| OpenCode fixture-only response parsing | R5 | official response contract fixtures |
| Persistent artifact refs lose bytes | R4 | separate-process reopen/read |
| Durable cancel does not interrupt owner | R8 | separate-process child termination |
| SecretReference becomes `[REDACTED]` | R3 | SQLite/process round trip |
| API-key literals miss canary registration | R3 | echoed HTTP/stdio secret scans |
| Proxy writer loses resolved canaries | R3 | proxy capture scan |
| DirectSpec performs no operation | R1 | real stdio operation E2E |
| AgentSession result has no trace | R2 | direct session outcome matrix |
| Native harness ignores workspace | R4/R5 | real-process workspace tests |
| Native stderr drain can deadlock | R5 | output above pipe/retention limits |
| Blob GC races publication | R8 | forced two-store interleaving |
| AnyIO EndOfStream replaces server error | R1 | original-exception regression test |
| Process-group cancellation is flaky | R5 | repeated stress gate |
| Application `.env` setup is broken | R6 | app-entry configuration tests |
| Reset variable/command/atomicity mismatch | R6 | command and refusal matrix |
| UI/API ship in SDK wheel | R6 | wheel-content rejection test |
| UI still calls legacy HTTP API | R6 | injected SDK UI tests; no requests import |
| `/api/v1` remains | R6 | route/source absence tests |
| SDK docs are placeholders | R9 | strict docs/snippet gate |
| Copyable pytest examples are absent | R9 | installed-wheel example corpus |
| Pytest plugin is empty | R7 | fixture/isolation tests |
| `mcp-pal test` is absent | R7 | pytest equivalence tests |
| Full suite/status claims are inaccurate | R0/R10 | truthful recorded green gates |

## 8. Definition of done

This remediation is complete only when every R0–R10 checkbox and gate passes,
the original plan links to the evidence rather than repeating unverified pass
counts, and no confirmed finding in section 7 remains hidden behind an xfail,
skip, fixture-only adapter, or deferred cleanup note.
