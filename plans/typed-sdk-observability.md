# Typed SDK observability

This is the decision-complete implementation plan for the finalized immutable
`TraceView` over canonical events. Canonical events remain the persisted audit
layer; the typed view is the public SDK/UI contract. Provider-specific fields
use explicit availability states and are never guessed.

## Runtime information confirmed

Direct MCP, OpenCode, Claude Code, and ACP runtimes can observe MCP traffic,
messages, tool activity, process lifecycle, and (where emitted) provider
metadata. OpenCode can emit provider/model, usage and cost; Claude Code can
emit thinking/signatures, stop/usage/cache/cost metadata; ACP can emit modes,
configuration, plans and state updates. Provider-hidden reasoning remains
unavailable; ACP usage/cost that the protocol does not expose is explicitly
unsupported (`UNSUPPORTED`/`HARNESS_UNSUPPORTED`). If an applicable usage
field is exposed but capture fails, it is `UNAVAILABLE` instead.

## Work items

### R1 — Public types and exports

- [x] Add frozen Pydantic observability models, enums, generic `Observation`,
  discriminated `TraceEntry` and runtime-info unions.
- [x] Add validation, JSON schema support, typed indexes, and trace filters.
- [x] Add public observability exports and model round-trip tests.

### R2 — Capture configuration and raw-evidence storage

- [x] Add bounded redacted raw-evidence configuration and durable in-memory/
  SQLite storage with digest verification and garbage collection.
- [x] Add `TraceCaptureConfig` with enabled-capture defaults and positive,
  preview/frame/execution caps; redact before applying caps.
- [x] Add event-associated `put_raw_evidence`, reference-based reads, typed
  integrity/unavailable errors, shared-blob refcounts, and execution cleanup.
- [x] Add fresh-schema payload/raw-evidence coexistence and in-memory/SQLite
  parity tests; R3 projector metadata remains intentionally unimplemented.

### R3 — Direct MCP projector

- [x] Project canonical direct-MCP events into finalized `TraceView`, including
  protocol pairing, timing, initialization and raw frames.
- [x] Enforce finalized trace identity and terminal truthfulness: one canonical
  `execution.created` at sequence zero, one last `execution.finished`, strict
  outcome/completeness/limitations agreement, and no compatibility fallback or
  synthesized terminal limitations.
- [x] Correlate only temporally valid, family-compatible, typed-ID exchanges;
  retain ambiguous/reversed/incompatible/unmatched evidence and map tool and
  protocol failures to their typed statuses without collapsing errors into
  success.
- [x] Preserve malformed source observations explicitly, including unknown
  message roles, malformed content/reasoning/provider/process fields, and
  official MCP initialization values; retain typed raw-evidence references.
- [x] Derive summary counts, activity health, cleanup status, timing, runtime,
  provenance, and lifecycle fields from committed evidence rather than the
  overall execution outcome.
- [x] Expose immutable `TraceResult.view`, `ExecutionResult.trace_view`, store
  retrieval, raw-evidence reads, and sync/async kit parity with memory/SQLite
  reopen and no-store/closed/unknown behavior.
- [x] Keep lookup semantics explicit: store `get_trace`/`get_trace_view` return
  `None` only for an unknown execution, while sync/async kit lookups return a
  typed value and raise `ExecutionNotFound` for unknown IDs (and preserve
  `TraceNotFinalized`/`TraceUnavailable` for known but unusable traces).
- [x] Verify direct in-process, stdio, streamable HTTP, and SSE transports,
  exhaustive canonical event/status/identity/terminal cases, persistence
  parity, mypy, focused Ruff, diff checks, and the complete SDK unit suite.

### R4 — Matcher migration

- [x] Make matchers normalize supported trace subjects through finalized typed
  `TraceView` instances and fail clearly for non-finalized/unavailable traces.
- [x] Make tool-call, discovery, status, evidence-source, result, timing, turn,
  count, predicate, and ambiguity assertions consume typed observations and
  typed initialization entries without parsing or rewriting canonical events.
- [x] Preserve source-specific `wire`/`reported` semantics and select a
  correlated call only once for `any`; keep unavailable, redacted, truncated,
  and observed-null states distinct in matching and diagnostics.
- [x] Migrate duration, trace, event, outcome, artifact, workspace, and
  capability helpers to typed views where available while preserving legacy
  non-trace value subjects.
- [x] Add finalized-view/result/session-subject, malformed-boundary, evidence,
  ambiguity, result projection, null-state, public-event mapping, and matcher
  adversarial coverage; verify sync/async parity, type checks, Ruff, diff
  checks, and the full SDK suite.
- [x] Keep R5 harness normalization out of the projector; reported evidence in
  R4 is consumed only when already present as typed `ReportedToolCall` values.
  Expose explicit `arguments_observed_null`/`result_observed_null` criteria
  flags, `result_partial` for opt-in partial result matching, and reject
  canonical `EventKind` assertions whose exact source kind is not represented
  by `TraceView`.

