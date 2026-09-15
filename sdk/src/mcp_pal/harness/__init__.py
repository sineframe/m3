"""Harness interfaces and supported CLI implementations."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .acp import ACPAdapter, ACPAgentAdapter, AcpHarnessAdapter
    from .claude import ClaudeCodeHarnessAdapter, ClaudeCodeSession
    from .codex import CodexHarnessAdapter
    from .contracts import (
        DeterministicHarnessAdapter,
        HarnessAdapter,
        HarnessAdapterCapabilities,
        HarnessAdapterContract,
        HarnessAdapterError,
        HarnessAdapterFactory,
        HarnessAdapterRegistry,
        HarnessCleanupError,
        HarnessLaunch,
        HarnessSession,
        HarnessSessionSnapshot,
        HarnessStartupError,
        HarnessTurnRequest,
        HarnessTurnResult,
        UnsupportedHarnessFeature,
        default_adapters,
    )
    from .fakes import (
        DeterministicACPAdapter,
        FakeClaudeCodeAdapter,
        FakeOpenCodeAdapter,
    )
    from .manifest import (
        HarnessManifest,
        ManifestValidationError,
        export_manifest,
        load_manifest,
        validate_manifest,
    )
    from .opencode import OpenCodeHarnessAdapter, OpenCodeSession
    from .pi import PiHarnessAdapter

if TYPE_CHECKING:
    from .observation_sink import HarnessObservationSink
    from .observations import (
        HARNESS_OBSERVATION_ADAPTER,
        HarnessObservation,
        HarnessObservationBase,
        HarnessSessionEvidence,
        InteractionObservedObservation,
        MessageChunkObservation,
        MetadataObservedObservation,
        PlanObservedObservation,
        ProcessObservedObservation,
        RawEvidenceInput,
        RawFrameObservation,
        ReasoningChunkObservation,
        StateObservedObservation,
        ToolCallObservedObservation,
        ToolResultObservedObservation,
        TurnEvidence,
        UsageObservedObservation,
    )


_LAZY_MODULES = {
    **{
        name: ".contracts"
        for name in (
            "DeterministicHarnessAdapter",
            "HarnessAdapter",
            "HarnessAdapterCapabilities",
            "HarnessAdapterContract",
            "HarnessAdapterError",
            "HarnessAdapterFactory",
            "HarnessAdapterRegistry",
            "HarnessCleanupError",
            "HarnessLaunch",
            "HarnessSession",
            "HarnessSessionSnapshot",
            "HarnessStartupError",
            "HarnessTurnRequest",
            "HarnessTurnResult",
            "UnsupportedHarnessFeature",
            "default_adapters",
        )
    },
    **{name: ".acp" for name in ("ACPAdapter", "ACPAgentAdapter", "AcpHarnessAdapter")},
    **{name: ".claude" for name in ("ClaudeCodeHarnessAdapter", "ClaudeCodeSession")},
    **{name: ".codex" for name in ("CodexHarnessAdapter",)},
    **{
        name: ".fakes"
        for name in (
            "DeterministicACPAdapter",
            "FakeClaudeCodeAdapter",
            "FakeOpenCodeAdapter",
        )
    },
    **{
        name: ".manifest"
        for name in (
            "HarnessManifest",
            "ManifestValidationError",
            "export_manifest",
            "load_manifest",
            "validate_manifest",
        )
    },
    **{name: ".opencode" for name in ("OpenCodeHarnessAdapter", "OpenCodeSession")},
    **{name: ".pi" for name in ("PiHarnessAdapter",)},
    **{
        name: ".observations"
        for name in (
            "HARNESS_OBSERVATION_ADAPTER",
            "HarnessObservation",
            "HarnessObservationBase",
            "HarnessSessionEvidence",
            "InteractionObservedObservation",
            "MessageChunkObservation",
            "MetadataObservedObservation",
            "PlanObservedObservation",
            "ProcessObservedObservation",
            "RawEvidenceInput",
            "RawFrameObservation",
            "ReasoningChunkObservation",
            "StateObservedObservation",
            "ToolCallObservedObservation",
            "ToolResultObservedObservation",
            "TurnEvidence",
            "UsageObservedObservation",
        )
    },
    "HarnessObservationSink": ".observation_sink",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_MODULES.get(name)
    if module_name is not None:
        module = import_module(module_name, __name__)
        return getattr(module, name)
    raise AttributeError(name)


__all__ = [
    "HARNESS_OBSERVATION_ADAPTER",
    "ACPAdapter",
    "ACPAgentAdapter",
    "AcpHarnessAdapter",
    "ClaudeCodeHarnessAdapter",
    "ClaudeCodeSession",
    "CodexHarnessAdapter",
    "DeterministicACPAdapter",
    "DeterministicHarnessAdapter",
    "FakeClaudeCodeAdapter",
    "FakeOpenCodeAdapter",
    "HarnessAdapter",
    "HarnessAdapterCapabilities",
    "HarnessAdapterContract",
    "HarnessAdapterError",
    "HarnessAdapterFactory",
    "HarnessAdapterRegistry",
    "HarnessCleanupError",
    "HarnessLaunch",
    "HarnessManifest",
    "HarnessObservation",
    "HarnessObservationBase",
    "HarnessObservationSink",
    "HarnessSession",
    "HarnessSessionEvidence",
    "HarnessSessionSnapshot",
    "HarnessStartupError",
    "HarnessTurnRequest",
    "HarnessTurnResult",
    "InteractionObservedObservation",
    "ManifestValidationError",
    "MessageChunkObservation",
    "MetadataObservedObservation",
    "OpenCodeHarnessAdapter",
    "OpenCodeSession",
    "PiHarnessAdapter",
    "PlanObservedObservation",
    "ProcessObservedObservation",
    "RawEvidenceInput",
    "RawFrameObservation",
    "ReasoningChunkObservation",
    "StateObservedObservation",
    "ToolCallObservedObservation",
    "ToolResultObservedObservation",
    "TurnEvidence",
    "UnsupportedHarnessFeature",
    "UsageObservedObservation",
    "default_adapters",
    "export_manifest",
    "load_manifest",
    "validate_manifest",
]
