"""M3 public value and type boundary.

Importing this package loads only the standard library.  Each public name is
imported from its defining module on first access (PEP 562), which loads
Pydantic value models and protocol/type declarations as needed.  Nothing here
initializes application settings, databases, transports, workers, or event
loops.
"""

import typing as _typing

if _typing.TYPE_CHECKING:
    from importlib.metadata import version as _distribution_version

    from . import types as _types_module
    from .aggregations import (
        EvaluationGroup as EvaluationGroup,
    )
    from .aggregations import (
        EvaluationQuery as EvaluationQuery,
    )
    from .aggregations import (
        EvaluationReport as EvaluationReport,
    )
    from .aggregations import (
        EvaluationStats as EvaluationStats,
    )
    from .aggregations import (
        HealthStats as HealthStats,
    )
    from .aggregations import (
        LatencyStats as LatencyStats,
    )
    from .aggregations import (
        ToolCallStats as ToolCallStats,
    )
    from .elicitation import (
        ElicitationPlan as ElicitationPlan,
    )
    from .elicitation import (
        ElicitationResponse as ElicitationResponse,
    )
    from .elicitation import (
        FormElicitationRequest as FormElicitationRequest,
    )
    from .elicitation import (
        PendingElicitationRound as PendingElicitationRound,
    )
    from .elicitation import (
        UrlElicitationRequest as UrlElicitationRequest,
    )
    from .elicitation import (
        expect_form as expect_form,
    )
    from .elicitation import (
        expect_url as expect_url,
    )
    from .elicitation import (
        maybe_form as maybe_form,
    )
    from .elicitation import (
        maybe_url as maybe_url,
    )
    from .elicitation import (
        one_of as one_of,
    )
    from .elicitation import (
        optional as optional,
    )
    from .elicitation import (
        round_of as round_of,
    )
    from .elicitation import (
        sequence as sequence,
    )
    from .errors import (
        CleanupError as CleanupError,
    )
    from .errors import (
        ElicitationExpectationError as ElicitationExpectationError,
    )
    from .errors import (
        ElicitationRoundLimitError as ElicitationRoundLimitError,
    )
    from .errors import (
        ExecutionNotFound as ExecutionNotFound,
    )
    from .errors import (
        InvalidTransitionError as InvalidTransitionError,
    )
    from .errors import (
        KitClosed as KitClosed,
    )
    from .errors import (
        MCPError as MCPError,
    )
    from .errors import (
        ModelValidationError as ModelValidationError,
    )
    from .errors import (
        OperationCancelled as OperationCancelled,
    )
    from .errors import (
        OperationTimeout as OperationTimeout,
    )
    from .errors import (
        ProtocolError as ProtocolError,
    )
    from .errors import (
        RawEvidenceIntegrityError as RawEvidenceIntegrityError,
    )
    from .errors import (
        RawEvidenceUnavailable as RawEvidenceUnavailable,
    )
    from .errors import (
        SessionBusy as SessionBusy,
    )
    from .errors import (
        SessionStillOpen as SessionStillOpen,
    )
    from .errors import (
        TraceNotFinalized as TraceNotFinalized,
    )
    from .errors import (
        TraceUnavailable as TraceUnavailable,
    )
    from .errors import (
        TransportError as TransportError,
    )
    from .errors import (
        UnsupportedFeature as UnsupportedFeature,
    )
    from .evaluations import (
        AsyncEvaluator as AsyncEvaluator,
    )
    from .evaluations import (
        EvaluationDecision as EvaluationDecision,
    )
    from .evaluations import (
        EvaluationRunner as EvaluationRunner,
    )
    from .evaluations import (
        EvaluationStore as EvaluationStore,
    )
    from .evaluations import (
        EvaluationVerdict as EvaluationVerdict,
    )
    from .evaluations import (
        Evaluator as Evaluator,
    )
    from .evaluations import (
        EvaluatorCallable as EvaluatorCallable,
    )
    from .evaluations import (
        EvaluatorRegistration as EvaluatorRegistration,
    )
    from .evaluations import (
        EvaluatorRegistry as EvaluatorRegistry,
    )
    from .evaluations import (
        InMemoryEvaluationStore as InMemoryEvaluationStore,
    )
    from .evaluations import (
        RequiredEvaluationError as RequiredEvaluationError,
    )
    from .feedback import (
        Comparison as Comparison,
    )
    from .feedback import (
        Feedback as Feedback,
    )
    from .feedback import (
        build_feedback as build_feedback,
    )
    from .feedback import (
        export_feedback as export_feedback,
    )
    from .interaction_handlers import (
        AllowedCommands as AllowedCommands,
    )
    from .interaction_handlers import (
        FilesystemHandler as FilesystemHandler,
    )
    from .interaction_handlers import (
        FilesystemRequest as FilesystemRequest,
    )
    from .interaction_handlers import (
        FilesystemResult as FilesystemResult,
    )
    from .interaction_handlers import (
        InteractionHandlers as InteractionHandlers,
    )
    from .interaction_handlers import (
        InteractionReceipt as InteractionReceipt,
    )
    from .interaction_handlers import (
        Interactions as Interactions,
    )
    from .interaction_handlers import (
        PermissionHandler as PermissionHandler,
    )
    from .interaction_handlers import (
        PermissionRequest as PermissionRequest,
    )
    from .interaction_handlers import (
        PermissionResult as PermissionResult,
    )
    from .interaction_handlers import (
        SamplingHandler as SamplingHandler,
    )
    from .interaction_handlers import (
        SamplingRequest as SamplingRequest,
    )
    from .interaction_handlers import (
        SamplingResult as SamplingResult,
    )
    from .interaction_handlers import (
        TerminalHandler as TerminalHandler,
    )
    from .interaction_handlers import (
        TerminalRequest as TerminalRequest,
    )
    from .interaction_handlers import (
        TerminalResult as TerminalResult,
    )
    from .interaction_handlers import (
        WorkspaceFiles as WorkspaceFiles,
    )
    from .matchers import check as check
    from .matchers import expect as expect
    from .matrix import (
        HarnessCase as HarnessCase,
    )
    from .matrix import (
        HarnessMatrix as HarnessMatrix,
    )
    from .matrix import (
        HarnessMatrixCase as HarnessMatrixCase,
    )
    from .matrix import (
        ServerCase as ServerCase,
    )
    from .matrix import (
        ToolCase as ToolCase,
    )
    from .matrix import (
        ToolMatrix as ToolMatrix,
    )
    from .matrix import (
        ToolMatrixCase as ToolMatrixCase,
    )
    from .observability import *  # noqa: F403 - module declares its public exports
    from .policy import (
        ToolDescriptor as ToolDescriptor,
    )
    from .policy import (
        ToolPolicyDecision as ToolPolicyDecision,
    )
    from .policy import (
        ToolPolicyEvaluator as ToolPolicyEvaluator,
    )
    from .policy import (
        ToolPolicyEvidence as ToolPolicyEvidence,
    )
    from .policy import (
        evaluate_tool_policy as evaluate_tool_policy,
    )
    from .services.acp_probes import (
        ACPAgentIdentity as ACPAgentIdentity,
    )
    from .services.acp_probes import (
        ACPAgentMode as ACPAgentMode,
    )
    from .services.acp_probes import (
        ACPProbeDimension as ACPProbeDimension,
    )
    from .services.acp_probes import (
        ACPProbeHistory as ACPProbeHistory,
    )
    from .services.acp_probes import (
        ACPProbeKind as ACPProbeKind,
    )
    from .services.acp_probes import (
        ACPProbeRequest as ACPProbeRequest,
    )
    from .services.acp_probes import (
        ACPProbeResult as ACPProbeResult,
    )
    from .services.acp_probes import (
        ACPProbeStatus as ACPProbeStatus,
    )
    from .services.acp_probes import (
        ACPProbeStore as ACPProbeStore,
    )
    from .services.acp_probes import (
        redact_probe as redact_probe,
    )
    from .services.acp_probes import (
        run_acp_probe as run_acp_probe,
    )
    from .snapshots import SnapshotOptions as SnapshotOptions
    from .snapshots import snapshot as snapshot
    from .sync_api import (
        AgentSession as AgentSession,
    )
    from .sync_api import (
        Config as Config,
    )
    from .sync_api import (
        ConfigError as ConfigError,
    )
    from .sync_api import (
        ConfigOrigin as ConfigOrigin,
    )
    from .sync_api import (
        ConfigSource as ConfigSource,
    )
    from .sync_api import (
        ExecutionHandle as ExecutionHandle,
    )
    from .sync_api import (
        HarnessAdapter as HarnessAdapter,
    )
    from .sync_api import (
        MCPTestKit as MCPTestKit,
    )
    from .sync_api import (
        ProbeEvidence as ProbeEvidence,
    )
    from .sync_api import (
        ProbeKind as ProbeKind,
    )
    from .sync_api import (
        ProbeReport as ProbeReport,
    )
    from .sync_api import (
        ProbeRequest as ProbeRequest,
    )
    from .sync_api import (
        ProbeResult as ProbeResult,
    )
    from .sync_api import (
        Probes as Probes,
    )
    from .sync_api import (
        load_config as load_config,
    )
    from .types import *  # noqa: F403 - module declares its public exports

    # Explicit legacy imports remain supported during the wildcard-surface
    # migration. They are intentionally absent from ``__all__``.
    from .types import (
        ACPAgent,  # noqa: F401
        ClaudeCode,  # noqa: F401
        Codex,  # noqa: F401
        EvaluationId,  # noqa: F401
        ExecutionSpec,  # noqa: F401
        FullToolPolicy,  # noqa: F401
        HarnessProfileRef,  # noqa: F401
        HarnessSpec,  # noqa: F401
        HarnessValue,  # noqa: F401
        NativeToolPolicy,  # noqa: F401
        OpenCode,  # noqa: F401
        Pi,  # noqa: F401
        RestrictiveToolPolicy,  # noqa: F401
    )

    for _compat_name in getattr(_types_module, "_COMPAT_EXPORTS", ()):
        if _compat_name not in globals() and hasattr(_types_module, _compat_name):
            globals()[_compat_name] = getattr(_types_module, _compat_name)
    del _compat_name, _types_module

    __version__ = _distribution_version("sf-m3")

    # Explicit manifest keeps the public re-exports intentional.
    from ._exports import PUBLIC_EXPORTS as _PUBLIC_EXPORTS

    __all__ = list(_PUBLIC_EXPORTS["m3"])