### R5 — Internal harness observation contract

- [x] Add typed adapter observations, non-throwing sinks, process lifecycle,
  bounded stderr, and failure-safe capture.
- [x] Define the closed, provider-neutral `HarnessObservation` discriminator,
  immutable `TurnEvidence`/`HarnessSessionEvidence` values, and export the
  shared contracts from the public `mcp_pal.harness` namespace.  They remain
  out of the lightweight top-level namespace so importing value models cannot
  import concrete adapter modules.
- [x] Add the non-throwing recorder sink boundary with canonical provenance,
  ordering, bounded raw-evidence handoff, process/stderr switches, and safe
  diagnostic/limitation bookkeeping. The sink separates policy-disabled
  capture (`capture_disabled`) from capture and persistence failures, and
  recorder/store raw writes use a single commit operation so callbacks only
  receive complete references.
- [x] Complete adversarial R5 tests for every failure path, capture state,
  raw-evidence readback, and memory/SQLite parity before marking this round
  complete.

The current R5 implementation intentionally stops before provider adapter
migrations. Its completed contract coverage includes the eleven closed
observation variants, strict scalar/usage/process validation, immutable turn
and session evidence, bounded/redacted text and JSON projections, binary raw
input with explicit encoding, policy switches, process stderr states, and
failure-safe diagnostic bookkeeping. The adversarial matrix covers injected
append/blob failures and ref-count cleanup, reported-call ambiguity and wire
coexistence, interleaved chunk identities, terminal timeout/cancellation
retention, multibyte/redaction canaries, and full memory/SQLite parity/reopen
assertions. Provider adapter migrations remain separate R6–R8 work and are not
prerequisites for this provider-neutral round.

Raw evidence references remain attached to canonical raw-message entries and
are available through the recorder/store evidence APIs. R5 does not use a raw
reference as a tool-call identity: the typed `ToolCallEntry` source models do
not expose a per-source reference association, so doing so here would be an
unsafe guess. Reported/wire correlation therefore uses an explicit shared
provider call ID, followed only by the documented unique same-turn/server/tool
fallback; a future typed per-source reference field can extend this without
changing the R5 contract.

### R6 — OpenCode

- [x] Capture bounded/redacted HTTP evidence, response parts, tool reports,
  provider/model identity, finish state, usage/cache/cost metadata, typed
  source timing, and safe malformed/timeout outcomes through
  the R5 `HarnessObservation`/`TurnEvidence` boundary. Public agent sessions
  persist the typed evidence through the R5 sink and expose OpenCode runtime
  metadata in `TraceView` without duplicating legacy adapter events.
- [x] Add deterministic OpenCode response fixtures covering text, reasoning,
  tool calls/results/status, usage/cache/cost, malformed fields, bounds,
  redaction, HTTP errors, timeout/connection failure, and public TraceView
  persistence/reopen behavior.
- [x] Run the required live OpenCode E2E against a real MCP server using the
  configured repository `.env` credential. The strict two-turn test verifies
  one typed call per nonce, actual turn IDs, wire and reported observations,
  conservative correlation, and nonce-preserving arguments/results. Provider
  history is bounded by before/after cursors and never falls back to prior
  turns.

### R7 — Claude Code

- [x] Capture stream JSON, visible/encrypted thinking, tool blocks, usage,
  cache counters, stop/result metadata, cost, duration, stderr and exit state.

  Implemented in the typed Claude adapter with wrapped partial-stream parsing,
  replay-safe block/tool correlation, bounded redacted evidence, and public
  TraceView projection. Validation: 1,072 passed, 1 skipped; optional live
  coverage unavailable because `ANTHROPIC_API_KEY` is absent.

### R8 — ACP

- [x] Capture ACP frames, messages, thoughts, tools, plans, states, modes,
  config, interactions and typed unsupported usage/cost when the protocol does
  not expose them.

  Implemented typed ACP session observations with bounded/redacted JSON-RPC
  evidence, agent/session metadata, messages/thoughts/tools/plans/states and
  interaction receipts; finalized `TraceView` projects ACP runtime extensions
  and explicit `HARNESS_UNSUPPORTED` usage. Validation: ACP contract focus
  15 passed; focused ACP/schema/observation/projector coverage 174 passed;
  full SDK 1,076 passed, 1 skipped. Local ACP→MCP E2E and process-loss/
  recovery coverage passed. Optional external live ACP coverage was not run
  (no external ACP credential/prerequisite configured).

### R9 — Cross-harness parity and persistence outcomes

