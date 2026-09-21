"""Opt-in provider-backed smoke test for two managed OpenCode releases."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from m3 import MCPTestKit, expect
from m3.types import StdioServer

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.process_lifecycle]

_ROOT = Path(__file__).parents[2].parent
_SERVER = _ROOT / "sdk" / "tests" / "fixtures" / "matrix_stdio_server.py"


@pytest.mark.skipif(
    os.environ.get("M3_RUN_LIVE_MANAGED_RUNTIME") != "1",
    reason="set M3_RUN_LIVE_MANAGED_RUNTIME=1 to download and run two OpenCode releases",
)
def test_live_managed_opencode_versions_and_cache_hit(tmp_path: Path) -> None:
    if not os.environ.get("OPENCODE_API_KEY"):
        pytest.skip("OPENCODE_API_KEY is not available")
    model = os.environ.get("M3_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    cache = tmp_path / "managed-harnesses"
    server = StdioServer(
        name="managed-mcp",
        command=sys.executable,
        args=(str(_SERVER),),
        cwd=str(_ROOT),
    )
    versions = ("1.18.30", "1.18.31", "1.18.30")
    observed = []
    for version in versions:
        selection = {
            "harness": "opencode",
            "models": [model],
            "runtime": "managed",
            "version": version,
            "credential_env": {"OPENCODE_API_KEY": "OPENCODE_API_KEY"},
        }
        with MCPTestKit(env={}, cwd=str(_ROOT), harness_cache_dir=cache) as kit:
            result = kit.agents([selection])[0].run(
                f"Use managed-mcp to echo managed-runtime-{version}.",
                server=server,
                timeout=180,
            )
        expect(result).to_have_tool_call("echo", server="managed-mcp", status="success")
        agent = result.snapshot.agent
        assert agent is not None
        assert agent.harness.runtime == "managed"
        assert agent.harness.resolved_version == version
        assert agent.harness.digest is not None
        observed.append(agent.harness.digest)
    assert observed[0] != observed[1]
    assert observed[0] == observed[2]
    assert len(tuple(cache.glob("opencode/*/*/sha256-*/receipt.json"))) == 2


@pytest.mark.skipif(
    os.environ.get("M3_RUN_LIVE_MANAGED_RUNTIME") != "1",
    reason="set M3_RUN_LIVE_MANAGED_RUNTIME=1 to run managed latest",
)
def test_live_managed_opencode_latest_reports_resolution(tmp_path: Path) -> None:
    if not os.environ.get("OPENCODE_API_KEY"):
        pytest.skip("OPENCODE_API_KEY is not available")
    model = os.environ.get("M3_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    server = StdioServer(
        name="managed-mcp",
        command=sys.executable,
        args=(str(_SERVER),),
        cwd=str(_ROOT),
    )
    with MCPTestKit(
        env={}, cwd=str(_ROOT), harness_cache_dir=tmp_path / "cache"
    ) as kit:
        result = kit.agents(
            [
                {
                    "harness": "opencode",
                    "models": [model],
                    "runtime": "managed",
                    "credential_env": {"OPENCODE_API_KEY": "OPENCODE_API_KEY"},
                }
            ]
        )[0].run("Use managed-mcp to echo managed-runtime-latest.", server=server)
    expect(result).to_have_tool_call("echo", server="managed-mcp", status="success")
    identity = result.snapshot.agent
    assert identity is not None
    assert identity.harness.runtime == "managed"
    assert identity.harness.requested_selector == "latest"
    assert identity.harness.resolved_version
    assert identity.harness.digest and len(identity.harness.digest) == 64


@pytest.mark.skipif(
    os.environ.get("M3_RUN_LIVE_MANAGED_NATIVE") != "1",
    reason="set M3_RUN_LIVE_MANAGED_NATIVE=1 for opt-in native managed smoke tests",
)
@pytest.mark.parametrize(
    ("harness", "credential", "model_env"),
    (
        ("claude", "ANTHROPIC_API_KEY", "M3_LIVE_CLAUDE_MODEL"),
        ("codex", "OPENAI_API_KEY", "M3_LIVE_CODEX_MODEL"),
        ("pi", "OPENAI_API_KEY", "M3_LIVE_PI_MODEL"),
    ),
)
def test_live_managed_native_vendor_smoke(
    tmp_path: Path, harness: str, credential: str, model_env: str
) -> None:
    if not os.environ.get(credential):
        pytest.skip(f"{credential} is not available")
    model = os.environ.get(model_env)
    if not model:
        pytest.skip(f"set {model_env} for managed {harness}")
    server = StdioServer(
        name="managed-mcp",
        command=sys.executable,
        args=(str(_SERVER),),
        cwd=str(_ROOT),
    )
    selection = {
        "harness": harness,
        "models": [model],
        "runtime": "managed",
        "credential_env": {credential: credential},
    }
    with MCPTestKit(
        env={}, cwd=str(_ROOT), harness_cache_dir=tmp_path / "cache"
    ) as kit:
        result = kit.agents([selection])[0].run(
            f"Use managed-mcp to echo managed-runtime-{harness}.", server=server
        )
    expect(result).to_have_tool_call("echo", server="managed-mcp", status="success")
    identity = result.snapshot.agent
    assert identity is not None
    assert identity.harness.runtime == "managed"
    assert identity.harness.resolved_version
