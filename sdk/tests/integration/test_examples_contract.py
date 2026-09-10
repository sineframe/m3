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
    assert headings[2] == "## 3. Use a native harness with the local stdio server"
    assert "client.list_all_tools()" in text
    assert "example_mcp_server.py" in text
    assert all(name in text for name in ("ClaudeCode", "OpenCode", "Codex", "Pi"))
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


def test_live_math_matrix_example_remains_opt_in_and_outside_ci_catalog() -> None:
    example = _EXAMPLES / "nondeterministic" / "test_math_harness_matrix.py"
    assert example.exists()
    text = example.read_text(encoding="utf-8")

    assert (_EXAMPLES / "servers" / "math_mcp_server.py").exists()
    assert "pytest.mark.live" in text
    assert "MCP_PAL_RUN_LIVE_MATH_MATRIX" in text
    assert "FullToolPolicy(acknowledge_risk=True)" in text
    assert "_TRIALS_PER_CASE = 2" in text
    assert not (_EXAMPLES / "tests" / example.name).exists()
