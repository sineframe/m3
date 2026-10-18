"""MCP transport instrumentation used by Claude harness runs."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .capture_proxy import (
        McpCaptureManager,
        McpCaptureSnapshot,
        McpObservation,
        McpObservationIncomplete,
        McpObservationSubscription,
        McpWireEvent,
    )
    from .http_proxy import McpHttpProxy, UnsafeUpstreamError

__all__ = [
    "McpCaptureManager",
    "McpCaptureSnapshot",
    "McpHttpProxy",
    "McpObservation",
    "McpObservationIncomplete",
    "McpObservationSubscription",
    "McpWireEvent",
    "UnsafeUpstreamError",
]

# Submodules load on first attribute access so ``python -m
# m3.transport.stdio_proxy`` does not import the capture manager or HTTP proxy.
_EXPORT_MODULES: dict[str, str] = {
    "McpCaptureManager": ".capture_proxy",
    "McpCaptureSnapshot": ".capture_proxy",
    "McpObservation": ".capture_proxy",
    "McpObservationIncomplete": ".capture_proxy",
    "McpObservationSubscription": ".capture_proxy",
    "McpWireEvent": ".capture_proxy",
    "McpHttpProxy": ".http_proxy",
    "UnsafeUpstreamError": ".http_proxy",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