- [x] Verify common projections across deterministic harness fixtures and
  retain completed, failed, timed-out and cancelled traces after reopen.

  Added a shared finalized-view parity helper and deterministic persistence
  coverage across direct, OpenCode, Claude Code and ACP fixtures. The matrix
  checks common identity/timing/provenance/state semantics, native source
  fields, SQLite reopen equality, terminal outcomes, and sync/async direct
  parity. Validation: 17 focused R9 cases passed; affected native/ACP/
  observation/projector/trace suites passed 290 tests; full SDK passed 1,093
  tests with 1 skipped. Direct completed/failed/timed-out/cancelled and native
  OpenCode failed/timed-out, Claude timed-out, and ACP process-loss cells were
  exercised and reopened. Native adapter cancellation does not currently
  return a finalized turn envelope for this sink path; its public session
  boundary remains covered by the existing adapter cancellation tests. The
  existing optional live OpenCode/Claude/ACP credential-gated coverage was
  not exercised.

### R10 — Documentation and regression

- [x] Publish API/concepts/examples, add UI compatibility fixtures, run the
  complete sync/async, typecheck, packaging and live OpenCode regression suite.

Published the finalized typed `TraceView` as the documentation source of truth
and added copyable direct, matcher, filtering, raw-evidence, SQLite reopen and
async examples. Added a 20-case public UI compatibility fixture covering all
four runtime discriminators and five terminal outcomes, typed evidence,
availability states, interactions, process lifecycle, redacted raw data and
JSON round trips. Validation: examples `34 passed, 1 skipped`; focused
parity/observability/projector/storage/UI coverage `264 passed`; full SDK
`1113 passed, 1 skipped`; strict mypy on new files passed; Ruff F/format and
`git diff --check` passed; package build passed; required live OpenCode E2E
passed (`1 passed`).

## Public contract

`TraceView` is finalized-only and immutable. Its timeline contains typed
messages, reasoning, tool calls, protocol exchanges, initialization, usage,
interactions, process, workspace, artifact, evaluation, diagnostic, provider,
and redacted raw-message entries. Category properties are indexes over that
single timeline. Wire evidence is authoritative when correlated; reported
and wire evidence are retained and conflicts are explicit. Hidden reasoning is
never synthesized. The later UI PR renders this contract directly and does
not reintroduce harness-specific normalization.

## Delivery order

Implement each round independently with focused tests and review before the
next round. Keep canonical event models and existing UI unchanged until the
corresponding projector and later UI migration are complete. The SDK is not
yet production-stable, so no legacy trace adapter or database compatibility
branch is required.

## Expanded decision-complete specification

### Architecture and non-goals

Capture adapters append typed internal observations to canonical events. A
pure `TraceProjector` deterministically projects those events into one ordered
immutable timeline. `TraceView` indexes that timeline; it is not a second
mutable store. Redacted bounded raw provider/MCP frames use durable
content-addressed evidence references. Wire MCP evidence is authoritative
after correlation, while reported evidence and conflicts remain inspectable.

This rollout does not implement live typed snapshots, typed streaming,
provider-hidden-reasoning recovery, automatic discovery calls, the UI rewrite,
a second projection database, v1 compatibility branches, or arbitrary public
metadata dictionaries. Canonical payloads remain an internal raw audit escape
hatch only.

### Observation state matrix and invariants

| State | Value | Reason | Contract |
| --- | --- | --- | --- |
| `OBSERVED` | Required | Optional | Provider emitted and capture succeeded. |
| `NOT_EMITTED` | Forbidden | Required | Source did not emit the field. |
| `UNSUPPORTED` | Forbidden | Required | Capability does not apply. |
| `UNAVAILABLE` | Forbidden | Required | Applicable value could not be observed. |
| `PROVIDER_HIDDEN` | Forbidden | Required | Provider intentionally withholds it. |
| `ENCRYPTED` | Forbidden | Required | Signature/encrypted content exists; evidence ref optional. |
| `REDACTED` | Optional safe partial | Required | Policy removed sensitive content. |
| `TRUNCATED` | Optional safe partial | Required | A capture limit removed the remainder. |

`Observation[T]` has `state`, typed optional `value`, optional bounded reason,
provenance tuple, and optional `RawEvidenceRef`. Invalid state/value/reason
combinations are rejected. Hidden or encrypted reasoning is never converted
to plaintext. The allowed reason matrix is closed: `NOT_EMITTED` requires
`PROVIDER_DID_NOT_EMIT`; `PROVIDER_HIDDEN` requires `PROVIDER_HIDDEN`;
`ENCRYPTED` requires `PROVIDER_ENCRYPTED`; `REDACTED` requires
`REDACTED_BY_POLICY`; `TRUNCATED` requires `EVIDENCE_TRUNCATED`;
`UNSUPPORTED` accepts only `HARNESS_UNSUPPORTED` or
`TRANSPORT_NOT_APPLICABLE`; and `UNAVAILABLE` accepts only
`CAPTURE_DISABLED`, `CAPTURE_FAILED`, `CORRELATION_UNAVAILABLE`, or
`MALFORMED_SOURCE`. `OBSERVED` requires `reason=None`.

