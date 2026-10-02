<!-- Generated from docs/site/reference/python/m3/observability.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Observability models

`TraceView` is the stable typed projection for reading a finalized trace. Its
entries preserve whether a value was observed, reported, inferred, redacted,
or unavailable.

Main entry groups:

Scope a trace with `trace_view.for_turn(turn)`, `for_session`, `for_server`,
or `between`.

The top-level fields are `schema_id`, `schema_version`, `trace_id`,
`execution_id`, `outcome`, `completeness`, `limitations`, `runtime`, and
`summary`, plus `timeline`. Indexes include `messages`, `reasoning`,
`tool_calls`, `protocol`, `transports`, `interactions`, `processes`,
`diagnostics`, and `raw_messages`.

Use `view.model_dump(mode="json")` to serialize a view. Observation states
such as unavailable, unsupported, hidden, encrypted, redacted, and truncated
describe whether a value can be read; check `state` and `reason` before
reading `value`. Call `read_raw_evidence(reference, max_bytes=...)` while the
kit or store is open.

The current `schema_version` is `"2.0"`, and a view with any other version is
rejected. Each tool argument and result is stored once, on its `ToolCallEntry`.
`correlation` records which evidence saw the call: `wire_only` and `correlated`
calls were observed on the wire, `reported_only` calls were only reported by
the harness. `ToolCallAttempt` and `ProtocolCallAttempt` keep per-round MRTR
state, and `ToolCallAttempt.latency_ms` is the latency of that round. The raw
request and response of each attempt are in the event log, at the attempt's
`sequence_start` and `sequence_end`.

`summary.usage` is the latest usage entry, not a sum. `usage.cost.value` and
`usage.currency.value` may be absent.

- Initialization and lifecycle: `InitializationEntry`, `LifecycleEntry`,
  `ProcessEntry`, `RuntimeTraceInfo`, `TraceStatus`, `TraceTiming`.
- Protocol and transport: `ProtocolEntry`, `ProtocolCallAttempt`,
  `ProtocolErrorInfo`, `ProtocolKind`, `TransportEntry`, `HttpExchange`.
- Tools: `ToolCallEntry`, `ToolCallAttempt`, `ToolCallStatus`, `ToolResult`,
  `ReportedToolCall`.
- Messages and model activity: `MessageEntry`, `MessageRole`, `ReasoningEntry`,
  `ProviderEntry`, `UsageEntry`, `UsageValue`.
- Interactions: `InteractionEntry`, `ElicitationEntry`, `WorkspaceEntry`.
- Evidence: `Observation`, `ObservationState`, `ObservationReason`,
  `RawEvidence`, `RawEvidenceSource`, `EvidenceCapture`, `EvidenceConflict`,
  `ArtifactEntry`, `RawMessageEntry`, `SafeHttpHeader`.

Harness-specific projections are `ACPTrace`, `ClaudeCodeTrace`, `CodexTrace`,
`OpenCodeTrace`, and `PiTrace`; `DirectTrace` covers direct MCP work.

`CaptureOptions` configures capture at execution creation. Redaction and missing
provider fields can make raw or normalized evidence unavailable. APIs that
require finalized or raw evidence raise the corresponding documented error
rather than fabricating a value.
