"""Explicitly opt-in live OpenCode E2E coverage.

Run with:

    MCP_PAL_RUN_LIVE_OPENCODE=1 uv run --project sdk --all-extras \
      pytest -q sdk/tests/e2e/test_live_opencode.py

The default model is intentionally a free OpenCode model. Override it with
MCP_PAL_LIVE_OPENCODE_MODEL when validating another configured provider.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from mcp_pal.types import (
    AgentExecutionSpec,
    ExecutionOutcome,
    OpenCode,
    RestrictiveToolPolicy,
    SecretReference,
    ServerBinding,
    StdioServer,
    TextContent,
    TurnOutcome,
    TurnResponse,
)

from mcp_pal import MCPTestKit

pytestmark = [pytest.mark.e2e, pytest.mark.live]

_SDK_ROOT = Path(__file__).parents[2]
_REPOSITORY_ROOT = _SDK_ROOT.parent
_MATRIX_SERVER = _SDK_ROOT / "tests" / "fixtures" / "matrix_stdio_server.py"
_LIVE_ENABLED = os.environ.get("MCP_PAL_RUN_LIVE_OPENCODE") == "1"


def _live_spec(executable: str, model: str) -> AgentExecutionSpec:
    provider = model.split("/", 1)[0] if "/" in model else None
    return AgentExecutionSpec(
        harness=OpenCode(
            model=model,
            provider=provider,
            executable=executable,
            credential_references={
                "OPENCODE_API_KEY": SecretReference(
                    source="environment", name="OPENCODE_API_KEY"
                )
            },
        ),
        servers=(
            ServerBinding(
                server=StdioServer(
                    name="e2e-mcp",
                    command=sys.executable,
                    args=(str(_MATRIX_SERVER),),
                    cwd=str(_REPOSITORY_ROOT),
                ),
                alias="e2e-mcp",
            ),
        ),
        tool_policy=RestrictiveToolPolicy(allowed_tools=("e2e-mcp:echo",)),
    )


def _assert_response_contains(response: TurnResponse | None, expected: str) -> None:
    assert response is not None
    assert response.content
    first = response.content[0]
    assert isinstance(first, TextContent)
    assert expected in first.text


def _contains_text(value: object, expected: str) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_text(key, expected) or _contains_text(item, expected)
            for key, item in value.items()
        )
    if isinstance(value, (tuple, list)):
        return any(_contains_text(item, expected) for item in value)
    if hasattr(value, "content"):
        return _contains_text(getattr(value, "content"), expected)
    if hasattr(value, "text"):
        return _contains_text(getattr(value, "text"), expected)
    return isinstance(value, str) and expected in value


def test_live_spec_helper_is_side_effect_free_and_explicit_about_credentials() -> None:
    spec = _live_spec("/usr/local/bin/opencode", "provider/model")
    assert spec.harness is not None
    assert isinstance(spec.harness, OpenCode)
    assert spec.harness.credential_references == {
        "OPENCODE_API_KEY": SecretReference(
            source="environment", name="OPENCODE_API_KEY"
        ),
    }
    assert spec.harness.model == "provider/model"
    assert isinstance(spec.tool_policy, RestrictiveToolPolicy)
    assert spec.tool_policy.allowed_tools == ("e2e-mcp:echo",)


@pytest.mark.skipif(
    not _LIVE_ENABLED, reason="set MCP_PAL_RUN_LIVE_OPENCODE=1 to call OpenCode"
)
def test_live_opencode_calls_the_mcp_across_two_turns() -> None:
    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")

    model = os.environ.get("MCP_PAL_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    nonce_one = "mcp-pal-live-e2e-first"
    nonce_two = "mcp-pal-live-e2e-second"
    spec = _live_spec(executable, model)

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(spec) as session:
            first = session.send(
                f"Call the e2e-mcp echo tool with text {nonce_one}. Return exactly the tool result.",
                timeout=90,
            )
            second = session.send(
                f"Call the same echo tool with text {nonce_two}. Return exactly the tool result.",
                timeout=90,
            )

            assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
        assert second.snapshot.outcome is TurnOutcome.COMPLETED, second.error
        _assert_response_contains(first.response, nonce_one)
        _assert_response_contains(second.response, nonce_two)
        assert first.snapshot.session_id == second.snapshot.session_id
        assert session.result.trace is not None
        trace = session.result.trace
        assert trace.events[-1].payload["outcome"] == ExecutionOutcome.COMPLETED.value
        view = trace.view()
        calls = [call for call in view.tool_calls if call.tool.value == "echo"]
        assert len(calls) == 2, "OpenCode did not report exactly two MCP echo calls"
        for nonce, call in zip((nonce_one, nonce_two), calls):
            assert call.correlation.value == "correlated"
            assert call.wire.state.value == "observed"
            assert call.reported.state.value == "observed"
            assert call.arguments.state.value == "observed"
            assert _contains_text(call.arguments.value, nonce)
            assert call.result.state.value == "observed"
            assert _contains_text(call.result.value, nonce)
