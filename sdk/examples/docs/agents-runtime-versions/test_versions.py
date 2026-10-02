import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent
PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}


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
        results = []
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
            expect(result).to_have_tool_call(
                "shipping_quote",
                server="shipping",
                arguments={"weight_kg": 2, "zone": "local"},
                status="success",
                count=1,
                result={"structured_content": EXPECTED_QUOTE},
                result_partial=True,
            )

    assert len({result.snapshot.execution_id for result in results}) == 2
    for version, result in zip(versions, results, strict=True):
        identity = result.snapshot.agent
        assert identity is not None
        assert identity.harness.requested_selector == version
        assert identity.harness.resolved_version == version
