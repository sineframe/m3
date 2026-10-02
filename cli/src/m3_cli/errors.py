"""Safe errors shared by command-line subcommands."""

from __future__ import annotations


class CLIError(ValueError):
    """A user-facing CLI error whose message contains no untrusted values."""


class UploadError(RuntimeError):
    """A report-upload failure whose message contains no untrusted values.

    Messages combine fixed text with validated run/execution IDs and integers.
    ``retryable`` is true when ``m3 upload`` can succeed later without rerunning
    tests; ``status`` and ``code`` describe the last server response, if any.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.status = status
        self.code = code


__all__ = ["CLIError", "UploadError"]
