"""Internal helpers for reserved credential environment names."""

from __future__ import annotations

RESERVED_CREDENTIAL_ENV = "M3_ACCESS_TOKEN"


def validate_credential_environment_names(target: str, source: str) -> None:
    """Reject the upload token name in supported agent and judge mappings."""
    if target == RESERVED_CREDENTIAL_ENV or source == RESERVED_CREDENTIAL_ENV:
        raise ValueError("M3_ACCESS_TOKEN cannot be mapped to a test credential")
