"""Stable SDK exception types and machine-readable error envelopes."""

from __future__ import annotations

from typing import Any as _Any, Mapping as _Mapping


class MCPError(Exception):
    """Base class for expected MCP Pal failures."""

    code = "mcp_error"

    def __init__(self, message: str, *, details: _Mapping[str, _Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


class ModelValidationError(MCPError):
    code = "invalid_argument"


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


__all__ = [
    "CleanupError",
    "InvalidTransitionError",
    "KitClosed",
    "MCPError",
    "ModelValidationError",
    "OperationCancelled",
    "OperationTimeout",
    "ProtocolError",
    "SessionBusy",
    "SessionStillOpen",
    "TransportError",
    "UnsupportedFeature",
]
