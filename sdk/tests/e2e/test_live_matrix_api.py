"""Opt-in live-provider coverage through the public agent selection API."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, expect
from m3.types import StdioServer

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.process_lifecycle]

_SDK_ROOT = Path(__file__).parents[2]
_REPOSITORY_ROOT = _SDK_ROOT.parent
_MCP_SERVER = _SDK_ROOT / "tests" / "fixtures" / "matrix_stdio_server.py"


def _server() -> StdioServer:
    return StdioServer(
        name="live-mcp",
        command=sys.executable,
        args=(str(_MCP_SERVER),),
        cwd=str(_REPOSITORY_ROOT),
    )


@pytest.mark.skipif(
    os.environ.get("M3_RUN_LIVE_OPENCODE") != "1",
    reason="set M3_RUN_LIVE_OPENCODE=1 to call OpenCode",
)
def test_live_opencode_matrix_case_uses_real_harness() -> None:
    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")
    if not os.environ.get("OPENCODE_API_KEY"):
        pytest.skip("OPENCODE_API_KEY is not available")
    model = os.environ.get("M3_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    server = _server()
    selection = {
        "harness": "opencode",
        "models": [model],
        "executable": executable,
        "credential_env": {"OPENCODE_API_KEY": "OPENCODE_API_KEY"},
    }
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        agent = kit.agents([selection])[0]
        result = agent.run(
            "Use the available MCP server to echo the text live-opencode.",
            server=server,
            timeout=120,
        )
    expect(result).to_have_tool_call("echo", server="live-mcp", status="success")


@pytest.mark.skipif(
    os.environ.get("M3_RUN_LIVE_CLAUDE") != "1",
    reason="set M3_RUN_LIVE_CLAUDE=1 to call Claude Code",
)
def test_live_claude_matrix_case_uses_real_harness() -> None:
    executable = shutil.which("claude")
    if executable is None:
        pytest.skip("Claude Code is not installed")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is not available")
    server = _server()
    selection = {
        "harness": "claude",
        "models": [os.environ.get("M3_LIVE_CLAUDE_MODEL", "sonnet")],
        "executable": executable,
        "credential_env": {"ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY"},
    }
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        agent = kit.agents([selection])[0]
        result = agent.run(
            "Use the available MCP server to echo the text live-claude.",
            server=server,
            timeout=120,
        )
    expect(result).to_have_tool_call("echo", server="live-mcp", status="success")
