"""Opt-in live-provider coverage through the public HarnessMatrix API."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from mcp_pal import MCPTestKit, expect
from mcp_pal.matrix import HarnessCase, HarnessMatrix, ServerCase, ToolCase
from mcp_pal.types import ClaudeCode, OpenCode, SecretReference, StdioServer

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.process_lifecycle]

_SDK_ROOT = Path(__file__).parents[2]
_REPOSITORY_ROOT = _SDK_ROOT.parent
_MCP_SERVER = _SDK_ROOT / "tests" / "fixtures" / "matrix_stdio_server.py"


def _server() -> ServerCase:
    return ServerCase(
        name="live-mcp",
        server=StdioServer(
            name="live-mcp",
            command=sys.executable,
            args=(str(_MCP_SERVER),),
            cwd=str(_REPOSITORY_ROOT),
        ),
        tools=(ToolCase(name="echo"),),
    )


@pytest.mark.skipif(
    os.environ.get("MCP_PAL_RUN_LIVE_OPENCODE") != "1",
    reason="set MCP_PAL_RUN_LIVE_OPENCODE=1 to call OpenCode",
)
def test_live_opencode_matrix_case_uses_real_harness() -> None:
    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")
    if not os.environ.get("OPENCODE_API_KEY"):
        pytest.skip("OPENCODE_API_KEY is not available")
    model = os.environ.get("MCP_PAL_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    case = HarnessMatrix.each_server(
        servers=(_server(),),
        harnesses=(
            HarnessCase(
                name="opencode",
                harness=OpenCode(
                    model=model,
                    executable=executable,
                    credential_references={
                        "OPENCODE_API_KEY": SecretReference(
                            source="environment", name="OPENCODE_API_KEY"
                        )
                    },
                ),
            ),
        ),
    ).cases()[0]
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = case.run(
            "Use the available MCP server to echo the text live-opencode.",
            kit=kit,
            timeout=120,
        )
    expect(result).to_have_tool_call("echo", server="live-mcp", status="success")


@pytest.mark.skipif(
    os.environ.get("MCP_PAL_RUN_LIVE_CLAUDE") != "1",
    reason="set MCP_PAL_RUN_LIVE_CLAUDE=1 to call Claude Code",
)
def test_live_claude_matrix_case_uses_real_harness() -> None:
    executable = shutil.which("claude")
    if executable is None:
        pytest.skip("Claude Code is not installed")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is not available")
    case = HarnessMatrix.each_server(
        servers=(_server(),),
        harnesses=(
            HarnessCase(
                name="claude",
                harness=ClaudeCode(
                    model=os.environ.get("MCP_PAL_LIVE_CLAUDE_MODEL", "sonnet"),
                    executable=executable,
                    credential_references={
                        "ANTHROPIC_API_KEY": SecretReference(
                            source="environment", name="ANTHROPIC_API_KEY"
                        )
                    },
                ),
            ),
        ),
    ).cases()[0]
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        result = case.run(
            "Use the available MCP server to echo the text live-claude.",
            kit=kit,
            timeout=120,
        )
    expect(result).to_have_tool_call("echo", server="live-mcp", status="success")