### Exact model and timing contract

`TraceEntryBase` has entry/kind/parent IDs, execution/session/turn/server and
connection identities, inclusive source sequence range, `TraceTiming`, status,
provenance and limitations. `TraceTiming` has UTC `started_at`, optional UTC
`finished_at`, finite nonnegative offsets, and duration exactly equal to end
minus start. End and finished-at cannot precede their starts; point events are
zero-duration. Naive timestamps and nonfinite values are invalid.

The `TraceEntry` discriminator covers lifecycle, message, reasoning,
tool-call, protocol, initialization, usage, interaction, process, workspace,
artifact, evaluation, diagnostic, redacted raw-message, and provider entries.
`TraceView` has schema ID/version, trace/execution identity, outcome,
completeness/limitations, discriminated runtime, summary, and ordered
timeline. Its typed indexes are `tool_calls`, `messages`, `reasoning`,
`protocol`, `raw_messages`, `interactions`, `processes`, and `diagnostics`.
`for_turn`, `for_session`, `for_server`, and inclusive-overlap `between`
preserve identity, outcome, limitations, provenance and order.

`UsageValue` and `InitializationValue` are value-only models. Summary/runtime
usage and initialization observations use these values rather than entry
models, so they never require timeline IDs or execution IDs.

### Public API, finalized-only behavior, and errors

Later rounds implement:

```python
TraceResult.view() -> TraceView
ExecutionResult.trace_view -> TraceView
ExecutionStore.get_trace(execution_id: ExecutionId | str) -> TraceResult | None
ExecutionStore.get_trace_view(execution_id: ExecutionId | str) -> TraceView | None
ExecutionStore.read_raw_evidence(reference: RawEvidenceRef, *, max_bytes: int = 1_048_576) -> RawEvidence
MCPTestKit.get_trace(execution_id) -> TraceResult
MCPTestKit.get_trace_view(execution_id) -> TraceView
MCPTestKit.read_raw_evidence(reference) -> RawEvidence
```

Async kits provide equivalent awaitable methods. Calling `view()` before a
terminal `execution.finished` event raises `TraceNotFinalized`. Store methods
return `None` only for an unknown execution; an existing execution without
usable evidence raises `TraceUnavailable`. Invalid/missing raw references
raise `RawEvidenceUnavailable` or `RawEvidenceIntegrityError`. Truncated
reports cannot silently claim complete views.

### Capture limits and storage lifecycle

`TraceCaptureConfig` defaults to raw evidence/provider messages/stderr enabled,
64 KiB redacted inline preview, 1 MiB redacted frame maximum, and 64 MiB
redacted raw evidence per execution. Caps are positive integer settings;
crossing one creates a `TRUNCATED` observation and limitation.

Raw evidence is structurally projected with the existing fail-closed
`redact_result` walker whenever it is valid UTF-8 JSON, which removes sensitive
keys and URL/query credentials even without configured canary values. Invalid
JSON, text, and opaque binary use the byte-canary pass; configured canaries are
always removed before either cap or any blob-store publication. A structural
no-op preserves the byte-pass output rather than reserializing it.

Canonical events remain the source of truth. Existing `v2_event_blobs` stores
semantic payloads under `payload` and redacted frames under `raw_evidence`.
`RawEvidenceRef` carries SHA-256, size, media type, and opaque durable key.
Temporary files are materialized before cleanup; reads verify digest and size.
Deletion decrements payload/evidence references and garbage-collects only
zero-reference blobs. In-memory and SQLite stores match. Completed, failed,
timed-out, and cancelled executions retain traces. Persistence failures add
bounded diagnostics/limitations and preserve earlier evidence/outcome.

### Projector and correlation algorithm

The projector requires one execution ID, contiguous source sequence, and one
terminal event; source events are never mutated. Chunks coalesce by provider
message ID/block index, otherwise adjacent same-turn/role/kind chunks; raw
chunks remain available.

MCP pairing uses connection ID, request sequence, direction, then typed
JSON-RPC ID validation. Integer and string IDs are distinct. Unmatched
requests become incomplete and unmatched responses become diagnostics.

Harness/wire calls correlate by shared call ID, explicit raw reference, then
exactly one unused chronological same-turn/server/tool candidate. Name-only or
ambiguous calls stay separate. Wire wins for server/tool/arguments/result,
error/status/latency; reported and wire values remain in their own fields;
non-empty divergence creates `EvidenceConflict`. No guessing or overwriting.

### Source mappings and harness-specific availability