else:
    from importlib import import_module as _import_module

    # Literal copy of PUBLIC_EXPORTS["m3"]; computing it would import
    # m3.observability.  tests/unit/test_public_models.py guards drift.  The
    # F405 names are bound by the TYPE_CHECKING star import.
    __all__ = [  # noqa: RUF022 - public API order is compatibility-checked
        "__version__",
        "MCPTestKit",
        "StdioServer",  # noqa: F405
        "HTTPServer",  # noqa: F405
        "InProcessServer",  # noqa: F405
        "ExecutionResult",  # noqa: F405
        "ExecutionOutcome",  # noqa: F405
        "TurnOutcome",  # noqa: F405
        "expect",
        "check",
        "MCPError",
    ]

    # Name -> defining module for every name the TYPE_CHECKING imports bind.
    # tests/unit/test_lazy_imports.py keeps the two in sync.
    _LAZY_ATTRS = {
        "EvaluationGroup": ".aggregations",
        "EvaluationQuery": ".aggregations",
        "EvaluationReport": ".aggregations",
        "EvaluationStats": ".aggregations",
        "HealthStats": ".aggregations",
        "LatencyStats": ".aggregations",
        "ToolCallStats": ".aggregations",
        "ElicitationPlan": ".elicitation",
        "ElicitationResponse": ".elicitation",
        "FormElicitationRequest": ".elicitation",
        "PendingElicitationRound": ".elicitation",
        "UrlElicitationRequest": ".elicitation",
        "expect_form": ".elicitation",
        "expect_url": ".elicitation",
        "maybe_form": ".elicitation",
        "maybe_url": ".elicitation",
        "one_of": ".elicitation",
        "optional": ".elicitation",
        "round_of": ".elicitation",
        "sequence": ".elicitation",
        "CleanupError": ".errors",
        "ElicitationExpectationError": ".errors",
        "ElicitationRoundLimitError": ".errors",
        "ExecutionNotFound": ".errors",
        "InvalidTransitionError": ".errors",
        "KitClosed": ".errors",
        "MCPError": ".errors",
        "ModelValidationError": ".errors",
        "OperationCancelled": ".errors",
        "OperationTimeout": ".errors",
        "ProtocolError": ".errors",
        "RawEvidenceIntegrityError": ".errors",
        "RawEvidenceUnavailable": ".errors",
        "SessionBusy": ".errors",
        "SessionStillOpen": ".errors",
        "TraceNotFinalized": ".errors",
        "TraceUnavailable": ".errors",
        "TransportError": ".errors",
        "UnsupportedFeature": ".errors",
        "AsyncEvaluator": ".evaluations",
        "EvaluationDecision": ".types",
        "EvaluationRunner": ".evaluations",
        "EvaluationStore": ".evaluations",
        "EvaluationVerdict": ".evaluations",
        "Evaluator": ".evaluations",
        "EvaluatorCallable": ".evaluations",
        "EvaluatorRegistration": ".evaluations",
        "EvaluatorRegistry": ".evaluations",
        "InMemoryEvaluationStore": ".evaluations",
        "RequiredEvaluationError": ".evaluations",
        "Comparison": ".feedback",
        "Feedback": ".feedback",
        "build_feedback": ".feedback",
        "export_feedback": ".feedback",
        "AllowedCommands": ".interaction_handlers",
        "FilesystemHandler": ".interaction_handlers",
        "FilesystemRequest": ".interaction_handlers",
        "FilesystemResult": ".interaction_handlers",
        "InteractionHandlers": ".interaction_handlers",
        "InteractionReceipt": ".interaction_handlers",
        "Interactions": ".interaction_handlers",
        "PermissionHandler": ".interaction_handlers",
        "PermissionRequest": ".interaction_handlers",
        "PermissionResult": ".interaction_handlers",
        "SamplingHandler": ".interaction_handlers",
        "SamplingRequest": ".interaction_handlers",
        "SamplingResult": ".interaction_handlers",
        "TerminalHandler": ".interaction_handlers",
        "TerminalRequest": ".interaction_handlers",
        "TerminalResult": ".interaction_handlers",
        "WorkspaceFiles": ".interaction_handlers",
        "check": ".matchers",
        "expect": ".matchers",
        "HarnessCase": ".matrix",
        "HarnessMatrix": ".matrix",
        "HarnessMatrixCase": ".matrix",
        "ServerCase": ".matrix",
        "ToolCase": ".matrix",
        "ToolMatrix": ".matrix",
        "ToolMatrixCase": ".matrix",
        "ACPTrace": ".observability",
        "ArtifactEntry": ".observability",
        "CaptureOptions": ".observability",
        "ClaudeCodeTrace": ".observability",
        "CodexTrace": ".observability",
        "ConflictField": ".observability",
        "CorrelationState": ".observability",
        "DiagnosticEntry": ".observability",
        "DirectTrace": ".observability",
        "ElicitationEntry": ".observability",
        "EvaluationEntry": ".observability",
        "EvidenceCapture": ".observability",
        "HttpExchange": ".observability",
        "InitializationEntry": ".observability",
        "InitializationValue": ".observability",
        "InteractionEntry": ".observability",
        "LifecycleEntry": ".observability",
        "MessageEntry": ".observability",
        "MessageRole": ".observability",
        "Observation": ".observability",
        "ObservationReason": ".observability",
        "ObservationState": ".observability",
        "OpenCodeTrace": ".observability",
        "PiTrace": ".observability",
        "ProcessEntry": ".observability",
        "ProtocolCallAttempt": ".observability",
        "ProtocolEntry": ".observability",
        "ProtocolErrorInfo": ".observability",
        "ProtocolKind": ".observability",
        "ProviderEntry": ".observability",
        "RawEvidence": ".observability",
        "RawEvidenceSource": ".observability",
        "RawMessageEntry": ".observability",
        "ReasoningEntry": ".observability",
        "ReportedToolCall": ".observability",
        "RuntimeTraceInfo": ".observability",
        "SafeHttpHeader": ".observability",
        "TimingClock": ".observability",
        "ToolCallAttempt": ".observability",
        "ToolCallEntry": ".observability",
        "ToolCallStatus": ".observability",
        "ToolResult": ".observability",
        "TraceEntry": ".observability",
        "TraceEntryBase": ".observability",
        "TraceStatus": ".observability",
        "TraceSummary": ".observability",
        "TraceTiming": ".observability",
        "TraceView": ".observability",
        "TransportEntry": ".observability",
        "UsageEntry": ".observability",
        "UsageValue": ".observability",
        "WorkspaceEntry": ".observability",
        "ToolDescriptor": ".policy",
        "ToolPolicyDecision": ".policy",
        "ToolPolicyEvaluator": ".policy",
        "ToolPolicyEvidence": ".policy",
        "evaluate_tool_policy": ".policy",
        "ACPAgentIdentity": ".services.acp_probes",
        "ACPAgentMode": ".services.acp_probes",
        "ACPProbeDimension": ".services.acp_probes",
        "ACPProbeHistory": ".services.acp_probes",
        "ACPProbeKind": ".services.acp_probes",
        "ACPProbeRequest": ".services.acp_probes",
        "ACPProbeResult": ".services.acp_probes",
        "ACPProbeStatus": ".services.acp_probes",
        "ACPProbeStore": ".services.acp_probes",
        "redact_probe": ".services.acp_probes",
        "run_acp_probe": ".services.acp_probes",
        "SnapshotOptions": ".snapshots",
        "snapshot": ".snapshots",
        "AgentSession": ".sync_api",
        "Config": ".sync_api",
        "ConfigError": ".sync_api",
        "ConfigOrigin": ".sync_api",
        "ConfigSource": ".sync_api",
        "ExecutionHandle": ".sync_api",
        "HarnessAdapter": ".sync_api",
        "MCPTestKit": ".sync_api",
        "ProbeEvidence": ".sync_api",
        "ProbeKind": ".sync_api",
        "ProbeReport": ".sync_api",
        "ProbeRequest": ".sync_api",
        "ProbeResult": ".sync_api",
        "Probes": ".sync_api",
        "load_config": ".sync_api",
        "EVENT_SCHEMA_ID": ".types",
        "EVENT_SCHEMA_VERSION": ".types",
        "ExecutionOutcome": ".types",
        "ExecutionResult": ".types",
        "TurnOutcome": ".types",
        "TurnResult": ".types",
        "HTTPServer": ".types",
        "StdioServer": ".types",
        "InProcessServer": ".types",
        "HarnessTimeouts": ".types",
        "McpTimeouts": ".types",
        "CodexPermissions": ".types",
        "SecretReference": ".types",
        "ServerBinding": ".types",
        "ServerValue": ".types",
        "ToolPolicy": ".types",
        "UserMessage": ".types",
        "TextContent": ".types",
        "DirectSpec": ".types",
        "DirectOperation": ".types",
        "CallTool": ".types",
        "ListTools": ".types",
        "ListResources": ".types",
        "ListTemplates": ".types",
        "ListPrompts": ".types",
        "ReadResource": ".types",
        "GetPrompt": ".types",
        "Ping": ".types",
        "EvaluationResult": ".types",
        "EvaluationRecord": ".types",
        "EvaluationContext": ".types",
        "EvaluationStatus": ".types",
        "EvaluationSource": ".types",
        "JudgeEvidence": ".types",
        "TraceResult": ".types",
        "EvidenceRef": ".types",
        "ArtifactRef": ".types",
        "ExecutionReport": ".types",
        "ExecutionEvidence": ".types",
        "Capability": ".types",
        "Readiness": ".types",
        "ProtocolConstraint": ".types",
        "WorkspacePolicy": ".types",
        "PermissionPolicy": ".types",
        "SamplingPolicy": ".types",
        "FilesystemPolicy": ".types",
        "TerminalPolicy": ".types",
        "ACPAgent": ".types",
        "ClaudeCode": ".types",
        "Codex": ".types",
        "EvaluationId": ".types",
        "ExecutionSpec": ".types",
        "FullToolPolicy": ".types",
        "HarnessProfileRef": ".types",
        "HarnessSpec": ".types",
        "HarnessValue": ".types",
        "NativeToolPolicy": ".types",
        "OpenCode": ".types",
        "Pi": ".types",
        "RestrictiveToolPolicy": ".types",
        "ActivityHealth": ".types",
        "AgentIdentity": ".types",
        "ArtifactId": ".types",
        "ArtifactPolicy": ".types",
        "AudioContent": ".types",
        "Event": ".types",
        "CapabilityStatus": ".types",
        "ConnectionId": ".types",
        "ContentBlock": ".types",
        "ToolInfo": ".types",
        "ToolAnnotations": ".types",
        "ResourceInfo": ".types",
        "TemplateInfo": ".types",
        "PromptInfo": ".types",
        "ErrorCode": ".types",
        "ErrorInfo": ".types",
        "EventDirection": ".types",
        "EventKind": ".types",
        "EventOrigin": ".types",
        "PayloadRef": ".types",
        "EventSource": ".types",
        "EvaluationRegistration": ".types",
        "RunId": ".types",
        "SuiteId": ".types",
        "EventId": ".types",
        "ExecutionId": ".types",
        "ExecutionPage": ".types",
        "ExecutionState": ".types",
        "ProjectId": ".types",
        "FileContent": ".types",
        "FrozenModel": ".types",
        "HarnessId": ".types",
        "HarnessIdentity": ".types",
        "HarnessProfileId": ".types",
        "Identifier": ".types",
        "ImageContent": ".types",
        "ExecutionStatus": ".types",
        "JsonRpcId": ".types",
        "LifecyclePhase": ".types",
        "Metadata": ".types",
        "ModelIdentity": ".types",
        "OpaqueContent": ".types",
        "ResourceLink": ".types",
        "RevisionId": ".types",
        "RevisionSelection": ".types",
        "ServerDefinition": ".types",
        "ServerId": ".types",
        "ServerProfileId": ".types",
        "ServerProfileRef": ".types",
        "SessionForkRequest": ".types",
        "SessionId": ".types",
        "SessionSource": ".types",
        "ReasoningState": ".types",
        "ReasoningVisibility": ".types",
        "RequestLink": ".types",
        "TraceId": ".types",
        "TransportKind": ".types",
        "TrustLevel": ".types",
        "TurnId": ".types",
        "TurnStatus": ".types",
        "TurnResponse": ".types",
        "TurnState": ".types",
        "WorkspaceKind": ".types",
        "DirectResult": ".types",
        "ListToolsResult": ".types",
        "ListResourcesResult": ".types",
        "ListTemplatesResult": ".types",
        "ListPromptsResult": ".types",
        "CallToolResult": ".types",
        "ReadResourceResult": ".types",
        "GetPromptResult": ".types",
        "PingResult": ".types",
    }
    _SUBMODULES = frozenset(
        {
            "_check_recording",
            "_credentials",
            "_default_store",
            "_exports",
            "_judge_evidence",
            "_mrtr",
            "_startup_admission",
            "_test_runs",
            "_timing",
            "_types",
            "agent_session",
            "aggregations",
            "configuration",
            "direct_client",
            "direct_trace",
            "elicitation",
            "errors",
            "evaluations",
            "events",
            "execution_runtime",
            "execution_trace",
            "feedback",
            "harness",
            "interaction_handlers",
            "judges",
            "managed_input_api",
            "managed_runtime",
            "matchers",
            "matrix",
            "observability",
            "policy",
            "services",
            "snapshots",
            "storage",
            "suites",
            "sync_api",
            "trace",
            "transport",
            "types",
            "workspace",
        }
    )

    def __getattr__(name: str) -> object:
        if name == "__version__":
            from importlib.metadata import version

            value: object = version("sf-m3")
        elif name in _LAZY_ATTRS:
            value = getattr(_import_module(_LAZY_ATTRS[name], __name__), name)
        elif name in _SUBMODULES:
            return _import_module(f".{name}", __name__)
        else:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        globals()[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted(set(globals()) | set(_LAZY_ATTRS) | _SUBMODULES | {"__version__"})
