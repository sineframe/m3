"""MCP Pal public value and type boundary.

Importing this module loads only standard-library metadata, Pydantic value
models, and protocol/type declarations.  It does not initialize application
settings, databases, transports, workers, or event loops.
"""

from importlib.metadata import version as _distribution_version
from typing import TYPE_CHECKING as _TYPE_CHECKING

from .errors import (
    CleanupError,
    ExecutionNotFound,
    InvalidTransitionError,
    KitClosed,
    MCPError,
    ModelValidationError,
    OperationCancelled,
    OperationTimeout,
    RawEvidenceIntegrityError,
    RawEvidenceUnavailable,
    ProtocolError,
    SessionBusy,
    SessionStillOpen,
    TransportError,
    TraceNotFinalized,
    TraceUnavailable,
    UnsupportedFeature,
)
from .matchers import check, expect
from .evaluations import (
    AsyncEvaluator,
    EvaluationRunner,
    EvaluationStore,
    EvaluationVerdict,
    Evaluator,
    EvaluatorCallable,
    EvaluatorRegistry,
    EvaluatorRegistration,
    InMemoryEvaluationStore,
    RequiredEvaluationError,
)
from .snapshots import SnapshotOptions, canonical_snapshot, normalize_snapshot
from .policy import (
    ToolDescriptor,
    ToolPolicyDecision,
    ToolPolicyEvidence,
    ToolPolicyEvaluator,
    evaluate_tool_policy,
)
from .interaction_handlers import (
    AllowlistedTerminalHandler,
    ElicitationRequest,
    ElicitationResult,
    ElicitationHandler,
    FilesystemHandler,
    FilesystemRequest,
    FilesystemResult,
    InteractionController,
    InteractionHandlers,
    InteractionReceipt,
    PermissionRequest,
    PermissionResult,
    PermissionHandler,
    SamplingRequest,
    SamplingResult,
    SamplingHandler,
    TerminalHandler,
    TerminalRequest,
    TerminalResult,
    WorkspaceFilesystemHandler,
)
from .sync_api import (
    AgentSession,
    ExecutionHandle,
    CapabilityProbeService,
    ConfigOrigin,
    ConfigSource,
    Configuration,
    ConfigurationError,
    MCPConfig,
    MCPTestKit,
    HarnessAdapter,
    ProbeEvidence,
    ProbeKind,
    ProbeReport,
    ProbeRequest,
    ProbeResult,
    ProbeService,
    ReadinessProbeService,
    SDKConfig,
    load_config,
    resolve_config,
)
from .types import *
from .observability import *
from .services.acp_probes import (
    ACPAgentIdentity, ACPAgentMode, ACPProbeDimension, ACPProbeHistory, ACPProbeKind,
    ACPProbeRequest, ACPProbeResult, ACPProbeStatus, ACPProbeStore,
    redacted_probe, run_acp_probe,
)
from .matrix import (
    HarnessCase,
    HarnessMatrix,
    HarnessMatrixCase,
    ServerCase,
    ToolCase,
    ToolMatrix,
    ToolMatrixCase,
)

__version__ = _distribution_version("mcp-pal")

from ._exports import PUBLIC_EXPORTS as _PUBLIC_EXPORTS

# Keep the runtime manifest authoritative while exposing the two canonical
# examples to static analyzers that do not evaluate dynamic ``__all__`` values.
if _TYPE_CHECKING:
    __all__: list[str] = [
        "MCPTestKit",
        "AgentSession",
        "ExecutionHandle",
        "HarnessAdapter",
        "expect",
        "CapabilityProbeService",
        "ConfigOrigin",
        "ConfigSource",
        "Configuration",
        "ConfigurationError",
        "MCPConfig",
        "ProbeEvidence",
        "ProbeKind",
        "ProbeReport",
        "ProbeRequest",
        "ProbeResult",
        "ProbeService",
        "ReadinessProbeService",
        "AsyncEvaluator",
        "EvaluationRunner",
        "EvaluationStore",
        "EvaluationVerdict",
        "Evaluator",
        "EvaluatorCallable",
        "EvaluatorRegistry",
        "EvaluatorRegistration",
        "InMemoryEvaluationStore",
        "RequiredEvaluationError",
        "SnapshotOptions",
        "canonical_snapshot",
        "normalize_snapshot",
        "KitClosed",
        "SDKConfig",
        "load_config",
        "resolve_config",
        "ToolCase",
        "ServerCase",
        "HarnessCase",
        "ToolMatrix",
        "HarnessMatrix",
        "ToolMatrixCase",
        "HarnessMatrixCase",
    ]
else:
    __all__ = list(_PUBLIC_EXPORTS["mcp_pal"])
