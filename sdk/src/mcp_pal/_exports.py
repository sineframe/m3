"""Checked manifest of the public SDK surface."""

PUBLIC_EXPORTS: dict[str, tuple[str, ...]] = {
    "mcp_pal": (
        "__version__", "MCPTestKit", "AgentSession", "ExecutionHandle", "HarnessAdapter", "Probes", "ConfigOrigin", "ConfigSource", "Config", "ConfigError", "ProbeEvidence", "ProbeKind", "ProbeReport", "ProbeRequest", "ProbeResult", "ACPAgentIdentity", "ACPAgentMode", "ACPProbeDimension", "ACPProbeHistory", "ACPProbeKind", "ACPProbeRequest", "ACPProbeResult", "ACPProbeStatus", "ACPProbeStore", "redact_probe", "run_acp_probe", "load_config",
        "EVENT_SCHEMA_ID", "EVENT_SCHEMA_VERSION", "Event", "EventDirection", "EventKind", "EventOrigin", "PayloadRef", "EventSource", "JsonRpcId", "LifecyclePhase", "EvidenceRef", "ReasoningState", "ReasoningVisibility", "RequestLink", "EvaluationGroup", "EvaluationQuery", "EvaluationReport", "EvaluationStats", "HealthStats", "LatencyStats", "ToolCallStats",
        "ACPAgent", "ActivityHealth", "AgentSpec", "ArtifactId", "ArtifactPolicy",
        "ArtifactRef", "AudioContent", "Capability", "CapabilityStatus", "ClaudeCode",
        "ConnectionId", "ContentBlock", "DirectSpec", "DirectOperation", "ToolInfo", "ResourceInfo", "TemplateInfo", "PromptInfo", "ErrorCode", "ErrorInfo", "EvaluationContext", "EvaluationDecision", "EvaluationSource", "EvaluationRecord", "RunId",
        "ElicitationPolicy", "EvaluationId", "EvaluationRegistration", "EvaluationResult", "EvaluationStatus", "EventId", "ExecutionEvidence", "ExecutionId",
        "ExecutionOutcome", "ExecutionPage", "ExecutionResult", "ExecutionState", "ExecutionSpec", "FullToolPolicy", "HarnessId",
        "HarnessProfileId", "HarnessProfileRef", "HarnessSpec", "HarnessValue", "Identifier",
        "InProcessServer", "FileContent", "FilesystemPolicy", "FrozenModel", "ImageContent", "ExecutionStatus", "Metadata", "NativeToolPolicy", "OpaqueContent", "OpenCode",
        "PermissionPolicy", "ProtocolConstraint", "Readiness", "ResourceLink", "RevisionId", "RevisionSelection", "RestrictiveToolPolicy", "SSEServer",
        "SamplingPolicy", "SecretReference", "ServerBinding", "ServerDefinition", "ServerId",
        "ServerProfileId", "ServerProfileRef", "SessionForkRequest", "SessionId", "SessionSource", "ExecutionReport", "StdioServer", "HTTPServer",
        "TerminalPolicy", "TextContent", "TraceId", "TraceResult", "TransportKind", "TurnId",
        "TurnStatus", "TurnOutcome", "TurnResponse", "TurnResult", "TurnState", "UserMessage",
        "WorkspaceKind", "WorkspacePolicy", "ListTools", "ListResources", "ListTemplates", "ListPrompts", "CallTool", "ReadResource", "GetPrompt", "Ping", "DirectResult", "ListToolsResult", "ListResourcesResult", "ListTemplatesResult", "ListPromptsResult", "CallToolResult", "ReadResourceResult", "GetPromptResult", "PingResult", "CleanupError", "ExecutionNotFound", "InvalidTransitionError", "KitClosed", "MCPError",
        "ModelValidationError", "OperationCancelled", "OperationTimeout", "ProtocolError", "SessionBusy",
        "SessionStillOpen", "ServerValue", "ToolPolicy", "TransportError", "TraceNotFinalized", "TraceUnavailable", "RawEvidenceUnavailable", "RawEvidenceIntegrityError", "TrustLevel", "UnsupportedFeature", "expect", "check",
        "ToolDescriptor", "ToolPolicyDecision", "ToolPolicyEvidence", "ToolPolicyEvaluator", "evaluate_tool_policy",
        "AsyncEvaluator", "EvaluationRunner", "EvaluationStore", "EvaluationVerdict", "Evaluator", "EvaluatorCallable", "EvaluatorRegistry", "EvaluatorRegistration", "InMemoryEvaluationStore", "RequiredEvaluationError", "SnapshotOptions", "snapshot",
        "AllowedCommands", "ElicitationRequest", "ElicitationResult", "ElicitationHandler", "FilesystemHandler", "FilesystemRequest", "FilesystemResult", "Interactions", "InteractionHandlers", "InteractionReceipt", "PermissionRequest", "PermissionResult", "PermissionHandler", "SamplingRequest", "SamplingResult", "SamplingHandler", "TerminalHandler", "TerminalRequest", "TerminalResult", "WorkspaceFiles",
        "ToolCase", "ServerCase", "HarnessCase", "ToolMatrix", "HarnessMatrix", "ToolMatrixCase", "HarnessMatrixCase",
    ),
        "mcp_pal.types": (
        "EVENT_SCHEMA_ID", "EVENT_SCHEMA_VERSION", "ACPAgent", "ActivityHealth", "AgentSpec", "ArtifactId", "ArtifactPolicy", "ArtifactRef",
        "AudioContent", "Event", "Capability", "CapabilityStatus", "ClaudeCode",
        "ConnectionId", "ContentBlock", "DirectSpec", "DirectOperation", "ToolInfo", "ResourceInfo", "TemplateInfo", "PromptInfo", "ElicitationPolicy", "ErrorCode", "ErrorInfo",
        "EventDirection", "EventKind", "EventOrigin", "PayloadRef", "EventSource", "EvaluationContext", "EvaluationDecision", "EvaluationId", "EvaluationSource", "EvaluationRegistration", "EvaluationResult", "EvaluationStatus", "EvaluationRecord", "RunId",
        "EventId", "ExecutionId", "ExecutionEvidence", "ExecutionOutcome", "ExecutionPage", "ExecutionResult", "ExecutionState", "ExecutionSpec", "FileContent",
        "FilesystemPolicy", "FrozenModel", "FullToolPolicy", "HarnessId", "HarnessProfileId", "HarnessProfileRef",
        "HarnessSpec", "HarnessValue", "Identifier", "ImageContent", "InProcessServer", "ExecutionStatus",
        "JsonRpcId", "LifecyclePhase", "Metadata", "NativeToolPolicy", "OpaqueContent", "OpenCode", "PermissionPolicy", "ProtocolConstraint",
        "Readiness", "ResourceLink", "RevisionId", "RevisionSelection", "RestrictiveToolPolicy", "SSEServer", "SamplingPolicy", "SecretReference",
        "ServerBinding", "ServerDefinition", "ServerId", "ServerProfileId", "ServerProfileRef", "ServerValue", "SessionForkRequest", "SessionId", "SessionSource", "ExecutionReport", "EvidenceRef", "ReasoningState", "ReasoningVisibility", "RequestLink",
        "StdioServer", "HTTPServer", "TerminalPolicy", "TextContent", "TraceId", "TraceResult", "ToolPolicy", "TransportKind", "TrustLevel",
        "TurnId", "TurnStatus", "TurnOutcome", "TurnResponse", "TurnResult", "TurnState",
        "UserMessage", "WorkspaceKind", "WorkspacePolicy", "ListTools", "ListResources", "ListTemplates", "ListPrompts", "CallTool", "ReadResource", "GetPrompt", "Ping", "DirectResult", "ListToolsResult", "ListResourcesResult", "ListTemplatesResult", "ListPromptsResult", "CallToolResult", "ReadResourceResult", "GetPromptResult", "PingResult",
    ),
    "mcp_pal.errors": (
        "CleanupError", "ExecutionNotFound", "InvalidTransitionError", "KitClosed", "MCPError", "ModelValidationError", "OperationCancelled",
        "OperationTimeout", "RawEvidenceIntegrityError", "RawEvidenceUnavailable", "ProtocolError", "SessionBusy", "SessionStillOpen", "TransportError", "TraceNotFinalized", "TraceUnavailable", "UnsupportedFeature",
    ),
    "mcp_pal.sync_api": ("AgentSession", "HarnessAdapter", "Probes", "CallToolResult", "CompletionResult", "ConfigOrigin", "ConfigSource", "Config", "ConfigError", "DirectClient", "ExecutionHandle", "PromptInfo", "ResourceInfo", "TemplateInfo", "ToolInfo", "EmptyResult", "GetPromptResult", "InitializationResult", "InputRequiredResult", "ListPromptsResult", "ListResourcesResult", "ListResourceTemplatesResult", "ListToolsResult", "MCPTestKit", "ProbeEvidence", "ProbeKind", "ProbeReport", "ProbeRequest", "ProbeResult", "PromptResult", "ResourceReadResult", "ToolCallResult", "Tool", "Resource", "ResourceTemplate", "load_config", "AllowedCommands", "ElicitationRequest", "ElicitationResult", "ElicitationHandler", "FilesystemHandler", "FilesystemRequest", "FilesystemResult", "Interactions", "InteractionHandlers", "InteractionReceipt", "PermissionRequest", "PermissionResult", "PermissionHandler", "SamplingRequest", "SamplingResult", "SamplingHandler", "TerminalHandler", "TerminalRequest", "TerminalResult", "WorkspaceFiles"),
    "mcp_pal.async_api": ("AsyncAgentSession", "HarnessAdapter", "AsyncProbes", "AsyncDirectClient", "AsyncExecutionHandle", "AsyncMCPTestKit", "CallToolResult", "CompletionResult", "ConfigOrigin", "ConfigSource", "Config", "ConfigError", "PromptInfo", "ResourceInfo", "TemplateInfo", "ToolInfo", "EmptyResult", "GetPromptResult", "InitializeResult", "InitializationResult", "InputRequiredResult", "ListPromptsResult", "ListResourcesResult", "ListResourceTemplatesResult", "ListToolsResult", "ProbeEvidence", "ProbeKind", "ProbeReport", "ProbeRequest", "ProbeResult", "Prompt", "PromptPage", "PromptResult", "ReadResourceResult", "Resource", "ResourcePage", "ResourceReadResult", "ResourceTemplate", "ResourceTemplatePage", "ResourceTemplatesPage", "ResourcesPage", "Tool", "ToolCallResult", "ToolPage", "ToolsPage", "load_config", "AllowedCommands", "ElicitationRequest", "ElicitationResult", "ElicitationHandler", "FilesystemHandler", "FilesystemRequest", "FilesystemResult", "Interactions", "InteractionHandlers", "InteractionReceipt", "PermissionRequest", "PermissionResult", "PermissionHandler", "SamplingRequest", "SamplingResult", "SamplingHandler", "TerminalHandler", "TerminalRequest", "TerminalResult", "WorkspaceFiles"),
    "mcp_pal.matchers": ("CheckGroup", "Expectation", "check", "expect"),
    "mcp_pal.testing": ("ArtifactIntegrityError", "ExpectedCall", "FaultInjector", "Gate", "MockExpectationError", "MockMCPServer", "MockProtocolError", "RecordedArtifact", "RecordedInteraction", "Recording", "RedactionBinding", "ReplayMismatch", "ReplayServer", "VirtualClock"),
    "mcp_pal.evaluations": ("AsyncEvaluator", "EvaluationDecision", "EvaluationRunner", "EvaluationStore", "EvaluationVerdict", "Evaluator", "EvaluatorCallable", "EvaluatorRegistry", "EvaluatorRegistration", "InMemoryEvaluationStore", "RequiredEvaluationError", "register_builtin_evaluators"),
    "mcp_pal.snapshots": ("SnapshotOptions", "snapshot"),
    "mcp_pal.policy": ("ConfirmationHook", "ToolDescriptor", "ToolPolicyDecision", "ToolPolicyEvidence", "ToolPolicyEvaluator", "evaluate_tool_policy"),
    "mcp_pal.pytest_plugin": ("pytest_addoption", "pytest_configure", "pytest_unconfigure"),
    "mcp_pal.matrix": ("HarnessCase", "HarnessMatrix", "HarnessMatrixCase", "ServerCase", "ToolCase", "ToolMatrix", "ToolMatrixCase"),
}

# Phase 4 implementation modules deliberately remain internal.  They expose
# direct ``__all__`` values for package-internal composition, but are not part
# of the versioned SDK surface until their contracts are documented and
# promoted through PUBLIC_EXPORTS.
_INTERNAL_MODULES: tuple[str, ...] = (
    "mcp_pal.events",
    "mcp_pal.execution_trace",
    "mcp_pal.storage",
    "mcp_pal.trace.redaction",
)

__all__ = ["PUBLIC_EXPORTS"]

# R1 typed observability models are a separate value-model module but are
# promoted through each supported SDK boundary.  Keep this list derived from
# that module's explicit ``__all__`` so the manifest cannot drift.
from .observability import __all__ as _OBSERVABILITY_EXPORTS

PUBLIC_EXPORTS["mcp_pal.observability"] = tuple(_OBSERVABILITY_EXPORTS)
for _boundary in ("mcp_pal", "mcp_pal.sync_api", "mcp_pal.async_api"):
    PUBLIC_EXPORTS[_boundary] = PUBLIC_EXPORTS[_boundary] + tuple(_OBSERVABILITY_EXPORTS)
del _boundary
