"""Explicitly opt-in live OpenCode E2E coverage.

Run with:

    MCP_PAL_RUN_LIVE_OPENCODE=1 uv run --project sdk --all-extras \
      pytest -q sdk/tests/e2e/test_live_opencode.py

The default model is intentionally a free OpenCode model. Override it with
MCP_PAL_LIVE_OPENCODE_MODEL when validating another configured provider.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from mcp_pal import MCPTestKit, expect
from mcp_pal.types import (
    ExecutionOutcome,
    StdioServer,
    TextContent,
    TurnOutcome,
    TurnResponse,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.process_lifecycle]

_SDK_ROOT = Path(__file__).parents[2]
_REPOSITORY_ROOT = _SDK_ROOT.parent
_MATRIX_SERVER = _SDK_ROOT / "tests" / "fixtures" / "matrix_stdio_server.py"
_LIVE_ENABLED = os.environ.get("MCP_PAL_RUN_LIVE_OPENCODE") == "1"


def _live_selection(executable: str, model: str) -> dict[str, object]:
    return {
        "harness": "opencode",
        "models": [model],
        "executable": executable,
        "credential_env": {"OPENCODE_API_KEY": "OPENCODE_API_KEY"},
    }


def _live_servers(
    server_names: tuple[str, ...], marker_root: Path | None = None
) -> tuple[StdioServer, ...]:
    return tuple(
        StdioServer(
            name=name,
            command=sys.executable,
            args=(str(_MATRIX_SERVER),),
            cwd=str(_REPOSITORY_ROOT),
            environment=(
                {"MCP_PAL_E2E_MCP_MARKER": str(marker_root / f"{name}.jsonl")}
                if marker_root is not None
                else {}
            ),
        )
        for name in server_names
    )


def _observed_tool_calls(marker: Path) -> list[dict[str, object]]:
    assert marker.exists(), "OpenCode did not start the configured MCP server"
    return [
        value
        for line in marker.read_text(encoding="utf-8").splitlines()
        if isinstance(value := json.loads(line), dict)
        and value.get("method") == "tools/call"
    ]


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
        return _contains_text(value.content, expected)
    if hasattr(value, "text"):
        return _contains_text(value.text, expected)
    return isinstance(value, str) and expected in value


def test_live_selection_helper_is_side_effect_free_and_explicit_about_credentials() -> (
    None
):
    selection = _live_selection("/usr/local/bin/opencode", "opencode/big-pickle")
    assert selection["credential_env"] == {"OPENCODE_API_KEY": "OPENCODE_API_KEY"}
    assert selection["models"] == ["opencode/big-pickle"]


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
    selection = _live_selection(executable, model)

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agents([selection])[0].session(
            servers=list(_live_servers(("e2e-mcp",))),
            tools=["e2e-mcp:echo"],
        ) as session:
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
        for nonce, call in zip((nonce_one, nonce_two), calls, strict=False):
            assert call.correlation.value == "correlated"
            assert call.wire.state.value == "observed"
            assert call.reported.state.value == "observed"
            assert call.arguments.state.value == "observed"
            assert _contains_text(call.arguments.value, nonce)
            assert call.result.state.value == "observed"
            assert _contains_text(call.result.value, nonce)


@pytest.mark.skipif(
    not _LIVE_ENABLED, reason="set MCP_PAL_RUN_LIVE_OPENCODE=1 to call OpenCode"
)
@pytest.mark.parametrize("server_name", ("catalog", "warehouse"))
def test_live_opencode_server_search_matrix_chooses_the_right_tool(
    tmp_path: Path, server_name: str
) -> None:
    """Real model-driven NxM search cells do not name the expected tool."""

    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")
    model = os.environ.get("MCP_PAL_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    nonce = f"live-search-{server_name}"
    selection = _live_selection(executable, model)
    servers = _live_servers((server_name,), tmp_path)

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agents([selection])[0].session(
            servers=list(servers),
            tools=[f"{server_name}:echo", f"{server_name}:failure"],
        ) as session:
            turn = session.send(
                f"Use the available MCP server to return the text {nonce}. "
                "Choose the appropriate available tool yourself, call it exactly "
                "once, and return its result.",
                timeout=90,
            )

    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, (
        turn.error.model_dump(mode="json") if turn.error is not None else None
    )
    expect(session.result).to_have_tool_call(
        "echo",
        turn=turn,
        server=server_name,
        arguments={"text": nonce},
        status="success",
        count=1,
    )
    expect(session.result).to_not_have_tool_call("failure", turn=turn)
    calls = _observed_tool_calls(tmp_path / f"{server_name}.jsonl")
    assert len(calls) == 1
    assert calls[0]["name"] == "echo"
    assert calls[0]["arguments"] == {"text": nonce}


@pytest.mark.skipif(
    not _LIVE_ENABLED, reason="set MCP_PAL_RUN_LIVE_OPENCODE=1 to call OpenCode"
)
@pytest.mark.parametrize("server_name", ("catalog", "warehouse"))
@pytest.mark.parametrize(
    ("tool", "arguments", "is_error"),
    (
        ("echo", {"text": "live-matrix-value"}, False),
        ("failure", {}, True),
    ),
)
def test_live_opencode_server_by_tool_matrix(
    tmp_path: Path,
    server_name: str,
    tool: str,
    arguments: dict[str, object],
    is_error: bool,
) -> None:
    """Real OpenCode NxT cells, complementing the real ACP matrix."""

    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")
    model = os.environ.get("MCP_PAL_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    selection = _live_selection(executable, model)
    servers = _live_servers((server_name,), tmp_path)
    instruction = (
        f"Call the {server_name} MCP server's {tool} tool exactly once with "
        f"these JSON arguments: {arguments!r}. Do not call any other tool and "
        "do not retry if the tool returns an error."
    )

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agents([selection])[0].session(
            servers=list(servers), tools=[f"{server_name}:{tool}"]
        ) as session:
            turn = session.send(instruction, timeout=90)

    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, (
        turn.error.model_dump(mode="json") if turn.error is not None else None
    )
    expect(session.result).to_have_tool_call(
        tool,
        turn=turn,
        server=server_name,
        arguments=arguments,
        status="tool_error" if is_error else "success",
        count=1,
    )
    calls = _observed_tool_calls(tmp_path / f"{server_name}.jsonl")
    assert len(calls) == 1
    assert calls[0]["name"] == tool
    assert calls[0]["arguments"] == arguments


@pytest.mark.skipif(
    not _LIVE_ENABLED, reason="set MCP_PAL_RUN_LIVE_OPENCODE=1 to call OpenCode"
)
def test_live_opencode_uses_all_servers_in_one_session(tmp_path: Path) -> None:
    """Real OpenCode receives N servers and uses each across multiple turns."""

    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")
    model = os.environ.get("MCP_PAL_LIVE_OPENCODE_MODEL", "opencode/big-pickle")
    server_names = ("catalog", "warehouse")
    selection = _live_selection(executable, model)
    servers = _live_servers(server_names, tmp_path)

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agents([selection])[0].session(
            servers=list(servers),
            tools=[f"{name}:echo" for name in server_names],
        ) as session:
            turns = tuple(
                session.send(
                    f"Call only the {name} MCP server's echo tool exactly once "
                    f"with text live-{name}. Return its result.",
                    timeout=90,
                )
                for name in server_names
            )

    for name, turn in zip(server_names, turns, strict=True):
        assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
        expect(session.result).to_have_tool_call(
            "echo",
            turn=turn,
            server=name,
            arguments={"text": f"live-{name}"},
            status="success",
            count=1,
        )
        calls = _observed_tool_calls(tmp_path / f"{name}.jsonl")
        assert len(calls) == 1
        assert calls[0]["arguments"] == {"text": f"live-{name}"}
