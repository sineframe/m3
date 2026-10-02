"""Harness versions whose elicitation and MRTR behaviour M3 has characterized.

These are the only Codex CLI and Pi releases the adapters enable action-bound
elicitation for. Bumping one means re-running the real-harness gates and
updating every copy that ``scripts/check_harness_versions.py --check`` lists.
"""

from __future__ import annotations

CODEX_VERIFIED_VERSION = "0.156.1"
PI_VERIFIED_VERSION = "0.85.1"
