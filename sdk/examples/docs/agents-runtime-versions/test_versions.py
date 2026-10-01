import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.evaluations import EvaluationDecision
from m3.types import EvaluationStatus, StdioServer

HERE = Path(__file__).resolve().parent
PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}


def quote_evaluator(context):
    passed = context.subject == EXPECTED_QUOTE
    return EvaluationDecision(
        status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
        score=1.0 if passed else 0.0,
        rationale="structured shipping quote matches the local contract",
    )


def test_two_codex_versions_have_distinct_recorded_identity() -> None:
    model = os.environ["M3_DOCS_CODEX_MODEL"]
    versions = (
        os.environ["M3_DOCS_CODEX_VERSION_A"],
        os.environ["M3_DOCS_CODEX_VERSION_B"],
    )
    if versions[0] == versions[1] or "latest" in versions:
        raise ValueError("choose two different explicit Codex versions")

    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    selections = [
        {
            "harness": "codex",
            "models": [model],
            "runtime": "managed",
            "version": version,
        }
        for version in versions
    ]

    with MCPTestKit(env={}) as kit:
        kit.register_evaluator("shipping.quote.v1", quote_evaluator)
        results = []
        evaluations = []
        for selection in selections:
            agent = kit.agents([selection])[0]
            result = agent.run(
                PROMPT,
                server=server,
                tools=["shipping:shipping_quote"],
                timeout=180,
            )
            assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
            results.append(result)
            calls = result.trace_view.tool_calls
            assert len(calls) == 1
            assert calls[0].tool.value == "shipping_quote"
            expect(result).to_have_tool_call(
                "shipping_quote",
                server="shipping",
                arguments={"weight_kg": 2, "zone": "local"},
                status="success",
                count=1,
                result={"structured_content": EXPECTED_QUOTE},
                result_partial=True,
            )
            quote = calls[0].result.value.structured_content.value
            assert quote == EXPECTED_QUOTE
            evaluations.append(
                kit.evaluate(
                    quote,
                    "shipping.quote.v1",
                    execution_id=result.snapshot.execution_id,
                )
            )

    assert len({result.snapshot.execution_id for result in results}) == 2
    assert len({item.evaluation_id for item in evaluations}) == 2
    assert [item.status for item in evaluations] == [
        EvaluationStatus.PASSED,
        EvaluationStatus.PASSED,
    ]
    for version, result in zip(versions, results, strict=True):
        identity = result.snapshot.agent
        assert identity is not None
        assert identity.harness.requested_selector == version
        assert identity.harness.resolved_version == version
