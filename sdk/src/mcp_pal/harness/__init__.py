"""Harness interfaces and supported CLI implementations."""
from .base import AcpRunSpec, HarnessResult, HarnessRunner, RunSpec
from .manifest import ManifestValidationError, export_manifest, load_manifest, validate_manifest
from .claude_cli import ClaudeCodeRunner, READ_ONLY_TOOLS
from .opencode_cli import OpenCodeRunner
from .claude import ClaudeCodeHarnessAdapter, ClaudeCodeSession
from .opencode import OpenCodeHarnessAdapter, OpenCodeSession
from .contracts import (
    DeterministicHarnessAdapter,
    HarnessAdapter,
    HarnessAdapterContract,
    HarnessAdapterCapabilities,
    HarnessAdapterFactory,
    HarnessAdapterRegistry,
    HarnessAdapterError,
    HarnessCleanupError,
    HarnessLaunch,
    HarnessSession,
    HarnessSessionSnapshot,
    HarnessStartupError,
    HarnessTurnRequest,
    HarnessTurnResult,
    default_harness_adapter_registry,
    UnsupportedHarnessFeature,
)
from .fakes import DeterministicACPAdapter, FakeClaudeCodeAdapter, FakeOpenCodeAdapter
from .acp import ACPAdapter, ACPAgentAdapter, AcpHarnessAdapter

__all__ = [
    "AcpRunSpec", "ClaudeCodeRunner", "OpenCodeRunner", "HarnessResult", "HarnessRunner",
    "ClaudeCodeHarnessAdapter", "ClaudeCodeSession", "OpenCodeHarnessAdapter", "OpenCodeSession",
    "ManifestValidationError", "READ_ONLY_TOOLS", "RunSpec", "export_manifest", "load_manifest",
    "validate_manifest", "DeterministicHarnessAdapter", "HarnessAdapter", "HarnessAdapterContract", "HarnessAdapterCapabilities",
    "HarnessAdapterError", "HarnessCleanupError", "HarnessLaunch", "HarnessSession",
    "HarnessAdapterFactory", "HarnessAdapterRegistry",
    "HarnessSessionSnapshot", "HarnessStartupError", "HarnessTurnRequest", "HarnessTurnResult",
    "default_harness_adapter_registry", "UnsupportedHarnessFeature",
    "DeterministicACPAdapter", "FakeClaudeCodeAdapter", "FakeOpenCodeAdapter",
    "AcpHarnessAdapter", "ACPAdapter", "ACPAgentAdapter",
]
