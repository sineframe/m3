"""Safe errors shared by command-line subcommands."""

from __future__ import annotations


class CLIError(ValueError):
    """A user-facing CLI error whose message contains no untrusted values."""


__all__ = ["CLIError"]