OpenCode captures safe HTTP metadata, bounded responses, provider/model,
finish/error/tokens/cache/cost, text/reasoning/tool parts and call state.
Claude captures bounded stream JSON, text/thinking/signature/tool blocks and
deltas, message/model/stop/result/service metadata, usage/cache/cost/duration,
stderr and process state; signature-only thinking is `ENCRYPTED`. ACP keeps
ACP and MCP frames distinct and captures identity, modes/config, messages,
thoughts, tool updates, plans/states, permission/filesystem/terminal/
elicitation/sampling interactions; usage absent from the protocol is explicit
`UNSUPPORTED` with `HARNESS_UNSUPPORTED`.

OpenCode provider/model/usage/cost, Claude thinking/cache/stop metadata, and
ACP modes/plans/config/interactions are harness-specific extensions. HTTP
status is unsupported for stdio; ACP usage/cost is `UNSUPPORTED` when the
protocol does not expose it, and `UNAVAILABLE` only when an exposed field
cannot be captured; server processing time is unavailable unless independently
reported.

Current source inputs map as follows: `CanonicalEvent` kinds
`mcp.initialized`, `mcp.request`, `mcp.response`, `mcp.error`,
`mcp.notification`, `mcp.progress`, `mcp.cancellation_*`, `agent.message`,
`assistant.content`, `tool.call_requested`, `tool.result_received`,
`reasoning`, lifecycle/process/interaction, `provider.event`, diagnostic,
artifact, workspace, evaluation, and cleanup events become the corresponding
timeline entries. `DirectTraceBridge`/`ExecutionTraceRecorder` provide direct
MCP observations. `harness/opencode.py` supplies OpenCode HTTP responses and
parts; `harness/claude.py` supplies Claude stream-JSON lines; `harness/acp.py`
supplies ACP session/update frames. Existing normalized helpers in
`trace/normalized.py`, `trace/claude.py`, and `trace/acp.py` are characterized
as source evidence during R5–R8, not copied as a second public schema.

### Matcher, UI boundary, files, and gates

Matchers normalize finalized views/results/session results and preserve
existing tool/server/argument/result/status/count/turn/latency/evidence-source
semantics. Negative assertions require finalization; failures include only
redacted IDs/correlation/limitations. User tests never rewrite payloads.

This PR does not modify Streamlit. A parity fixture must cover hierarchy,
messages/thinking/tools, MCP calls/frames, args/results/errors, transports,
latency/duration, tokens/cost, capture limits, result metadata, ACP updates,
Claude encrypted state, and OpenCode provider/model. The following UI PR
renders only `TraceView` and removes harness-specific normalization.

Likely R1 files are this plan, `sdk/src/mcp_pal/observability.py`,
`errors.py`, package exports, sync/async exports, parity manifest, and typed
model tests. R2–R5 touch configuration/storage/recorders/contracts; R6–R8
touch adapters/fixtures; R9–R10 touch projector/matcher/docs/regressions.

R1 → R2 → R3 → R4 → R5 → R6/R7/R8 → R9 → R10 is the dependency order. Each
round is independently tested and reviewed. OpenCode live E2E is required;
Claude and ACP live tests are optional with explicit prerequisites. Deterministic
fixtures are always merge gates.

### Detailed test matrix and forbidden assumptions

- [x] Every R1 state/value/reason combination, deep immutability and generic JSON round-trip.
- [x] R1 schema for every model and both aliases; every entry/runtime discriminator and round-trip.
- [x] R1 timezone normalization, naive/nonfinite rejection, timing/sequence/completeness invariants.
- [x] R1 indexes, inclusive boundaries, overlap/order, and identity/outcome/limitation preservation.
- [x] Direct transports, protocol/tool success/failures, typed IDs, unmatched/ambiguous/conflicting calls.
- [x] OpenCode/Claude/ACP fields, unavailable capabilities, hidden reasoning, and cross-harness common parity.
- [x] R2 redaction-before-write, frame/execution caps, malformed/missing/
  tampered references, structural JSON/URL redaction, persistence/reopen,
  integrity, fixed opaque IDs, role/evidence-ID coexistence, model invariants,
  refcounts, and zero-reference GC.
- [x] R3–R9 malformed harness frames, process/timeout/cancel capture,
  projector persistence and cross-harness outcomes.
- [x] R1 sync/async/export parity.
- [x] Matchers on untouched traces, docs/packaging, UI parity, and required live OpenCode.

Never stringify JSON-RPC IDs, correlate duplicate calls by guess, trust reports
over wire evidence, infer hidden reasoning, estimate absent usage, expose
secrets through evidence/errors/headers/stderr, claim complete from truncated
pages, use provider strings as diagnostic codes, mutate canonical events, add
a compatibility database reset, or broaden this PR into UI work. The SDK is
not production-stable; all terminal executions retain traces and finalized
views are sufficient for this rollout.

## Lower-capability-agent implementation declarations

