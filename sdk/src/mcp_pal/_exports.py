"""Checked manifest of the public SDK surface."""

PUBLIC_EXPORTS: dict[str, tuple[str, ...]] = {
    "mcp_pal": (
        "__version__", "MCPTestKit", "AgentSession", "ExecutionHandle", "HarnessAdapter", "CapabilityProbeService", "ConfigOrigin", "ConfigSource", "Configuration", "ConfigurationError", "MCPConfig", "ProbeEvidence", "ProbeKind", "ProbeReport", "ProbeRequest", "ProbeResult", "ProbeService", "ReadinessProbeService", "SDKConfig", "load_config", "resolve_config",
        "EVENT_SCHEMA_ID", "EVENT_SCHEMA_VERSION", "CanonicalEventEnvelope", "EventDirection", "EventKind", "EventOrigin", "EventPayloadRef", "EventProvenance", "JsonRpcId", "LifecyclePhase", "RawEvidenceRef", "ReasoningState", "ReasoningVisibility", "RequestCorrelation",
        "ACPAgent", "ActivityHealth", "AgentExecutionSpec", "ArtifactId", "ArtifactPolicy",
        "ArtifactRef", "AudioContent", "BaseExecutionSpec", "CanonicalEvent", "Capability", "CapabilityStatus", "ClaudeCode",
        "ConnectionId", "ContentBlock", "DirectExecutionSpec", "ErrorCode", "ErrorInfo", "EvaluationContext",
        "ElicitationPolicy", "EvaluationId", "EvaluationRegistration", "EvaluationResult", "EvaluationStatus", "EventId", "ExecutionId",
        "ExecutionOutcome", "ExecutionResult", "ExecutionSnapshot", "ExecutionSpec", "FullToolPolicy", "HarnessId",
        "HarnessProfileId", "HarnessProfileRef", "HarnessSpec", "HarnessValue", "Identifier",
        "InProcessServer", "FileContent", "FilesystemPolicy", "FrozenModel", "ImageContent", "LifecycleState", "Metadata", "NativeToolPolicy", "OpaqueContent", "OpenCode",
        "PermissionPolicy", "ProtocolConstraint", "Readiness", "ResourceLinkContent", "RevisionId", "RevisionSelection", "RestrictiveToolPolicy", "SSEServer",
        "SamplingPolicy", "SecretReference", "ServerBinding", "ServerDefinition", "ServerId",
        "ServerProfileId", "ServerProfileRef", "SessionForkRequest", "SessionId", "SessionProvenance", "StdioServer", "StreamableHTTPServer",
        "TerminalPolicy", "TextContent", "TraceId", "TraceResult", "TransportKind", "TurnId",
        "TurnLifecycle", "TurnOutcome", "TurnResponse", "TurnResult", "TurnSnapshot", "UserMessage",
        "WorkspaceKind", "WorkspacePolicy", "CleanupError", "InvalidTransitionError", "KitClosed", "MCPError",
        "ModelValidationError", "OperationCancelled", "OperationTimeout", "ProtocolError", "SessionBusy",
        "SessionStillOpen", "ServerValue", "ToolPolicy", "TransportError", "TrustLevel", "UnsupportedFeature", "expect", "check",
        "ToolDescriptor", "ToolPolicyDecision", "ToolPolicyEvidence", "ToolPolicyEvaluator", "evaluate_tool_policy",
        "AsyncEvaluator", "EvaluationRunner", "EvaluationStore", "EvaluationVerdict", "Evaluator", "EvaluatorCallable", "EvaluatorRegistry", "EvaluatorRegistration", "InMemoryEvaluationStore", "RequiredEvaluationError", "SnapshotOptions", "canonical_snapshot", "normalize_snapshot",
        "AllowlistedTerminalHandler", "ElicitationRequest", "ElicitationResult", "ElicitationHandler", "FilesystemHandler", "FilesystemRequest", "FilesystemResult", "InteractionController", "InteractionHandlers", "InteractionReceipt", "PermissionRequest", "PermissionResult", "PermissionHandler", "SamplingRequest", "SamplingResult", "SamplingHandler", "TerminalHandler", "TerminalRequest", "TerminalResult", "WorkspaceFilesystemHandler",
    ),
        "mcp_pal.types": (
        "EVENT_SCHEMA_ID", "EVENT_SCHEMA_VERSION", "ACPAgent", "ActivityHealth", "AgentExecutionSpec", "ArtifactId", "ArtifactPolicy", "ArtifactRef",
        "AudioContent", "BaseExecutionSpec", "CanonicalEvent", "CanonicalEventEnvelope", "Capability", "CapabilityStatus", "ClaudeCode",
        "ConnectionId", "ContentBlock", "DirectExecutionSpec", "ElicitationPolicy", "ErrorCode", "ErrorInfo",
        "EventDirection", "EventKind", "EventOrigin", "EventPayloadRef", "EventProvenance", "EvaluationContext", "EvaluationId", "EvaluationRegistration", "EvaluationResult", "EvaluationStatus",
        "EventId", "ExecutionId", "ExecutionOutcome", "ExecutionResult", "ExecutionSnapshot", "ExecutionSpec", "FileContent",
        "FilesystemPolicy", "FrozenModel", "FullToolPolicy", "HarnessId", "HarnessProfileId", "HarnessProfileRef",
        "HarnessSpec", "HarnessValue", "Identifier", "ImageContent", "InProcessServer", "LifecycleState",
        "JsonRpcId", "LifecyclePhase", "Metadata", "NativeToolPolicy", "OpaqueContent", "OpenCode", "PermissionPolicy", "ProtocolConstraint",
        "Readiness", "ResourceLinkContent", "RevisionId", "RevisionSelection", "RestrictiveToolPolicy", "SSEServer", "SamplingPolicy", "SecretReference",
        "ServerBinding", "ServerDefinition", "ServerId", "ServerProfileId", "ServerProfileRef", "ServerValue", "SessionForkRequest", "SessionId", "SessionProvenance", "RawEvidenceRef", "ReasoningState", "ReasoningVisibility", "RequestCorrelation",
        "StdioServer", "StreamableHTTPServer", "TerminalPolicy", "TextContent", "TraceId", "TraceResult", "ToolPolicy", "TransportKind", "TrustLevel",
        "TurnId", "TurnLifecycle", "TurnOutcome", "TurnResponse", "TurnResult", "TurnSnapshot",
        "UserMessage", "WorkspaceKind", "WorkspacePolicy",
    ),
    "mcp_pal.errors": (
        "CleanupError", "InvalidTransitionError", "KitClosed", "MCPError", "ModelValidationError", "OperationCancelled",
        "OperationTimeout", "ProtocolError", "SessionBusy", "SessionStillOpen", "TransportError", "UnsupportedFeature",
    ),
    "mcp_pal.sync_api": ("AgentSession", "HarnessAdapter", "CapabilityProbeService", "CallToolResult", "CompletionResult", "ConfigOrigin", "ConfigSource", "Configuration", "ConfigurationError", "DirectClient", "ExecutionHandle", "DirectPrompt", "DirectResource", "DirectResourceTemplate", "EmptyResult", "GetPromptResult", "InitializationResult", "InputRequiredResult", "ListPromptsResult", "ListResourcesResult", "ListResourceTemplatesResult", "ListToolsResult", "MCPConfig", "MCPTestKit", "ProbeEvidence", "ProbeKind", "ProbeReport", "ProbeRequest", "ProbeResult", "ProbeService", "ReadinessProbeService", "SDKConfig", "PromptResult", "ResourceReadResult", "ToolCallResult", "Tool", "Resource", "ResourceTemplate", "load_config", "resolve_config", "AllowlistedTerminalHandler", "ElicitationRequest", "ElicitationResult", "ElicitationHandler", "FilesystemHandler", "FilesystemRequest", "FilesystemResult", "InteractionController", "InteractionHandlers", "InteractionReceipt", "PermissionRequest", "PermissionResult", "PermissionHandler", "SamplingRequest", "SamplingResult", "SamplingHandler", "TerminalHandler", "TerminalRequest", "TerminalResult", "WorkspaceFilesystemHandler"),
    "mcp_pal.async_api": ("AsyncAgentSession", "HarnessAdapter", "AsyncCapabilityProbeService", "AsyncDirectClient", "AsyncExecutionHandle", "AsyncMCPTestKit", "CallToolResult", "CompletionResult", "ConfigOrigin", "ConfigSource", "Configuration", "ConfigurationError", "DirectPrompt", "DirectResource", "DirectResourceTemplate", "DirectTool", "EmptyResult", "GetPromptResult", "InitializeResult", "InitializationResult", "InputRequiredResult", "ListPromptsResult", "ListResourcesResult", "ListResourceTemplatesResult", "ListToolsResult", "MCPConfig", "ProbeEvidence", "ProbeKind", "ProbeReport", "ProbeRequest", "ProbeResult", "Prompt", "PromptPage", "PromptResult", "ReadResourceResult", "Resource", "ResourcePage", "ResourceReadResult", "ResourceTemplate", "ResourceTemplatePage", "ResourceTemplatesPage", "ResourcesPage", "SDKConfig", "Tool", "ToolCallResult", "ToolPage", "ToolsPage", "load_config", "resolve_config", "AllowlistedTerminalHandler", "ElicitationRequest", "ElicitationResult", "ElicitationHandler", "FilesystemHandler", "FilesystemRequest", "FilesystemResult", "InteractionController", "InteractionHandlers", "InteractionReceipt", "PermissionRequest", "PermissionResult", "PermissionHandler", "SamplingRequest", "SamplingResult", "SamplingHandler", "TerminalHandler", "TerminalRequest", "TerminalResult", "WorkspaceFilesystemHandler"),
    "mcp_pal.matchers": ("CheckGroup", "Expectation", "check", "expect"),
    "mcp_pal.testing": ("ArtifactIntegrityError", "ExpectedCall", "FaultInjector", "Gate", "MockExpectationError", "MockMCPServer", "MockProtocolError", "RecordedArtifact", "RecordedInteraction", "Recording", "RedactionBinding", "ReplayMismatch", "ReplayServer", "VirtualClock"),
    "mcp_pal.evaluations": ("AsyncEvaluator", "EvaluationRunner", "EvaluationStore", "EvaluationVerdict", "Evaluator", "EvaluatorCallable", "EvaluatorRegistry", "EvaluatorRegistration", "InMemoryEvaluationStore", "RequiredEvaluationError"),
    "mcp_pal.snapshots": ("SnapshotOptions", "canonical_snapshot", "normalize_snapshot"),
    "mcp_pal.policy": ("ConfirmationHook", "ToolDescriptor", "ToolPolicyDecision", "ToolPolicyEvidence", "ToolPolicyEvaluator", "evaluate_tool_policy"),
    "mcp_pal.pytest_plugin": (),
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
