import os
import sys
from pathlib import Path

from m3 import ExecutionOutcome, MCPTestKit, expect
from m3.types import SecretReference, StdioServer

HERE = Path(__file__).resolve().parent
PROMPT = "Use shipping:shipping_quote once with weight_kg 2 and zone local."
EXPECTED_QUOTE = {"amount": 9.0, "currency": "USD"}


def test_native_pins_and_local_acp_share_assertions(tmp_path: Path) -> None:
    selections = [
        {
            "harness": "codex",
            "models": [os.environ["M3_DOCS_CODEX_MODEL"]],
            "runtime": "managed",
            "version": os.environ["M3_DOCS_CODEX_VERSION"],
        },
        {
            "harness": "pi",
            "models": [os.environ["M3_DOCS_PI_MODEL"]],
            "provider": os.environ["M3_DOCS_PI_PROVIDER"],
            "credential_references": {
                os.environ["M3_DOCS_PI_KEY_NAME"]: SecretReference(
                    source="environment", name="M3_DOCS_PI_API_KEY"
                )
            },
            "runtime": "managed",
            "version": os.environ["M3_DOCS_PI_VERSION"],
        },
        {
            "harness": "claude_code",
            "models": [os.environ["M3_DOCS_CLAUDE_MODEL"]],
            "credential_references": {
                os.environ["M3_DOCS_CLAUDE_KEY_NAME"]: SecretReference(
                    source="environment", name="M3_DOCS_CLAUDE_API_KEY"
                )
            },
            "runtime": "managed",
            "version": os.environ["M3_DOCS_CLAUDE_VERSION"],
        },
        {
            "harness": "opencode",
            "models": [os.environ["M3_DOCS_OPENCODE_MODEL"]],
            "provider": os.environ["M3_DOCS_OPENCODE_PROVIDER"],
            "credential_references": {
                os.environ["M3_DOCS_OPENCODE_KEY_NAME"]: SecretReference(
                    source="environment", name="M3_DOCS_OPENCODE_API_KEY"
                )
            },
            "runtime": "managed",
            "version": os.environ["M3_DOCS_OPENCODE_VERSION"],
        },
        {
            "harness": "acp",
            "models": ["fixture"],
            "manifest": {
                "schema_version": "m3.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(HERE / "deterministic_acp_agent.py")],
            },
        },
    ]
    server = StdioServer(
        name="shipping",
        command=sys.executable,
        args=(str(HERE / "shipping_server.py"),),
        cwd=str(HERE),
    )
    with MCPTestKit(
        env={}, harness_cache_dir=tmp_path / "external-harness-cache"
    ) as kit:
        agents = kit.agents(selections, trials=2)
        assert len(agents) == 10
        for agent in agents:
            result = agent.run(PROMPT, server=server, timeout=180)
            assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
            expect(result).to_have_tool_call(
                "shipping_quote",
                server="shipping",
                arguments={"weight_kg": 2, "zone": "local"},
                status="success",
                count=1,
                result={"structured_content": EXPECTED_QUOTE},
                result_partial=True,
            )