The following declarations are normative. An implementation agent must not
add fields or change names without updating this plan and obtaining review.

```python
class UsageValue(FrozenModel):
    input_tokens: Observation[int]
    output_tokens: Observation[int]
    reasoning_tokens: Observation[int]
    cache_creation_tokens: Observation[int]
    cache_read_tokens: Observation[int]
    cache_write_tokens: Observation[int]
    total_tokens: Observation[int]
    cost: Observation[float]
    currency: Observation[str]

class InitializationValue(FrozenModel):
    protocol_version: Observation[str]
    server_name: Observation[str]
    server_version: Observation[str]
    instructions: Observation[str]
    capabilities: Observation[JsonValue]
    tools: Observation[tuple[DirectTool, ...]]
    resources: Observation[tuple[DirectResource, ...]]
    resource_templates: Observation[tuple[DirectResourceTemplate, ...]]
    prompts: Observation[tuple[DirectPrompt, ...]]

class TraceSummary(FrozenModel):
    timing: TraceTiming
    usage: Observation[UsageValue]
    turn_count: int
    message_count: int
    reasoning_count: int
    tool_call_count: int
    successful_tool_call_count: int
    failed_tool_call_count: int
    protocol_error_count: int
    activity_health: ActivityHealth
    cleanup_status: TraceStatus

class RawEvidence(FrozenModel):
    reference: RawEvidenceRef
    media_type: str
    content: JsonValue | str
    size_bytes: int
    returned_size_bytes: int
    truncated: bool = False
    redacted: Literal[True] = True

class RawEvidenceCapture(FrozenModel):
    reference: RawEvidenceRef
    preview: Observation[str]
    original_size_bytes: int
    stored_size_bytes: int
    redacted: bool
    truncated: bool

class TraceCaptureConfig(FrozenModel):
    capture_raw_evidence: bool = True
    capture_provider_messages: bool = True
    capture_stderr: bool = True
    raw_preview_bytes: int = 65_536
    raw_frame_bytes: int = 1_048_576
    raw_execution_bytes: int = 67_108_864
```

Every `Observation` default is `NOT_EMITTED` with
`PROVIDER_DID_NOT_EMIT`; ACP aggregate usage defaults to `UNSUPPORTED` with
`HARNESS_UNSUPPORTED`. `TraceEntryBase` exact fields are `entry_id`, `kind`,
`parent_id`, `execution_id`, `session_id`, `turn_id`, `server_binding`,
`connection_id`, `sequence_start`, `sequence_end`, `timing`, `status`,
`provenance`, and `limitations`. Entry-specific fields are listed above in
the public model section and are closed by `extra="forbid"`.

### Complete entry and runtime declarations

The field types below are exact (defaults are omitted only where the model
uses its documented `NOT_EMITTED` observation factory):

