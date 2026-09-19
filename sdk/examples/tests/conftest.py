"""Shared pytest fixtures for the executable SDK examples."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from m3 import StdioServer

EXAMPLES_ROOT = Path(__file__).parents[1]
SERVER_SCRIPT = EXAMPLES_ROOT / "servers" / "example_mcp_server.py"


@pytest.fixture
def example_server() -> StdioServer:
    """Describe the real MCP subprocess used by each example test."""

    return StdioServer(
        name="example-mcp",
        command=sys.executable,
        args=(str(SERVER_SCRIPT),),
        cwd=str(EXAMPLES_ROOT),
    )
