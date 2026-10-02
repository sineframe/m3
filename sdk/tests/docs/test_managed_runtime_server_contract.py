"""Verify the shipping example's direct-call contract as an internal test."""

import shutil
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, expect
from m3.evaluations import EvaluationDecision
from m3.types import EvaluationStatus, StdioServer

PROJECT = Path(__file__).parents[3] / "sdk/examples/docs/agents-managed-runtimes"
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}

pytestmark = pytest.mark.process_lifecycle


def quote_evaluator(context):
    passed = context.subject == EXPECTED_QUOTE
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
        score=1.0 if passed else 0.0,
        rationale="structured shipping quote matches the local contract",
    )


def test_local_server_matcher_and_evaluation(tmp_path: Path) -> None:
    server_path = tmp_path / "shipping_server.py"
    shutil.copy2(PROJECT / "shipping_server.py", server_path)
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(server_path),),
        cwd=str(tmp_path),
    )
    with MCPTestKit(env={}) as kit:
        kit.register_evaluator("shipping.quote.v1", quote_evaluator)
        with kit.direct(server) as client:
            result = client.call_tool(
                "shipping_quote", {"weight_kg": 2, "zone": "local"}
            )
        trace = client.final_trace

        assert result.structured_content == EXPECTED_QUOTE
        assert trace is not None
        expect(trace).to_have_tool_call(
            "shipping_quote",
            server="shipping",
            arguments={"weight_kg": 2, "zone": "local"},
            status="success",
            count=1,
            result={"structured_content": EXPECTED_QUOTE},
            result_partial=True,
        )
        evaluation = kit.evaluate(
            result.structured_content,
            "shipping.quote.v1",
            execution_id=trace.execution_id,
        )

    assert evaluation.status is EvaluationStatus.PASSED
