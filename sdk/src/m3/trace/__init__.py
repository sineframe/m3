"""Claude trace capture, redaction, and post-run assembly."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .claude import build_claude_trace
    from .redaction import redact

__all__ = ["build_claude_trace", "redact"]

# Submodules load on first attribute access so ``python -m
# m3.transport.stdio_proxy`` does not import the Claude trace assembler.
_EXPORT_MODULES: dict[str, str] = {
    "build_claude_trace": ".claude",
    "redact": ".redaction",
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
