---
title: "Public value types"
description: "Public values are immutable, serializable contracts. Construct server and operation specifications as inputs; treat result, trace, evidence, and report models as outputs."
---

# Public value types

Public values are immutable, serializable contracts. Construct server and
operation specifications as inputs; treat result, trace, evidence, and report
models as outputs.

## Servers

| Type | Use |
| --- | --- |
| `StdioServer` | Launch a command with explicit arguments, environment, and working directory. The client owns the process. |
| `HTTPServer` | Connect to a Streamable HTTP URL with explicit headers and trust classification. The client does not own the service. |
| `InProcessServer` | Bind an in-process implementation for controlled tests. |
| `ServerBinding` | Use an inline server or a saved `ServerProfileRef`. |
| `SecretReference` | Refer to a secret without embedding its value in a durable model. |

## Direct operations

`DirectSpec` describes one direct execution. `DirectOperation` is the common
operation union. Concrete operations are `CallTool`, `ListTools`,
`ListResources`, `ListTemplates`, `ListPrompts`, `ReadResource`, `GetPrompt`,
and `Ping`. Each stores the operation inputs; execution produces a typed result.

## Results and outcomes

`ExecutionResult` contains the finalized execution snapshot and trace.
`TurnResult` contains one agent turn. `ExecutionOutcome` and `TurnOutcome`
describe terminal meaning separately from pytest.

`ExecutionReport` and `ExecutionEvidence` are durable report projections.
`TraceResult` is the captured trace envelope. `EvidenceRef` and `ArtifactRef`
refer to stored material; their existence does not imply that raw content is
available.

## Content and policy

`UserMessage` and `TextContent` are supported message/content values.
`ToolPolicy` controls advertised tools. `WorkspacePolicy`, `PermissionPolicy`,
`SamplingPolicy`, `FilesystemPolicy`, and `TerminalPolicy` constrain harness
interactions. The concrete harness must support the requested control.

## Evaluations

`EvaluationContext` is passed to evaluator code. It returns an
`EvaluationDecision`, which becomes an `EvaluationResult` and optionally a
durable `EvaluationRecord`. `EvaluationStatus` distinguishes passed, failed,
error, inconclusive, and not-run states; `EvaluationSource` records provenance.

## Capabilities

`Capability`, `Readiness`, and `ProtocolConstraint` describe checked support.
They are evidence-bearing values, not promises that an unchecked combination
works. `EVENT_SCHEMA_ID` and `EVENT_SCHEMA_VERSION` identify the public event
wire schema.
