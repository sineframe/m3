"""Harness interfaces and supported CLI implementations."""
from .base import AcpRunSpec, HarnessResult, HarnessRunner, RunSpec
from .manifest import ManifestValidationError, export_manifest, load_manifest, validate_manifest
from .claude_cli import ClaudeCodeRunner, READ_ONLY_TOOLS
from .opencode_cli import OpenCodeRunner

__all__ = ["AcpRunSpec", "ClaudeCodeRunner", "OpenCodeRunner", "HarnessResult", "HarnessRunner", "ManifestValidationError", "READ_ONLY_TOOLS", "RunSpec", "export_manifest", "load_manifest", "validate_manifest"]
