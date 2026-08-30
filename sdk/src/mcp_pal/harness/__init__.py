"""Harness interfaces and supported CLI implementations."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .acp import ACPAdapter, ACPAgentAdapter, AcpHarnessAdapter
    from .base import AcpRunSpec, HarnessResult, HarnessRunner, RunSpec
    from .claude import ClaudeCodeHarnessAdapter, ClaudeCodeSession
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
        default_harness_adapter_registry,
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
            "default_harness_adapter_registry",
        )
    },
    **{name: ".acp" for name in ("ACPAdapter", "ACPAgentAdapter", "AcpHarnessAdapter")},
    **{
        name: ".base"
        for name in ("AcpRunSpec", "HarnessResult", "HarnessRunner", "RunSpec")
    },
    **{name: ".claude" for name in ("ClaudeCodeHarnessAdapter", "ClaudeCodeSession")},
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
    "AcpRunSpec", "HarnessResult", "HarnessRunner",
    "ClaudeCodeHarnessAdapter", "ClaudeCodeSession", "OpenCodeHarnessAdapter", "OpenCodeSession",
    "HarnessManifest", "ManifestValidationError", "RunSpec", "export_manifest", "load_manifest",
    "validate_manifest", "DeterministicHarnessAdapter", "HarnessAdapter", "HarnessAdapterContract", "HarnessAdapterCapabilities",
    "HarnessAdapterError", "HarnessCleanupError", "HarnessLaunch", "HarnessSession",
    "HarnessAdapterFactory", "HarnessAdapterRegistry", "HarnessSessionSnapshot", "HarnessStartupError", "HarnessTurnRequest", "HarnessTurnResult",
    "default_harness_adapter_registry", "UnsupportedHarnessFeature", "DeterministicACPAdapter", "FakeClaudeCodeAdapter", "FakeOpenCodeAdapter",
    "AcpHarnessAdapter", "ACPAdapter", "ACPAgentAdapter", "HarnessObservation", "HARNESS_OBSERVATION_ADAPTER", "HarnessObservationBase",
    "HarnessSessionEvidence", "TurnEvidence", "RawEvidenceInput", "RawFrameObservation", "MessageChunkObservation", "ReasoningChunkObservation",
    "ToolCallObservedObservation", "ToolResultObservedObservation", "UsageObservedObservation", "PlanObservedObservation", "StateObservedObservation",
    "InteractionObservedObservation", "ProcessObservedObservation", "MetadataObservedObservation", "HarnessObservationSink",
]
