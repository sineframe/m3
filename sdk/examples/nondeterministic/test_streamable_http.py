"""Nondeterministic Streamable HTTP contract and harness examples.

Run this separately from the deterministic examples:

    uv run --env-file .env --project sdk --all-extras \
      pytest -q sdk/examples/nondeterministic/test_streamable_http.py

The endpoint is external and may change independently of this repository.
The two OpenCode harness tests are nondeterministic and may incur provider
usage.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping

import pytest
from mcp_pal import MCPTestKit, expect
from mcp_pal.matrix import HarnessCase, HarnessMatrix, ServerCase, ToolCase
from mcp_pal.sync_api import ToolCallResult
from mcp_pal.types import (
    AgentSpec,
    ExecutionOutcome,
    OpenCode,
    RestrictiveToolPolicy,
    SecretReference,
    ServerBinding,
    HTTPServer,
    TransportKind,
    TrustLevel,
    TurnOutcome,
)


pytestmark = pytest.mark.e2e

_DEEPWIKI_URL = "https://mcp.deepwiki.com/mcp"
_DOCUMENTED_DEEPWIKI_TOOLS = (
    "ask_question",
    "read_wiki_contents",
    "read_wiki_structure",
)


def test_streamable_http_discovers_and_calls_documented_tool() -> None:
    server = HTTPServer(name="deepwiki", url=_DEEPWIKI_URL)

    with MCPTestKit(env={}) as kit:
        with kit.direct(server) as client:
            assert client.initialization is not None
            tools = client.list_all_tools()
            assert tools

            available = {tool.name for tool in tools}
            assert set(_DOCUMENTED_DEEPWIKI_TOOLS) <= available
            result = client.call_tool(
                "read_wiki_structure",
                {"repoName": "modelcontextprotocol/python-sdk"},
            )
            assert isinstance(result, ToolCallResult)
            assert result.is_error is False
            text_blocks = [
                block["text"]
                for block in result.content
                if isinstance(block, Mapping)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ]
            assert any(text.strip() for text in text_blocks)

        evidence = client.transport_evidence
        assert evidence is not None
        assert evidence.state == "closed"

        final_trace = client.final_trace
        assert final_trace is not None
        view = final_trace.view()
        assert view.outcome is ExecutionOutcome.COMPLETED
        transports = view.transports
        assert transports
        assert any(
            entry.phase == "connected"
            and entry.configured.value is TransportKind.STREAMABLE_HTTP
            and entry.instrumented.value is TransportKind.STREAMABLE_HTTP
            for entry in transports
        )


def _opencode(executable: str, model: str) -> OpenCode:
    provider = model.split("/", 1)[0] if "/" in model else None
    return OpenCode(
        model=model,
        provider=provider,
        executable=executable,
        credential_references={
            "OPENCODE_API_KEY": SecretReference(
                source="environment", name="OPENCODE_API_KEY"
            )
        },
    )


def _deepwiki_server() -> HTTPServer:
    return HTTPServer(
        name="deepwiki", url=_DEEPWIKI_URL, trust=TrustLevel.PUBLIC
    )


def _require_opencode() -> tuple[str, str]:
    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")
    if not os.environ.get("OPENCODE_API_KEY"):
        pytest.skip("OPENCODE_API_KEY is not available")
    return executable, os.environ.get(
        "MCP_PAL_OPENCODE_MODEL", "opencode/big-pickle"
    )


def test_opencode_selects_read_wiki_structure() -> None:
    executable, model = _require_opencode()
    server = _deepwiki_server()
    spec = AgentSpec(
        harness=_opencode(executable, model),
        servers=(ServerBinding(server=server, alias="deepwiki"),),
        tool_policy=RestrictiveToolPolicy(
            allowed_tools=tuple(
                f"deepwiki:{name}" for name in _DOCUMENTED_DEEPWIKI_TOOLS
            )
        ),
    )
    with MCPTestKit(env={}) as kit:
        with kit.agent_session(spec) as session:
            turn = session.send(
                "For documentation topics in modelcontextprotocol/python-sdk, "
                "retrieve the available documentation topic hierarchy and report "
                "its top-level sections.",
                timeout=120,
            )

        assert turn.snapshot.outcome is TurnOutcome.COMPLETED, turn.error
        result = session.result
        expect(result).to_have_tool_call(
            "read_wiki_structure",
            turn=turn,
            server="deepwiki",
            arguments={"repoName": "modelcontextprotocol/python-sdk"},
            status="success",
            count=1,
        )
        called = {
            call.tool.value
            for call in result.trace_view.tool_calls
            if call.turn_id == turn.snapshot.turn_id
        }
        assert called.isdisjoint(
            set(_DOCUMENTED_DEEPWIKI_TOOLS) - {"read_wiki_structure"}
        )


def test_opencode_harness_matrix_calls_read_wiki_structure() -> None:
    executable, model = _require_opencode()
    server = ServerCase(
        name="deepwiki",
        server=_deepwiki_server(),
        tools=(
            ToolCase(
                name="read_wiki_structure",
                arguments={"repoName": "modelcontextprotocol/python-sdk"},
                prompt=(
                    "For modelcontextprotocol/python-sdk, inspect the wiki "
                    "structure and report its top-level documentation sections."
                ),
            ),
        ),
    )
    matrix = HarnessMatrix.each_tool(
        servers=(server,),
        harnesses=(HarnessCase(name="opencode", harness=_opencode(executable, model)),),
    )
    case = matrix.cases()[0]
    assert case.id == "deepwiki/read_wiki_structure/opencode"
    with MCPTestKit(env={}) as kit:
        result = case.run(kit=kit, timeout=120)

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "read_wiki_structure",
        server="deepwiki",
        arguments={"repoName": "modelcontextprotocol/python-sdk"},
        status="success",
        count=1,
    )
