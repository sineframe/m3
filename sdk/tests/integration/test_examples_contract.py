"""Small regressions for the public, user-facing example catalog."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_SDK = Path(__file__).parents[2]
_EXAMPLES = _SDK / "examples"
_REPO = _SDK.parent
_SITE = _REPO / "docs" / "site"


def _assert_sdk_doc_is_relocation_stub(filename: str, route: str) -> str:
    text = (_SDK / "docs" / filename).read_text(encoding="utf-8")
    assert "moved" in text.splitlines()[0].lower(), filename
    assert f"https://m3.sineframe.com/docs/{route}" in text, filename
    return text


def test_examples_docs_are_goal_oriented_and_not_a_synthetic_catalog() -> None:
    text = (_SITE / "examples.md").read_text(encoding="utf-8")
    assert text.startswith('---\ntitle: "Examples by task"\n')
    assert "# Examples by task" in text

    pages = (
        "getting-started.md",
        "guides/servers/stdio.md",
        "guides/servers/http.md",
        "guides/agents/first-test.md",
        "guides/agents/sessions.md",
        "guides/results/baselines.md",
        "guides/evaluations/custom.md",
        "guides/elicitation/plans.md",
    )
    for page in pages:
        assert f"]({page})" in text
        assert (_SITE / page).is_file(), page

    # The documentation links to executable projects maintained in the SDK.
    assert "sdk/examples/docs/elicitation-plans" in (
        _SITE / "guides" / "elicitation" / "plans.md"
    ).read_text(encoding="utf-8")
    project = _EXAMPLES / "docs" / "elicitation-plans"
    assert (project / "test_plan.py").is_file()
    assert (project / "elicitation_server.py").is_file()
    _assert_sdk_doc_is_relocation_stub("examples.md", "examples")


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
    examples = (_SITE / "examples.md").read_text(encoding="utf-8")
    concepts = (_SITE / "concepts" / "testing-model.md").read_text(encoding="utf-8")
    elicitation = (_SITE / "guides" / "elicitation" / "plans.md").read_text(
        encoding="utf-8"
    )
    elicitation_api = (
        _SITE / "reference" / "python" / "m3" / "elicitation.md"
    ).read_text(encoding="utf-8")
    managed_input_api = (
        _SITE / "reference" / "python" / "m3" / "managed-input.md"
    ).read_text(encoding="utf-8")
    index = (_SDK / "docs" / "README.md").read_text(encoding="utf-8")
    parity = (_SDK / "tests" / "mrtr-harness-parity.md").read_text(encoding="utf-8")
    api = (_REPO / "app" / "docs" / "api-v2.md").read_text(encoding="utf-8")
    skill = (_REPO / "skills" / "testing-with-m3" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    patterns = (
        _REPO / "skills" / "testing-with-m3" / "references" / "test-patterns.md"
    ).read_text(encoding="utf-8")

    assert "guides/elicitation/plans.md" in examples
    assert "## Modern MRTR elicitation" not in concepts
    assert "elicitation" not in concepts.lower()
    assert "https://m3.sineframe.com/docs/reference/python/" in index
    assert elicitation.startswith(
        '---\ntitle: "Plan answers to elicitation requests"\n'
    )
    assert "elicitation plan" in elicitation.lower()
    assert "sequence(...)" in elicitation
    assert "one_of(...)" in elicitation
    assert "round_of(...)" in elicitation
    assert "sdk/examples/docs/elicitation-plans" in elicitation
    assert elicitation_api.startswith(
        '---\ntitle: "Elicitation plans and direct request handling"\n'
    )
    assert all(
        term in elicitation_api
        for term in (
            "ElicitationPlan",
            "ElicitationResponse",
            "FormElicitationRequest",
            "UrlElicitationRequest",
            "expect_form",
            "maybe_form",
            "expect_url",
            "maybe_url",
            "sequence",
            "round_of",
            "one_of",
            "optional",
            "agent.run",
            "agent.submit",
            "session.send",
        )
    )
    assert managed_input_api.startswith('---\ntitle: "Managed elicitation input API"\n')
    assert all(
        term in managed_input_api
        for term in (
            "PendingElicitationRound",
            "ManagedInputLease",
            "ManagedInputRecord",
            "respond_elicitation",
            "idempotency_key",
            "fail_recovery",
        )
    )
    _assert_sdk_doc_is_relocation_stub("elicitation.md", "guides/elicitation/plans")
    _assert_sdk_doc_is_relocation_stub(
        "elicitation-api.md", "reference/python/m3/elicitation"
    )
    assert "Modern MRTR" not in elicitation
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
    assert "are the API v2 `ExecutionSpec` wire variants" in api
    assert "Elicitation guide" in skill
    assert "https://m3.sineframe.com/docs/guides/elicitation/plans" in patterns
    assert "https://m3.sineframe.com/docs/reference/python/m3/elicitation" in skill
    assert "https://m3.sineframe.com/docs/reference/python/m3/elicitation" in patterns
    assert "AgentSpec" not in skill
    assert "AgentSpec" not in patterns


def test_elicitation_python_snippets_compile() -> None:
    """Documentation Python examples stay syntactically executable."""

    guide = (_SITE / "guides" / "elicitation" / "plans.md").read_text(encoding="utf-8")
    reference = (_SITE / "reference" / "python" / "m3" / "elicitation.md").read_text(
        encoding="utf-8"
    )
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
    for index, snippet in enumerate(snippets):
        compile(snippet, f"<elicitation-doc-snippet-{index}>", "exec")

    source = (_EXAMPLES / "docs" / "elicitation-plans" / "test_plan.py").read_text(
        encoding="utf-8"
    )
    ast.parse(source)
    assert "client.call_tool(" in source
    assert "elicitation=plan" in source
    assert "test_plan.py" in guide


def test_direct_elicitation_docs_select_current_protocol() -> None:
    """Canonical guide and runnable docs example select elicitation support."""

    guide = (_SITE / "guides" / "elicitation" / "plans.md").read_text(encoding="utf-8")
    example = (_EXAMPLES / "docs" / "elicitation-plans" / "test_plan.py").read_text(
        encoding="utf-8"
    )

    assert 'Config(protocol_revision="2026-07-28")' in guide
    assert 'Config(protocol_revision="2026-07-28")' in example
    assert "elicitation=plan" in example
    assert "with kit.direct(server) as client" in example
