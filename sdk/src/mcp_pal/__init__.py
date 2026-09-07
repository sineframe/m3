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
from .aggregations import (
    EvaluationGroup,
    EvaluationQuery,
    EvaluationReport,
    EvaluationStats,
    HealthStats,
    LatencyStats,
    ToolCallStats,
)
from .evaluations import (
    AsyncEvaluator,
    EvaluationDecision,
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
from .snapshots import SnapshotOptions, snapshot, snapshot
from .policy import (
    ToolDescriptor,
    ToolPolicyDecision,
    ToolPolicyEvidence,
    ToolPolicyEvaluator,
    evaluate_tool_policy,
)
from .interaction_handlers import (
    AllowedCommands,
    ElicitationRequest,
    ElicitationResult,
    ElicitationHandler,
    FilesystemHandler,
    FilesystemRequest,
    FilesystemResult,
    Interactions,
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
    WorkspaceFiles,
)
from .sync_api import (
    AgentSession,
    ExecutionHandle,
    Probes,
    ConfigOrigin,
    ConfigSource,
    Config,
    ConfigError,
    MCPTestKit,
    HarnessAdapter,
    ProbeEvidence,
    ProbeKind,
    ProbeReport,
    ProbeRequest,
    ProbeResult,
    load_config,
)
from .types import *
from .observability import *
from .services.acp_probes import (
    ACPAgentIdentity, ACPAgentMode, ACPProbeDimension, ACPProbeHistory, ACPProbeKind,
    ACPProbeRequest, ACPProbeResult, ACPProbeStatus, ACPProbeStore,
    redact_probe, run_acp_probe,
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

# Keep the runtime manifest authoritative while exposing the two stable
# examples to static analyzers that do not evaluate dynamic ``__all__`` values.
if _TYPE_CHECKING:
    __all__: list[str] = [
        "MCPTestKit",
        "AgentSession",
        "ExecutionHandle",
        "HarnessAdapter",
        "expect",
        "Probes",
        "ConfigOrigin",
        "ConfigSource",
        "Config",
        "ConfigError",
        "ProbeEvidence",
        "ProbeKind",
        "ProbeReport",
        "ProbeRequest",
        "ProbeResult",
        "AsyncEvaluator",
        "EvaluationDecision",
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
        "snapshot",
        "KitClosed",
        "load_config",
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
