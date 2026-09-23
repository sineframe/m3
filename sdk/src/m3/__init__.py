"""M3 public value and type boundary.

Importing this module loads only standard-library metadata, Pydantic value
models, and protocol/type declarations.  It does not initialize application
settings, databases, transports, workers, or event loops.
"""

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