```python
class MessageEntry(TraceEntryBase):
    message_id: Observation[str]
    role: MessageRole
    content: tuple[ContentBlock, ...]
    stop_reason: Observation[str]
class ReasoningEntry(TraceEntryBase):
    block_id: Observation[str]
    content: Observation[tuple[ContentBlock, ...]]
class ToolCallEntry(TraceEntryBase):
    call_id: str
    provider_call_id: Observation[str]
    server: Observation[str]
    tool: Observation[str]
    arguments: Observation[JsonValue]
    result: Observation[ToolResult]
    tool_status: ToolCallStatus
    correlation: CorrelationState
    jsonrpc_id: Observation[JsonRpcId]
    server_latency_ms: Observation[float]
    policy: Observation[ToolPolicyDecision]
    reported: Observation[ReportedToolCall]
    wire: Observation[WireToolCall]
    conflicts: tuple[EvidenceConflict, ...]
class ProtocolEntry(TraceEntryBase):
    protocol: ProtocolKind
    method: Observation[str]
    direction: EventDirection
    jsonrpc_id: Observation[JsonRpcId]
    request: Observation[JsonValue]
    response: Observation[JsonValue]
    error: Observation[ProtocolErrorDetails]
    http: Observation[HttpExchangeMetadata]
class InitializationEntry(TraceEntryBase):
    protocol_version: Observation[str]
    server_name: Observation[str]
    server_version: Observation[str]
    instructions: Observation[str]
    capabilities: Observation[JsonValue]
    tools: Observation[tuple[DirectTool, ...]]
    resources: Observation[tuple[DirectResource, ...]]
    resource_templates: Observation[tuple[DirectResourceTemplate, ...]]
    prompts: Observation[tuple[DirectPrompt, ...]]
class UsageEntry(TraceEntryBase):
    input_tokens: Observation[int]; output_tokens: Observation[int]
    reasoning_tokens: Observation[int]
    cache_creation_tokens: Observation[int]; cache_read_tokens: Observation[int]
    cache_write_tokens: Observation[int]; total_tokens: Observation[int]
    cost: Observation[float]; currency: Observation[str]
class InteractionEntry(TraceEntryBase):
    interaction_kind: str; request: Observation[JsonValue]; response: Observation[JsonValue]
class ProcessEntry(TraceEntryBase):
    executable: Observation[str]; pid: Observation[int]
    exit_code: Observation[int]; signal: Observation[int]; stderr: Observation[str]
class WorkspaceEntry(TraceEntryBase):
    change: Observation[JsonValue]
class ArtifactEntry(TraceEntryBase): artifact: ArtifactRef
class EvaluationEntry(TraceEntryBase): evaluation: EvaluationResult
class DiagnosticEntry(TraceEntryBase): code: str; message: str
class RawMessageEntry(TraceEntryBase):
    source: RawEvidenceSource; direction: EventDirection; media_type: str
    preview: Observation[JsonValue | str]; evidence_ref: RawEvidenceRef | None
    size_bytes: int; redacted: Literal[True]
class ProviderEntry(TraceEntryBase):
    provider: str; category: str; data: Observation[JsonValue]

class DirectTraceInfo(FrozenModel):
    transport: Observation[TransportKind]; protocol: Observation[str]
    initialization: Observation[InitializationValue]
class OpenCodeTraceInfo(FrozenModel):
    session_id: Observation[str]; provider_id: Observation[str]; model_id: Observation[str]
    finish_reason: Observation[str]; http_lifecycle: Observation[JsonValue]
    usage: Observation[UsageValue]
class ClaudeCodeTraceInfo(FrozenModel):
    session_id: Observation[str]; model_id: Observation[str]
    result_subtype: Observation[str]; stop_reason: Observation[str]
    service_tier: Observation[str]; api_duration_ms: Observation[float]
    encrypted_reasoning: Observation[bool]; usage: Observation[UsageValue]
class ACPTraceInfo(FrozenModel):
    session_id: Observation[str]; protocol_version: Observation[str]
    agent_identity: Observation[JsonValue]; available_modes: Observation[JsonValue]
    current_mode: Observation[str]; config_options: Observation[JsonValue]
    selected_config: Observation[JsonValue]; plan_state_available: Observation[bool]
    usage: Observation[UsageValue]
```

`ToolResult`, `ReportedToolCall`, `WireToolCall`, `EvidenceConflict`,
`ProtocolErrorDetails`, `HttpExchangeMetadata`, and `SafeHttpHeader` are
closed frozen models with the exact fields named in the public model section;
all provider-dependent members are `Observation[...]`. `TraceView` has exact
fields `schema_id`, `schema_version`, `trace_id`, `execution_id`, `outcome`,
`completeness`, `limitations`, `runtime`, `summary`, and `timeline`.

### R2 storage declarations

`RawEvidenceRef` is the existing frozen model with `evidence_id`, optional
64-lowercase-hex `sha256`, optional nonnegative `size_bytes`, optional
`media_type`, and optional opaque `storage_key`. `evidence_id` is a fixed-size
deterministic opaque identifier `re:<sha256(event_id UTF-8)>`; it is not
reversible and is stored on raw-evidence reference rows. The current fresh
schema has
`v2_event_blobs(event_id TEXT NOT NULL REFERENCES v2_events(id) ON DELETE
CASCADE, sha256 TEXT NOT NULL REFERENCES v2_blobs(sha256), role TEXT NOT NULL
CHECK(role IN ('payload','raw_evidence')), media_type TEXT, evidence_id TEXT
UNIQUE, CHECK((role = 'payload' AND evidence_id IS NULL AND media_type IS NULL) OR (role =
'raw_evidence' AND evidence_id IS NOT NULL AND media_type IS NOT NULL AND
length(media_type) > 0)), PRIMARY KEY(event_id, role))`. Payload rows also
require `media_type IS NULL`; raw rows require both reference fields
non-null/nonempty. Media type and evidence ID belong to the event-role
reference, not the digest-global `v2_blobs` row. The evidence ID is validated
as `re:` plus 64 lowercase hex characters.
`RawEvidence` rejects a returned size larger than the stored size and requires
its `truncated` flag to equal the strict size comparison. `RawEvidenceCapture`
requires a matching reference size when supplied, an identical preview
reference, and preview state precedence `TRUNCATED`, then `REDACTED`, then
`OBSERVED` according to its two booleans.
This explicitly replaces the current `event_id`-only primary key and
`CHECK(role='payload')`; no runtime v1/v2 compatibility branch is added. Thus
one event can have at most one payload row and one raw-evidence row. The
internal write API is exactly
`put_raw_evidence(event_id: EventId | str, content: bytes, *, media_type: str) -> RawEvidenceCapture`;
the referenced event must already exist, and its owning execution must own the
capture budget. The public read API remains reference-based:
`read_raw_evidence(reference: RawEvidenceRef, *, max_bytes=1_048_576) -> RawEvidence`.
Lookup validates `evidence_id` and resolves it through the raw-evidence row to
`(event_id, raw_evidence)`, then uses `storage_key`/`sha256` to locate the
content-addressed `v2_blobs` bytes.
Its size/hash/media type are verified on read, and reference counts are
decremented transactionally during execution deletion. The deletion API is
exactly
`delete_execution(execution_id) -> None` on both in-memory and SQLite stores.
Capture config caps are 65_536, 1_048_576, and 67_108_864 bytes by default.
Both concrete stores accept `capture_config: TraceCaptureConfig | None`; their
existing `config: RedactionConfig | None` parameter remains solely the
redaction configuration. `RawEvidenceCapture.preview` is the metadata later
persisted by R5 into canonical observation events; R2 does not add that
projector behavior.
The implementation files are `observability.py`, `storage/evidence.py`,
`storage/ephemeral.py`, `storage/sqlite.py`, and
`tests/unit/test_raw_evidence_storage.py`. R2 includes a fresh-schema
coexistence test inserting both roles for one event, rejecting a third
duplicate role, enforcing the role check, resolving evidence_id/storage_key,
and confirming cascade/ref-count behavior after execution deletion.

