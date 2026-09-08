"""Live evaluation of a math MCP server through multiple agent harnesses.

Run explicitly because this test invokes an external model and is
nondeterministic:

    MCP_PAL_RUN_LIVE_MATH_MATRIX=1 \
      uv run --project sdk --all-extras pytest -s -q \
      sdk/examples/nondeterministic/test_math_harness_matrix.py
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from mcp_pal import (
    # ClaudeCode,
    EvaluationContext,
    EvaluationDecision,
    EvaluationStatus,
    ExecutionResult,
    FullToolPolicy,
    HarnessCase,
    HarnessMatrix,
    MCPTestKit,
    OpenCode,
    # SecretReference,
    # RestrictiveToolPolicy,
    ServerCase,
    StdioServer,
    ToolCase,
)
from mcp_pal.aggregations import EvaluationQuery
from mcp_pal.storage import SQLiteExecutionStore


pytestmark = [pytest.mark.e2e, pytest.mark.live]

_EXAMPLES_ROOT = Path(__file__).parents[1]
_REPOSITORY_ROOT = _EXAMPLES_ROOT.parents[1]
_SERVER = _EXAMPLES_ROOT / "servers" / "math_mcp_server.py"
_LIVE_ENABLED = os.environ.get("MCP_PAL_RUN_LIVE_MATH_MATRIX") == "1"
_EVALUATOR = "example.math-answer-and-tool.v1"
_TRIALS_PER_CASE = 2


@dataclass(frozen=True, slots=True)
class MathTestCase:
    id: str
    prompt: str
    expected_value: int
    expected_tool: str


_TEST_CASES: tuple[MathTestCase, ...] = (
    MathTestCase("add-small", "What is 2 + 2?", 4, "add_tool"),
    MathTestCase("add-medium", "What is 7 + 8?", 15, "add_tool"),
    MathTestCase("subtract-small", "What is 9 - 4?", 5, "subtract_tool"),
    MathTestCase("subtract-medium", "What is 100 - 37?", 63, "subtract_tool"),
    MathTestCase("multiply-small", "What is 6 * 7?", 42, "multiply_tool"),
    MathTestCase("multiply-square", "What is 9 * 9?", 81, "multiply_tool"),
    MathTestCase("divide-small", "What is 84 / 7?", 12, "divide_tool"),
    MathTestCase("divide-medium", "What is 144 / 12?", 12, "divide_tool"),
    MathTestCase("add-larger", "What is 13 + 29?", 42, "add_tool"),
    MathTestCase("multiply-larger", "What is 11 * 12?", 132, "multiply_tool"),
)


def _harnesses(opencode: str) -> tuple[HarnessCase, ...]:
    model = os.environ.get("MCP_PAL_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    provider = model.split("/", 1)[0] if "/" in model else None
    return (
        HarnessCase(
            name="opencode",
            harness=OpenCode(
                model=model,
                provider=provider,
                executable=opencode,
            ),
        ),
        # Enable when Claude Code and its credentials are available:
        # HarnessCase(
        #     name="claude-sonnet",
        #     harness=ClaudeCode(
        #         model=os.environ["MCP_PAL_LIVE_CLAUDE_MODEL"],
        #         credential_references={
        #             "ANTHROPIC_API_KEY": SecretReference(
        #                 source="environment", name="ANTHROPIC_API_KEY"
        #             )
        #         },
        #     ),
        # ),
    )


def _contains_number(answer: str, expected: int) -> bool:
    pattern = rf"(?<![\d.]){re.escape(str(expected))}(?:\.0+)?(?![\d.])"
    return re.search(pattern, answer) is not None


def _evaluate_math_case(context: EvaluationContext) -> EvaluationDecision:
    subject = cast(Mapping[str, Any], context.subject)
    answer = subject.get("answer")
    expected_value = subject.get("expected_value")
    expected_tool = subject.get("expected_tool")
    used_tools_value = subject.get("used_tools")
    if (
        not isinstance(answer, str)
        or isinstance(expected_value, bool)
        or not isinstance(expected_value, int)
        or not isinstance(expected_tool, str)
        or not isinstance(used_tools_value, (list, tuple))
        or not all(isinstance(tool, str) for tool in used_tools_value)
    ):
        return EvaluationDecision(
            status=EvaluationStatus.ERROR,
            rationale="invalid evaluator subject",
        )

    used_tools = tuple(cast(list[str] | tuple[str, ...], used_tools_value))
    answer_ok = _contains_number(answer, expected_value)
    tool_ok = expected_tool in used_tools
    passed = answer_ok and tool_ok
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
        score=1.0 if passed else 0.0,
        rationale=(
            f"answer_ok={answer_ok}; tool_ok={tool_ok}; "
            f"expected_tool={expected_tool}; used_tools={used_tools}"
        ),
        metrics={"answer_ok": float(answer_ok), "tool_ok": float(tool_ok)},
    )


def _observed_tools(execution: ExecutionResult) -> tuple[str, ...]:
    if execution.trace is None:
        return ()
    return tuple(
        name
        for call in execution.trace_view.tool_calls
        if isinstance((name := call.tool.value), str)
    )


@pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason="set MCP_PAL_RUN_LIVE_MATH_MATRIX=1 to call OpenCode",
)
def test_ten_math_cases_across_harnesses_with_repeated_trials(
    tmp_path: Path,
) -> None:
    opencode = shutil.which("opencode")
    if opencode is None:
        pytest.skip("OpenCode is not installed")

    server = StdioServer(
        name="math",
        command=sys.executable,
        args=(str(_SERVER),),
        cwd=str(_EXAMPLES_ROOT),
    )
    # Use SQLiteExecutionStore(".mcp-pal/math-evaluations.sqlite") instead when
    # the evaluation history should remain available after pytest exits.
    store = SQLiteExecutionStore(tmp_path / "math-evaluations.sqlite")
    try:
        with MCPTestKit(store=store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
            kit.register_evaluator(_EVALUATOR, _evaluate_math_case)
            run_id = kit.run_id.root

            # Discover, rather than hard-code, every tool available from the server.
            with kit.direct(server) as client:
                advertised_tools = client.list_all_tools()
            assert advertised_tools, "the math MCP server advertised no tools"
            server_case = ServerCase(
                name="math",
                server=server,
                tools=tuple(ToolCase(name=tool.name) for tool in advertised_tools),
            )

            for test_case in _TEST_CASES:
                matrix = HarnessMatrix.each_server(
                    id=f"math-{test_case.id}",
                    servers=(server_case,),
                    harnesses=_harnesses(opencode),
                    trials=_TRIALS_PER_CASE,
                )
                for matrix_case in matrix.cases():
                    with matrix_case.session(
                        kit=kit,
                        tool_policy=FullToolPolicy(acknowledge_risk=True),
                        # To expose only known safe tools instead, replace the
                        # preceding line with:
                        # tool_policy=RestrictiveToolPolicy(
                        #     allowed_tools=(
                        #         "math:add_tool",
                        #         "math:subtract_tool",
                        #         "math:multiply_tool",
                        #         "math:divide_tool",
                        #     ),
                        # ),
                        metadata={
                            "logical_case": test_case.id,
                            "harness_config": matrix_case.harness.name,
                        },
                    ) as session:
                        turn = session.send(
                            "Use one of the available math MCP tools to answer this "
                            f"question. Choose the correct tool yourself: {test_case.prompt}",
                            timeout=120,
                        )
                    execution = session.result
                    kit.evaluate(
                        {
                            "answer": turn.response.text if turn.response is not None else "",
                            "expected_value": test_case.expected_value,
                            "expected_tool": test_case.expected_tool,
                            "used_tools": _observed_tools(execution),
                        },
                        _EVALUATOR,
                        execution_id=execution.snapshot.execution_id,
                        turn_id=turn.turn_id,
                        case_id=(
                            f"{matrix_case.matrix_id}:"
                            f"{matrix_case.cell_id or matrix_case.id}"
                        ),
                        trace=execution.trace,
                        metadata={
                            "logical_case": test_case.id,
                            "harness_config": matrix_case.harness.name,
                            "trial": matrix_case.trial,
                        },
                    )

        report = store.aggregate_evaluations(
            EvaluationQuery(
                filters={"evaluator": (_EVALUATOR,), "run_id": (run_id,)},
                group_by=("metadata.harness_config",),
            )
        )
        expected_trials = (
            len(_TEST_CASES) * len(_harnesses(opencode)) * _TRIALS_PER_CASE
        )
        assert report.totals.evaluation_count == expected_trials
        assert report.totals.measured_count == expected_trials
        assert report.totals.pass_rate is not None

        print(
            f"\nmath eval overall: {report.totals.pass_rate:.1%} "
            f"({report.totals.status_counts['passed']}/{expected_trials})"
        )
        for group in report.groups:
            print(
                f"{group.key['metadata.harness_config']}: "
                f"{group.values.pass_rate:.1%} "
                f"({group.values.status_counts['passed']}/"
                f"{group.values.measured_count})"
            )

    finally:
        store.close()
