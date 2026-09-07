"""Small regressions for the public, user-facing example catalog."""

from __future__ import annotations

from pathlib import Path

_SDK = Path(__file__).parents[2]
_EXAMPLES = _SDK / "examples"


def test_examples_docs_are_goal_oriented_and_not_a_synthetic_catalog() -> None:
    text = (_SDK / "docs" / "examples.md").read_text(encoding="utf-8")
    assert text.startswith("# Examples\n")
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings[0] == "## 1. Test a deployed MCP endpoint with Streamable HTTP"
    assert headings[1] == "## 2. Discover a direct local server tool before calling it"
    assert headings[2] == "## 3. Use Claude Code or OpenCode with the local stdio server"
    assert "client.list_all_tools()" in text
    assert "example_mcp_server.py" in text
    assert "ClaudeCode" in text and "OpenCode" in text
    assert "deterministic_acp_agent.py" in text
    assert "test_streamable_http.py" in text
    assert (_EXAMPLES / "nondeterministic" / "test_streamable_http.py").exists()
    assert "Verified examples" not in text
    assert "pass count" not in text.lower()
    assert "source of truth" not in text.lower()
    assert not (_EXAMPLES / "tests" / "test_tool_usage_assertions.py").exists()
    assert all(
        "Event" not in path.read_text(encoding="utf-8")
        for path in (_EXAMPLES / "tests").glob("*.py")
    )
