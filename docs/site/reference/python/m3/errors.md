---
title: "Exceptions"
description: "Normal MCP tool errors usually remain typed tool results rather than raised exceptions. Test result.is_error when the server intentionally returned a tool-level failure."
---

# Exceptions

| Exception | Meaning |
| --- | --- |
| `MCPError` | Base public MCP failure. |
| `ProtocolError` | Invalid or failed MCP protocol exchange. |
| `TransportError` | Process, stream, or HTTP transport failure. |
| `OperationTimeout` | Configured deadline elapsed. |
| `OperationCancelled` | Caller or owning runtime cancelled the work. |
| `CleanupError` | Resource cleanup failed. |
| `KitClosed` | Work was requested after the kit closed. |
| `SessionBusy` | Concurrent work conflicts with the session state. |
| `SessionStillOpen` | A finalized result was requested before session closure. |
| `TraceNotFinalized` | Final trace data was requested too early. |
| `TraceUnavailable` | The execution has no usable trace. |
| `RawEvidenceUnavailable` | Required raw evidence was not captured or retained. |
| `RawEvidenceIntegrityError` | Stored raw evidence failed integrity checks. |
| `ExecutionNotFound` | Requested execution identity is absent. |
| `InvalidTransitionError` | Execution state transition is invalid. |
| `ModelValidationError` | Public input or result model failed validation. |
| `UnsupportedFeature` | The selected integration does not support the operation. |
| `ElicitationExpectationError` | An input request did not satisfy its plan. |
| `ElicitationRoundLimitError` | The supported interaction round limit was exceeded. |

Normal MCP tool errors usually remain typed tool results rather than raised
exceptions. Test `result.is_error` when the server intentionally returned a
tool-level failure.
