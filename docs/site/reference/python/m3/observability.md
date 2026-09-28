# Observability models

`TraceView` is the stable typed projection for reading a finalized trace. Its
entries preserve whether a value was observed, reported, inferred, redacted,
or unavailable.

Main entry groups:

- Initialization and lifecycle: `InitializationEntry`, `LifecycleEntry`,
  `ProcessEntry`, `RuntimeTraceInfo`, `TraceStatus`, `TraceTiming`.
- Protocol and transport: `ProtocolEntry`, `ProtocolCallAttempt`,
  `ProtocolErrorInfo`, `ProtocolKind`, `TransportEntry`, `HttpExchange`.
- Tools: `ToolCallEntry`, `ToolCallAttempt`, `ToolCallStatus`, `ToolResult`,
  `ReportedToolCall`, `WireToolCall`.
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
