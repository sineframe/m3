"""Checks on raw pytest passthrough arguments (standard library only)."""

from __future__ import annotations

from collections.abc import Sequence

RESERVED_PYTEST_OPTIONS = frozenset(
    {
        "--results-db",
        "--project-root",
        "--credential-env",
        "--m3-server-selections",
        "--m3-ci",
        "--m3-run-id",
        "--m3-timings-owner",
        "--m3-ci-metadata",
    }
)


def passthrough_option_error(args: Sequence[str]) -> str | None:
    """Keep CLI-owned run state out of raw pytest passthrough arguments."""
    for arg in args:
        if arg.startswith("@"):
            return "pytest response files are not supported in m3 passthrough"
        option = arg.split("=", 1)[0]
        if option in RESERVED_PYTEST_OPTIONS:
            return f"{option} must be set through m3, not pytest passthrough"
    return None
