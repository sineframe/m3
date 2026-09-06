"""Checked manifest of the public SDK surface."""

PUBLIC_EXPORTS: dict[str, tuple[str, ...]] = {
    "mcp_pal": (
        "__version__", "MCPTestKit", "AgentSession", "ExecutionHandle", "HarnessAdapter", "CapabilityProbeService", "ConfigOrigin", "ConfigSource", "Configuration", "ConfigurationError", "MCPConfig", "ProbeEvidence", "ProbeKind", "ProbeReport", "ProbeRequest", "ProbeResult", "ProbeService", "ReadinessProbeService", "ACPAgentIdentity", "ACPAgentMode", "ACPProbeDimension", "ACPProbeHistory", "ACPProbeKind", "ACPProbeRequest", "ACPProbeResult", "ACPProbeStatus", "ACPProbeStore", "redacted_probe", "run_acp_probe", "SDKConfig", "load_config", "resolve_config",
        "EVENT_SCHEMA_ID", "EVENT_SCHEMA_VERSION", "CanonicalEventEnvelope", "EventDirection", "EventKind", "EventOrigin", "EventPayloadRef", "EventProvenance", "JsonRpcId", "LifecyclePhase", "RawEvidenceRef", "ReasoningState", "ReasoningVisibility", "RequestCorrelation", "EvaluationAggregateGroup", "EvaluationAggregateQuery", "EvaluationAggregateReport", "EvaluationAggregateValues", "EvaluationHealthSummary", "EvaluationLatencySummary", "EvaluationToolCallSummary",
        "ACPAgent", "ActivityHealth", "AgentExecutionSpec", "ArtifactId", "ArtifactPolicy",
        "ArtifactRef", "AudioContent", "BaseExecutionSpec", "CanonicalEvent", "Capability", "CapabilityStatus", "ClaudeCode",
        "ConnectionId", "ContentBlock", "DirectExecutionSpec", "DirectOperation", "DirectOperationBase", "DirectOperationResultBase", "DirectTool", "DirectResource", "DirectResourceTemplate", "DirectPrompt", "ErrorCode", "ErrorInfo", "EvaluationContext", "EvaluationDecision", "EvaluationProvenance", "PersistedEvaluationRecord", "RunId",
        "ElicitationPolicy", "EvaluationId", "EvaluationRegistration", "EvaluationResult", "EvaluationStatus", "EventId", "ExecutionEvidence", "ExecutionId",
        "ExecutionOutcome", "ExecutionPage", "ExecutionResult", "ExecutionSnapshot", "ExecutionSpec", "FullToolPolicy", "HarnessId",
        "HarnessProfileId", "HarnessProfileRef", "HarnessSpec", "HarnessValue", "Identifier",
        "InProcessServer", "FileContent", "FilesystemPolicy", "FrozenModel", "ImageContent", "LifecycleState", "Metadata", "NativeToolPolicy", "OpaqueContent", "OpenCode",
        "PermissionPolicy", "ProtocolConstraint", "Readiness", "ResourceLinkContent", "RevisionId", "RevisionSelection", "RestrictiveToolPolicy", "SSEServer",
        "SamplingPolicy", "SecretReference", "ServerBinding", "ServerDefinition", "ServerId",
        "ServerProfileId", "ServerProfileRef", "SessionForkRequest", "SessionId", "SessionProvenance", "PersistedExecutionReport", "StdioServer", "StreamableHTTPServer",
        "TerminalPolicy", "TextContent", "TraceId", "TraceResult", "TransportKind", "TurnId",
        "TurnLifecycle", "TurnOutcome", "TurnResponse", "TurnResult", "TurnSnapshot", "UserMessage",
        "WorkspaceKind", "WorkspacePolicy", "ListToolsOperation", "ListResourcesOperation", "ListResourceTemplatesOperation", "ListPromptsOperation", "CallToolOperation", "ReadResourceOperation", "GetPromptOperation", "PingOperation", "DirectOperationResult", "ListToolsOperationResult", "ListResourcesOperationResult", "ListResourceTemplatesOperationResult", "ListPromptsOperationResult", "CallToolOperationResult", "ReadResourceOperationResult", "GetPromptOperationResult", "PingOperationResult", "CleanupError", "ExecutionNotFound", "InvalidTransitionError", "KitClosed", "MCPError",
        "ModelValidationError", "OperationCancelled", "OperationTimeout", "ProtocolError", "SessionBusy",
        "SessionStillOpen", "ServerValue", "ToolPolicy", "TransportError", "TraceNotFinalized", "TraceUnavailable", "RawEvidenceUnavailable", "RawEvidenceIntegrityError", "TrustLevel", "UnsupportedFeature", "expect", "check",
        "ToolDescriptor", "ToolPolicyDecision", "ToolPolicyEvidence", "ToolPolicyEvaluator", "evaluate_tool_policy",
        "AsyncEvaluator", "EvaluationDecision", "EvaluationRunner", "EvaluationStore", "EvaluationVerdict", "Evaluator", "EvaluatorCallable", "EvaluatorRegistry", "EvaluatorRegistration", "InMemoryEvaluationStore", "RequiredEvaluationError", "SnapshotOptions", "canonical_snapshot", "normalize_snapshot",
        "AllowlistedTerminalHandler", "ElicitationRequest", "ElicitationResult", "ElicitationHandler", "FilesystemHandler", "FilesystemRequest", "FilesystemResult", "InteractionController", "InteractionHandlers", "InteractionReceipt", "PermissionRequest", "PermissionResult", "PermissionHandler", "SamplingRequest", "SamplingResult", "SamplingHandler", "TerminalHandler", "TerminalRequest", "TerminalResult", "WorkspaceFilesystemHandler",
        "ToolCase", "ServerCase", "HarnessCase", "ToolMatrix", "HarnessMatrix", "ToolMatrixCase", "HarnessMatrixCase",
    ),
        "mcp_pal.types": (
        "EVENT_SCHEMA_ID", "EVENT_SCHEMA_VERSION", "ACPAgent", "ActivityHealth", "AgentExecutionSpec", "ArtifactId", "ArtifactPolicy", "ArtifactRef",
        "AudioContent", "BaseExecutionSpec", "CanonicalEvent", "CanonicalEventEnvelope", "Capability", "CapabilityStatus", "ClaudeCode",
        "ConnectionId", "ContentBlock", "DirectExecutionSpec", "DirectOperation", "DirectOperationBase", "DirectOperationResultBase", "DirectTool", "DirectResource", "DirectResourceTemplate", "DirectPrompt", "ElicitationPolicy", "ErrorCode", "ErrorInfo",
        "EventDirection", "EventKind", "EventOrigin", "EventPayloadRef", "EventProvenance", "EvaluationContext", "EvaluationDecision", "EvaluationId", "EvaluationProvenance", "EvaluationRegistration", "EvaluationResult", "EvaluationStatus", "PersistedEvaluationRecord", "RunId",
        "EventId", "ExecutionId", "ExecutionEvidence", "ExecutionOutcome", "ExecutionPage", "ExecutionResult", "ExecutionSnapshot", "ExecutionSpec", "FileContent",
        "FilesystemPolicy", "FrozenModel", "FullToolPolicy", "HarnessId", "HarnessProfileId", "HarnessProfileRef",
        "HarnessSpec", "HarnessValue", "Identifier", "ImageContent", "InProcessServer", "LifecycleState",
        "JsonRpcId", "LifecyclePhase", "Metadata", "NativeToolPolicy", "OpaqueContent", "OpenCode", "PermissionPolicy", "ProtocolConstraint",
        "Readiness", "ResourceLinkContent", "RevisionId", "RevisionSelection", "RestrictiveToolPolicy", "SSEServer", "SamplingPolicy", "SecretReference",
        "ServerBinding", "ServerDefinition", "ServerId", "ServerProfileId", "ServerProfileRef", "ServerValue", "SessionForkRequest", "SessionId", "SessionProvenance", "PersistedExecutionReport", "RawEvidenceRef", "ReasoningState", "ReasoningVisibility", "RequestCorrelation",
        "StdioServer", "StreamableHTTPServer", "TerminalPolicy", "TextContent", "TraceId", "TraceResult", "ToolPolicy", "TransportKind", "TrustLevel",
        "TurnId", "TurnLifecycle", "TurnOutcome", "TurnResponse", "TurnResult", "TurnSnapshot",
        "UserMessage", "WorkspaceKind", "WorkspacePolicy", "ListToolsOperation", "ListResourcesOperation", "ListResourceTemplatesOperation", "ListPromptsOperation", "CallToolOperation", "ReadResourceOperation", "GetPromptOperation", "PingOperation", "DirectOperationResult", "ListToolsOperationResult", "ListResourcesOperationResult", "ListResourceTemplatesOperationResult", "ListPromptsOperationResult", "CallToolOperationResult", "ReadResourceOperationResult", "GetPromptOperationResult", "PingOperationResult",
    ),
    "mcp_pal.errors": (
        "CleanupError", "ExecutionNotFound", "InvalidTransitionError", "KitClosed", "MCPError", "ModelValidationError", "OperationCancelled",
        "OperationTimeout", "RawEvidenceIntegrityError", "RawEvidenceUnavailable", "ProtocolError", "SessionBusy", "SessionStillOpen", "TransportError", "TraceNotFinalized", "TraceUnavailable", "UnsupportedFeature",
    ),
    "mcp_pal.sync_api": ("AgentSession", "HarnessAdapter", "CapabilityProbeService", "CallToolResult", "CompletionResult", "ConfigOrigin", "ConfigSource", "Configuration", "ConfigurationError", "DirectClient", "ExecutionHandle", "DirectPrompt", "DirectResource", "DirectResourceTemplate", "DirectTool", "EmptyResult", "GetPromptResult", "InitializationResult", "InputRequiredResult", "ListPromptsResult", "ListResourcesResult", "ListResourceTemplatesResult", "ListToolsResult", "MCPConfig", "MCPTestKit", "ProbeEvidence", "ProbeKind", "ProbeReport", "ProbeRequest", "ProbeResult", "ProbeService", "ReadinessProbeService", "SDKConfig", "PromptResult", "ResourceReadResult", "ToolCallResult", "Tool", "Resource", "ResourceTemplate", "load_config", "resolve_config", "AllowlistedTerminalHandler", "ElicitationRequest", "ElicitationResult", "ElicitationHandler", "FilesystemHandler", "FilesystemRequest", "FilesystemResult", "InteractionController", "InteractionHandlers", "InteractionReceipt", "PermissionRequest", "PermissionResult", "PermissionHandler", "SamplingRequest", "SamplingResult", "SamplingHandler", "TerminalHandler", "TerminalRequest", "TerminalResult", "WorkspaceFilesystemHandler"),
    "mcp_pal.async_api": ("AsyncAgentSession", "HarnessAdapter", "AsyncCapabilityProbeService", "AsyncDirectClient", "AsyncExecutionHandle", "AsyncMCPTestKit", "CallToolResult", "CompletionResult", "ConfigOrigin", "ConfigSource", "Configuration", "ConfigurationError", "DirectPrompt", "DirectResource", "DirectResourceTemplate", "DirectTool", "EmptyResult", "GetPromptResult", "InitializeResult", "InitializationResult", "InputRequiredResult", "ListPromptsResult", "ListResourcesResult", "ListResourceTemplatesResult", "ListToolsResult", "MCPConfig", "ProbeEvidence", "ProbeKind", "ProbeReport", "ProbeRequest", "ProbeResult", "Prompt", "PromptPage", "PromptResult", "ReadResourceResult", "Resource", "ResourcePage", "ResourceReadResult", "ResourceTemplate", "ResourceTemplatePage", "ResourceTemplatesPage", "ResourcesPage", "SDKConfig", "Tool", "ToolCallResult", "ToolPage", "ToolsPage", "load_config", "resolve_config", "AllowlistedTerminalHandler", "ElicitationRequest", "ElicitationResult", "ElicitationHandler", "FilesystemHandler", "FilesystemRequest", "FilesystemResult", "InteractionController", "InteractionHandlers", "InteractionReceipt", "PermissionRequest", "PermissionResult", "PermissionHandler", "SamplingRequest", "SamplingResult", "SamplingHandler", "TerminalHandler", "TerminalRequest", "TerminalResult", "WorkspaceFilesystemHandler"),
    "mcp_pal.matchers": ("CheckGroup", "Expectation", "check", "expect"),
    "mcp_pal.testing": ("ArtifactIntegrityError", "ExpectedCall", "FaultInjector", "Gate", "MockExpectationError", "MockMCPServer", "MockProtocolError", "RecordedArtifact", "RecordedInteraction", "Recording", "RedactionBinding", "ReplayMismatch", "ReplayServer", "VirtualClock"),
    "mcp_pal.evaluations": ("AsyncEvaluator", "EvaluationDecision", "EvaluationRunner", "EvaluationStore", "EvaluationVerdict", "Evaluator", "EvaluatorCallable", "EvaluatorRegistry", "EvaluatorRegistration", "InMemoryEvaluationStore", "RequiredEvaluationError", "register_builtin_evaluators"),
    "mcp_pal.snapshots": ("SnapshotOptions", "canonical_snapshot", "normalize_snapshot"),
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
