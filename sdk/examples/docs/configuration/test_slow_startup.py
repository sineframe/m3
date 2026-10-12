import sys
from pathlib import Path

import pytest

from m3.types import HarnessTimeouts, McpTimeouts, StdioServer

pytestmark = pytest.mark.m3(suite_name="slow-startup")


def test_slow_startup(agent):
    server = StdioServer(
        name="slow",
        command=sys.executable,
        args=[str(Path(__file__).with_name("slow_mcp.py"))],
        environment={"DELAY_S": "40"},
        timeouts=McpTimeouts(startup_seconds=75),
    )
    with agent.session(
        servers=[server],
        harness_timeouts=HarnessTimeouts(total_seconds=120, startup_seconds=90),
    ) as session:
        turn = session.send("Call lookup and report the code.", timeout=120)
        assert turn.error is None
