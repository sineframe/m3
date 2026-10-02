import json
import sys
from pathlib import Path

import pytest

from m3 import ExecutionOutcome, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent
PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}

pytestmark = pytest.mark.m3(suite_name="runtime-comparison")


def test_shipping_quote_across_runtimes(agent) -> None:
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    result = agent.run(PROMPT, server=server, timeout=180)
    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    identity = result.snapshot.agent
    assert identity is not None
    runtime = identity.harness
    assert runtime.runtime == "managed"
    assert runtime.requested_selector != "latest"
    assert runtime.requested_selector == runtime.resolved_version
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
        result={"structured_content": EXPECTED_QUOTE},
        result_partial=True,
    )
    quote = result.trace_view.tool_calls[0].result.value.structured_content.value
    print(
        f"\n{runtime.kind}: requested={runtime.requested_selector} "
        f"resolved={runtime.resolved_version} quote={json.dumps(dict(quote), sort_keys=True)}"
    )
    print(f"execution={result.snapshot.execution_id}")
