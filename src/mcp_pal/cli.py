"""Compatibility entry point for the local MCP Pal command-line kit."""
from .harness.cli import main

__all__ = ["main"]

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
