"""Stable SDK exception types and machine-readable error envelopes."""

from __future__ import annotations

from collections.abc import Mapping as _Mapping
from typing import Any as _Any


class MCPError(Exception):
    """Base class for expected M3 failures."""

    code = "mcp_error"

    def __init__(
        self, message: str, *, details: _Mapping[str, _Any] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


class ModelValidationError(MCPError):
    code = "invalid_argument"


class ElicitationExpectationError(MCPError):
    """An elicitation round did not match the declared response plan."""

    code = "elicitation_expectation_failed"


class ElicitationRoundLimitError(MCPError):
    """An elicitation operation exceeded its configured round limit."""

    code = "elicitation_round_limit"
    terminal = True


class ManagedInputError(MCPError):
    """Base class for durable human-input lifecycle failures."""

    code = "managed_input_error"


class ManagedInputConflict(ManagedInputError):
    """A managed-input write lost an ownership or idempotency race."""

    code = "managed_input_conflict"


class ManagedInputValidationError(ManagedInputError):
    """A managed-input round or response map is invalid."""

    code = "managed_input_invalid"


class ManagedInputStateError(ManagedInputError):
    """A managed-input lifecycle transition is not allowed."""

    code = "managed_input_state"


class ManagedInputRecoveryError(ManagedInputError):
    """The persisted interaction cannot be resumed safely after worker loss."""

    code = "managed_input_recovery_unavailable"


class InvalidTransitionError(MCPError):
    code = "invalid_transition"


class ProtocolError(MCPError):
    code = "protocol_error"


class TransportError(MCPError):
    code = "transport_error"


class OperationTimeout(MCPError):
    code = "timeout"


class OperationCancelled(MCPError):
    code = "cancelled"


class SessionStillOpen(MCPError):
    code = "session_still_open"


class SessionBusy(MCPError):
    code = "session_busy"


class UnsupportedFeature(MCPError):
    code = "unsupported"


class KitClosed(MCPError):
    """An operation was attempted after its owning test kit was closed."""

    code = "kit_closed"


class CleanupError(MCPError):
    code = "cleanup_failed"


class ExecutionNotFound(MCPError):
    """The requested execution does not exist in the trace store."""

    code = "execution_not_found"


class TraceUnavailable(MCPError):
    """An execution exists but has no usable trace evidence."""

    code = "trace_unavailable"


class TraceNotFinalized(MCPError):
    """A typed view was requested before terminal execution evidence arrived."""

    code = "trace_not_finalized"


class RawEvidenceUnavailable(MCPError):
    """Referenced redacted raw evidence cannot be read."""

    code = "raw_evidence_unavailable"


class RawEvidenceIntegrityError(MCPError):
    """Referenced raw evidence failed its digest or size integrity check."""

    code = "raw_evidence_integrity_error"


__all__ = [
    "CleanupError",
    "ElicitationExpectationError",
    "ElicitationRoundLimitError",
    "ExecutionNotFound",
    "InvalidTransitionError",
    "KitClosed",
    "MCPError",
    "ManagedInputConflict",
    "ManagedInputError",
    "ManagedInputRecoveryError",
    "ManagedInputStateError",
    "ManagedInputValidationError",
    "ModelValidationError",
    "OperationCancelled",
    "OperationTimeout",
    "ProtocolError",
    "RawEvidenceIntegrityError",
    "RawEvidenceUnavailable",
    "SessionBusy",
    "SessionStillOpen",
    "TraceNotFinalized",
    "TraceUnavailable",
    "TransportError",
    "UnsupportedFeature",
]