### R3 projector declarations

Implement pure `mcp_pal.trace.projector.TraceProjector.project(trace: TraceResult)
-> TraceView` and optional `from_events(events, *, trace_id, execution_id,
outcome, completeness, limitations) -> TraceView`. Integrate only through `TraceResult.view()`,
`ExecutionResult.trace_view`, and store/kit lookup methods. The projector
must not import storage, UI, application, pytest, or adapter implementations.

### R4 matcher declarations

Normalize subjects to a finalized `TraceView`; implement
`to_have_tool_call(name=None, *, server=None, arguments=None, result=None,
status=None, count=None, turn=None, latency_ms=None, evidence="any")` using
typed fields. Preserve exact/partial/regex/unordered/tolerant argument
matching, min/max counts, positive/negative assertions, and redacted failure
diagnostics. Never parse canonical payloads in matchers.

### R5 internal declarations

`HarnessObservation` is a closed discriminator over `raw_frame`,
`message_chunk`, `reasoning_chunk`, `tool_call_observed`,
`tool_result_observed`, `usage_observed`, `plan_observed`, `state_observed`,
`interaction_observed`, `process_observed`, and `metadata_observed`. Every
observation carries observation ID, harness kind, turn sequence, wall-clock
time, monotonic offset, optional provider/block/call ID, typed fields, and
optional raw evidence input. `HarnessObservationSink.emit(observation) ->
None` is non-throwing; parsing/persistence failures become safe diagnostics
and limitations. Public `TurnEvidence` and `HarnessSessionEvidence` replace
untyped evidence maps.

`harness_kind` is intentionally a bounded, nonempty string rather than a
closed enum: it is the stable adapter-plugin identifier, so a new adapter does
not require changing this provider-neutral contract. It is never used as a
provider-specific schema discriminator; the closed `kind` field is the only
observation union discriminator.

### Concrete round files and tests

- R1: `observability.py`, `errors.py`, package exports, parity manifest,
  `test_observability_models.py`; gate all model/state/schema/discriminator,
  filter, deep-freeze and export tests.
- R2: `observability.py`, `storage/evidence.py`, `storage/blobs.py`,
  `storage/sqlite.py`, `storage/ephemeral.py`, and
  `test_raw_evidence_storage.py`; gate config defaults, redaction-before-write,
  preview/read bounds, reopen/hash/role validation, rollback, and refcount GC.
- R3: `trace/projector.py`, `direct_trace.py`, execution result/store APIs,
  direct projector tests; gate direct stdio/HTTP/SSE/in-process traces.
- R4: `matchers.py`, matcher unit/adversarial tests and examples; gate all
  assertions on untouched views.
- R5: `harness/contracts.py`, `harness/base.py`, execution recorder/evidence
  models and sink tests; gate malformed/failure/timeout/cancel retention.
- R6: `harness/opencode.py`, OpenCode trace adapter/fixtures and live test;
  gate deterministic fixture plus required live OpenCode E2E.
- R7: `harness/claude.py`, Claude trace adapter/fixtures and optional live
  test; gate visible/encrypted thinking, deltas, usage and process state.
- R8: `harness/acp.py`, ACP trace adapter/fixtures and optional live test;
  gate modes/config/plans/states/interactions and unsupported usage when absent
  from the protocol.
- R9: cross-harness fixture/persistence tests; gate all terminal outcomes,
  SQLite reopen, common-field parity and extension isolation.
- R10: docs/examples/UI parity fixture/package/typecheck/regression files;
  gate full suite, required live OpenCode, optional-prerequisite reporting.
