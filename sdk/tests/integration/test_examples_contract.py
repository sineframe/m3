"""Small regressions for the public, user-facing example catalog."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_SDK = Path(__file__).parents[2]
_EXAMPLES = _SDK / "examples"
_REPO = _SDK.parent


def test_examples_docs_are_goal_oriented_and_not_a_synthetic_catalog() -> None:
    text = (_SDK / "docs" / "examples.md").read_text(encoding="utf-8")
    assert text.startswith("# Examples\n")
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings[0] == "## 1. Test a deployed MCP endpoint with Streamable HTTP"
    assert headings[1] == "## 2. Discover a direct local server tool before calling it"
    assert headings[2] == "## 3. Run one test across native harnesses"
    assert "client.list_all_tools()" in text
    assert "example_mcp_server.py" in text
    # The examples documentation is migrated with the broad documentation
    # cleanup checkpoint; keep this contract aligned with the current docs.
    assert "@pytest.mark.m3" in text
    assert "--harness opencode=" in text
    assert "kit.agents(agents, trials=2)" in text
    assert "HarnessMatrix" not in text
    assert "AgentSpec" not in text
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
    assert "M3_RUN_LIVE_MATH_MATRIX" in text
    expected_call = ast.parse(
        "kit.agents(_agent_selections(opencode), trials=_TRIALS_PER_CASE)", mode="eval"
    ).body
    assert any(
        isinstance(node, ast.Call) and ast.dump(node) == ast.dump(expected_call)
        for node in ast.walk(ast.parse(text))
    )
    assert "_TRIALS_PER_CASE = 2" in text
    assert not (_EXAMPLES / "tests" / example.name).exists()


def test_modern_mrtr_examples_are_complete_public_action_bound_examples() -> None:
    maintained = {
        "test_modern_mrtr_sdk.py": {
            "pytest.fixture",
            "InputRequiredResult",
            "request_state",
            "input_responses",
        },
        "test_modern_mrtr_direct.py": {
            "client.call_tool",
            "elicitation=plan",
        },
        "test_modern_mrtr_pi_qualified.py": {
            "pytest.mark.m3(agents=",
            "agent.run(",
            "server=example_server",
            "elicitation=plan",
            "to_have_tool_call",
        },
        "test_modern_mrtr_pi_unqualified.py": {
            "pytest.mark.m3(agents=",
            "agent.run(",
            "elicitation=plan",
            "to_have_tool_call",
        },
        "test_modern_mrtr_pi_session.py": {
            "pytest.mark.m3(agents=",
            "agent.session(server=example_server, timeout=120)",
            "session.send(",
            "elicitation=plan",
            "to_have_tool_call",
        },
        "test_modern_mrtr_pi_composed.py": {
            "pytest.mark.m3(agents=",
            "one_of(",
            "optional(",
            "round_of(",
            "sequence(",
            "expect_url(",
            "agent.run(",
            "to_have_tool_call",
        },
        "test_modern_mrtr_codex.py": {
            "pytest_plugins",
            "CodexExample",
            "book_verified_shipment",
            "one_of(",
            "optional(",
            "round_of(",
            ".session(",
            ".submit(",
            'permission_policy="allow"',
        },
        "test_modern_mrtr_codex_action_scopes.py": {
            "CodexExample",
            "test_codex_planned_non_accept_response_omits_wire_content_and_keeps_meta",
            "test_same_codex_session_uses_fresh_scope_for_two_planned_turns",
            "test_installed_codex_fails_when_required_plan_is_unused",
            'permission_policy="allow"',
        },
    }
    server = _EXAMPLES / "servers" / "modern_mrtr_server.py"
    assert server.exists()
    server_text = server.read_text(encoding="utf-8")
    assert "from mcp import types" in server_text
    assert "types.InputRequiredResult" in server_text
    assert "asyncio.run(_serve_stdio())" in server_text
    for filename, required in maintained.items():
        path = _EXAMPLES / "tests" / filename
        assert path.exists(), filename
        text = path.read_text(encoding="utf-8")
        assert not path.read_text(encoding="utf-8").count("AgentSpec")
        assert "ElicitationScript" not in text
        assert all(token in text for token in required), filename


def test_modern_mrtr_pi_examples_collect_with_plain_pytest_command() -> None:
    modules = [
        "examples/tests/test_modern_mrtr_pi_qualified.py",
        "examples/tests/test_modern_mrtr_pi_unqualified.py",
        "examples/tests/test_modern_mrtr_pi_session.py",
        "examples/tests/test_modern_mrtr_pi_composed.py",
    ]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *modules],
        cwd=_SDK,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.count("pi-fixture-model-pi-trial-1") == 9


def test_elicitation_docs_and_testing_guidance_keep_one_current_contract() -> None:
    examples = (_SDK / "docs" / "examples.md").read_text(encoding="utf-8")
    concepts = (_SDK / "docs" / "concepts.md").read_text(encoding="utf-8")
    elicitation = (_SDK / "docs" / "elicitation.md").read_text(encoding="utf-8")
    elicitation_api = (_SDK / "docs" / "elicitation-api.md").read_text(encoding="utf-8")
    index = (_SDK / "docs" / "README.md").read_text(encoding="utf-8")
    parity = (_SDK / "tests" / "mrtr-harness-parity.md").read_text(encoding="utf-8")
    api = (_REPO / "app" / "docs" / "api-v2.md").read_text(encoding="utf-8")
    skill = (_REPO / "skills" / "testing-with-m3" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    patterns = (
        _REPO / "skills" / "testing-with-m3" / "references" / "test-patterns.md"
    ).read_text(encoding="utf-8")

    for filename in (
        "test_modern_mrtr_sdk.py",
        "test_modern_mrtr_direct.py",
        "test_modern_mrtr_pi_qualified.py",
        "test_modern_mrtr_pi_unqualified.py",
        "test_modern_mrtr_pi_session.py",
        "test_modern_mrtr_pi_composed.py",
        "test_modern_mrtr_codex.py",
        "test_modern_mrtr_codex_action_scopes.py",
    ):
        assert filename in examples
    assert "modern_mrtr_server.py" in examples
    assert "## Modern MRTR elicitation" not in concepts
    assert "elicitation" not in concepts.lower()
    assert "[Elicitation](elicitation.md)" in index
    assert "Pi-to-Codex MRTR parity inventory" in index
    assert elicitation.startswith("# Elicitation\n")
    assert elicitation_api.startswith("# Elicitation API reference\n")
    assert "[API reference](elicitation-api.md)" in elicitation
    assert 'one_of(address(example_server, "home"' in elicitation
    assert "optional(one_of(" in elicitation
    assert 'round_of(address(example_server, "home"' in elicitation
    assert "expect(result).to_have_tool_call(" in elicitation
    assert "There is no tool-call node inside the elicitation plan" in elicitation
    assert "Codex App Server support" in elicitation
    assert "mrtr-harness-parity.md" in elicitation
    assert (
        "https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation"
        in elicitation_api
    )
    assert "AgentSpec" not in elicitation
    assert "Modern MRTR" not in elicitation
    assert all(
        term in elicitation_api
        for term in (
            "ElicitationPlan",
            "ElicitationResponse",
            "FormElicitationRequest",
            "UrlElicitationRequest",
            "PendingElicitationRound",
            "expect_form",
            "maybe_form",
            "expect_url",
            "maybe_url",
            "sequence",
            "optional",
            "one_of",
            "round_of",
            "call_tool",
            "get_prompt",
            "read_resource",
            "allow_input_required",
            "agent.run",
            "agent.submit",
            "session.send",
            "pending_elicitation",
            "respond_elicitation",
            "ElicitationEntry",
            "ProtocolCallAttempt",
            "ToolCallAttempt",
            "TraceView.elicitations",
            "ToolCallEntry.attempts",
            "input_required",
            "never perform",
            "trace",
            "Pi 0.85.1",
            "SQLiteExecutionStore",
            "same-worker",
            "form, multi-round, and URL",
            "terminal recovery error",
            "Worker/process restart",
            "Codex App Server support and limitations",
            "Implementation status: verified for unmodified Codex CLI 0.156.1",
            "serverRequest/resolved",
            "input_required",
            "effective supported plan limit is at most nine",
        )
    )
    assert "test_pi_mrtr_bridge.py" in parity
    assert "test_pi_control.py" in parity
    assert "test_real_pi_mrtr_gate.py" in parity
    assert "test_pi_control_real.py" in parity
    assert "test_modern_mrtr_pi_composed.py" in parity
    assert "test_modern_mrtr_codex.py" in parity
    assert "test_modern_mrtr_codex_action_scopes.py" in parity
    assert "unequal responses are ambiguous" in parity
    assert "elicitation_policy" not in api
    assert "ElicitationPolicy" not in api
    assert "ExecutionSpec" in api
    assert "DirectSpec" in api
    assert "AgentSpec" in api
    assert "sends a `DirectSpec` or `AgentSpec` through `MCPTestKit`" in api
    assert "no replacement discriminator or variant" in api
    assert "Elicitation guide" in skill
    assert "sdk/docs/elicitation.md" in patterns
    assert "elicitation-api.md" in skill
    assert "elicitation-api.md" in patterns
    assert "AgentSpec" not in skill
    assert "AgentSpec" not in patterns


def test_elicitation_python_snippets_compile() -> None:
    """Documentation Python examples stay syntactically executable."""

    guide = (_SDK / "docs" / "elicitation.md").read_text(encoding="utf-8")
    reference = (_SDK / "docs" / "elicitation-api.md").read_text(encoding="utf-8")
    snippets: list[str] = []
    for text in (guide, reference):
        in_python = False
        current: list[str] = []
        end_fence = ""
        for line in text.splitlines():
            if line in {"~~~python", "```python"}:
                assert not in_python, "nested Python documentation fence"
                in_python = True
                end_fence = line[:3]
                current = []
            elif line == end_fence and in_python:
                snippets.append("\n".join(current))
                in_python = False
            elif in_python:
                current.append(line)
        assert not in_python, "unterminated Python documentation fence"
        headings = [line for line in text.splitlines() if line.startswith("### ")]
        assert len(headings) == len(set(headings)), "duplicate elicitation heading"
    assert snippets
    assert "\ntry:\ntry:" not in reference
    assert reference.count("### Inventory 21") == 1
    assert reference.count('assert view.elicitations[0].request_key == "address"') == 1
    for index, snippet in enumerate(snippets):
        compile(snippet, f"<elicitation-doc-snippet-{index}>", "exec")


def test_direct_elicitation_docs_select_current_protocol() -> None:
    """Direct MRTR examples opt into the protocol that carries elicitation."""

    text = (_SDK / "docs" / "elicitation-api.md").read_text(encoding="utf-8")
    action_section = text.split("direct action parameters", 1)[1].split(
        "### Manual escape hatch", 1
    )[0]
    manual_section = text.split("### Manual escape hatch", 1)[1].split(
        "### Inventory 24", 1
    )[0]
    trace_section = text.split("## Trace assertions", 1)[1]
    selected = 'with kit.direct(server, protocol="2026-07-28") as client:'

    assert selected in action_section
    assert selected in manual_section
    assert selected in trace_section
