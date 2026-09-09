"""Black-box E2E coverage for the four user-shaped SDK matrix patterns."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

from mcp_pal import MCPTestKit, expect
from mcp_pal.sync_api import ToolCallResult
from mcp_pal.types import (
    ACPAgent,
    AgentSpec,
    FullToolPolicy,
    RestrictiveToolPolicy,
    ServerBinding,
    StdioServer,
    TurnOutcome,
)


pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]

_SDK_ROOT = Path(__file__).parents[2]
_REPOSITORY_ROOT = _SDK_ROOT.parent
_FIXTURES = _SDK_ROOT / "tests" / "fixtures"
_MCP_SERVER = _FIXTURES / "matrix_stdio_server.py"
_MATRIX_HARNESS = _FIXTURES / "matrix_acp_harness.py"
_SERVER_NAMES = ("catalog", "warehouse")
_HARNESS_KINDS = ("acp",)
_TOOL_CASES = (
    ("echo", {"text": "matrix-value"}, False),
    ("failure", {}, True),
)


def _server(name: str, marker: Path) -> StdioServer:
    return StdioServer(
        name=name,
        command=sys.executable,
        args=(str(_MCP_SERVER),),
        cwd=str(_REPOSITORY_ROOT),
        environment={"MCP_PAL_E2E_MCP_MARKER": str(marker)},
    )


def _harness(kind: str) -> ACPAgent:
    if kind == "acp":
        return ACPAgent(
            model="matrix-fixture",
            manifest={
                "schema_version": "mcp-pal.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(_MATRIX_HARNESS)],
                "env": {},
            },
        )
    raise AssertionError(f"unknown harness fixture: {kind}")


def _full_policy(kind: str, server: str) -> FullToolPolicy:
    assert kind == "acp" and server
    return FullToolPolicy(acknowledge_risk=True)


def _tool_policy(
    kind: str, server: str, tool: str
) -> RestrictiveToolPolicy:
    assert kind == "acp"
    return RestrictiveToolPolicy(allowed_tools=(f"{server}:{tool}",))


def _prompt(
    *, server: str | None = None, tool: str | None = None,
    arguments: dict[str, Any] | None = None, query: str | None = None,
) -> str:
    return json.dumps(
        {
            key: value
            for key, value in {
                "server": server,
                "tool": tool,
                "arguments": arguments,
                "query": query,
            }.items()
            if value is not None
        },
        separators=(",", ":"),
    )


def _tool_observations(marker: Path) -> list[dict[str, Any]]:
    assert marker.exists(), "the harness did not start the configured MCP server"
    return [
        value
        for line in marker.read_text(encoding="utf-8").splitlines()
        if isinstance(value := json.loads(line), dict)
        and value.get("method") == "tools/call"
    ]


@pytest.mark.parametrize("server_name", _SERVER_NAMES)
@pytest.mark.parametrize("harness_kind", _HARNESS_KINDS)
def test_server_by_harness_search_matrix_calls_a_discovered_tool(
    tmp_path: Path, server_name: str, harness_kind: str
) -> None:
    """N×M: each harness receives one server and chooses from its tool list."""

    marker = tmp_path / f"{server_name}-{harness_kind}.jsonl"
    binding = ServerBinding(
        server=_server(server_name, marker), alias=server_name
    )
    spec = AgentSpec(
        harness=_harness(harness_kind),
        servers=(binding,),
        tool_policy=_full_policy(harness_kind, server_name),
    )

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(spec) as session:
            turn = session.send(
                _prompt(server=server_name, query="find the matrix value"),
                timeout=20,
            )

    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
    expect(session.result).to_have_tool_call(
        "echo", turn=turn, server=server_name, status="success", count=1
    )
    observations = _tool_observations(marker)
    assert len(observations) == 1
    assert observations[0]["name"] == "echo"


@pytest.mark.parametrize("harness_kind", _HARNESS_KINDS)
def test_each_harness_can_use_all_servers_in_one_multiturn_session(
    tmp_path: Path, harness_kind: str
) -> None:
    """M executions: each harness receives all N servers in one session."""

    markers = {
        name: tmp_path / f"all-{harness_kind}-{name}.jsonl"
        for name in _SERVER_NAMES
    }
    bindings = tuple(
        ServerBinding(server=_server(name, markers[name]), alias=name)
        for name in _SERVER_NAMES
    )
    spec = AgentSpec(
        harness=_harness(harness_kind),
        servers=bindings,
        tool_policy=_full_policy(harness_kind, _SERVER_NAMES[0]),
    )

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(spec) as session:
            turns = tuple(
                session.send(
                    _prompt(
                        server=name,
                        tool="echo",
                        arguments={"text": f"from-{name}"},
                    ),
                    timeout=20,
                )
                for name in _SERVER_NAMES
            )

    for name, turn in zip(_SERVER_NAMES, turns, strict=True):
        assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
        expect(session.result).to_have_tool_call(
            "echo",
            turn=turn,
            server=name,
            arguments={"text": f"from-{name}"},
            status="success",
            count=1,
        )
        observations = _tool_observations(markers[name])
        assert len(observations) == 1
        assert observations[0]["arguments"] == {"text": f"from-{name}"}


@pytest.mark.parametrize("server_name", _SERVER_NAMES)
@pytest.mark.parametrize(("tool", "arguments", "is_error"), _TOOL_CASES)
def test_deterministic_server_by_tool_matrix(
    tmp_path: Path,
    server_name: str,
    tool: str,
    arguments: dict[str, Any],
    is_error: bool,
) -> None:
    """N×T: direct calls give deterministic MCP contract coverage."""

    marker = tmp_path / f"direct-{server_name}-{tool}.jsonl"
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.direct(_server(server_name, marker)) as client:
            result = client.call_tool(tool, arguments)

    assert isinstance(result, ToolCallResult)
    assert result.is_error is is_error
    if tool == "echo":
        assert result.content[0]["text"] == "matrix-value"
    else:
        assert result.content[0]["text"] == "expected failure"
    observations = _tool_observations(marker)
    assert len(observations) == 1
    assert observations[0]["name"] == tool
    assert observations[0]["arguments"] == arguments


@pytest.mark.parametrize("server_name", _SERVER_NAMES)
@pytest.mark.parametrize(("tool", "arguments", "is_error"), _TOOL_CASES)
@pytest.mark.parametrize("harness_kind", _HARNESS_KINDS)
def test_server_by_tool_by_harness_matrix(
    tmp_path: Path,
    server_name: str,
    tool: str,
    arguments: dict[str, Any],
    is_error: bool,
    harness_kind: str,
) -> None:
    """N×T×M: every harness is asked to invoke every server tool."""

    marker = tmp_path / f"full-{server_name}-{tool}-{harness_kind}.jsonl"
    spec = AgentSpec(
        harness=_harness(harness_kind),
        servers=(
            ServerBinding(
                server=_server(server_name, marker), alias=server_name
            ),
        ),
        tool_policy=_tool_policy(harness_kind, server_name, tool),
    )

    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(spec) as session:
            turn = session.send(
                _prompt(server=server_name, tool=tool, arguments=arguments),
                timeout=20,
            )

    assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
    expect(session.result).to_have_tool_call(
        tool,
        turn=turn,
        server=server_name,
        arguments=arguments,
        status="tool_error" if is_error else "success",
        count=1,
    )
    observations = _tool_observations(marker)
    assert len(observations) == 1
    assert observations[0]["name"] == tool
    assert observations[0]["arguments"] == arguments
