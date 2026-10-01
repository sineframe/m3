import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import StdioServer

HERE = Path(__file__).resolve().parent


def test_codex_pin_is_recorded(tmp_path: Path) -> None:
    model = os.environ["M3_DOCS_CODEX_MODEL"]
    version = os.environ["M3_DOCS_CODEX_VERSION"]
    if version == "latest":
        raise ValueError("M3_DOCS_CODEX_VERSION must be an explicit version")

    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    selection = {
        "harness": "codex",
        "models": [model],
        "runtime": "managed",
        "version": version,
    }

    with MCPTestKit(env={}, harness_cache_dir=tmp_path / "harness-cache") as kit:
        agent = kit.agents([selection])[0]
        result = agent.run(
            "Use shipping:shipping_quote once with weight_kg 2 and zone local.",
            server=server,
            tools=["shipping:shipping_quote"],
            timeout=180,
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    identity = result.snapshot.agent
    assert identity is not None
    assert identity.harness.kind == "codex"
    assert identity.harness.runtime == "managed"
    assert identity.harness.requested_selector == version
    assert identity.harness.resolved_version == version
    assert identity.harness.target is not None
    assert identity.harness.digest is not None
    assert len(identity.harness.digest) == 64
    expect(result).to_have_tool_call(
        "shipping_quote",
        server="shipping",
        arguments={"weight_kg": 2, "zone": "local"},
        status="success",
        count=1,
        result={"structured_content": {"amount": 9.0, "currency": "USD"}},
        result_partial=True,
    )
