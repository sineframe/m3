import sys
from pathlib import Path

import pytest

from m3 import StdioServer, expect

pytestmark = pytest.mark.m3(suite_name="shipping-agent")


def test_agent_evidence(agent) -> None:
    project_root = Path(__file__).parents[1]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(project_root / "shipping_server.py"),),
        cwd=str(project_root),
    )
    result = agent.run(
        "Quote a 2 kg parcel in the local zone.",
        server=server,
        permission_policy="allow",
    )

    expect(result).to_have_tool_call(
        "shipping_quote",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
    )
