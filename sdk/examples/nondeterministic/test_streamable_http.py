"""Nondeterministic Streamable HTTP contract and harness examples.

Run this separately from the deterministic examples:

    uv run --env-file .env --project sdk --all-extras \
      pytest -q sdk/examples/nondeterministic/test_streamable_http.py

The endpoint is external and may change independently of this repository.
The OpenCode and Codex ACP harness tests are nondeterministic and may incur
provider usage. The Codex test also requires ``codex-acp`` on ``PATH``; install
it with ``npm install -g @agentclientprotocol/codex-acp``.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest

from m3 import MCPTestKit, expect
from m3.matrix import ServerCase, ToolCase
from m3.sync_api import ToolCallResult
from m3.types import (
    ExecutionOutcome,
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


def _opencode_entry(executable: str, model: str) -> dict[str, object]:
    return {"harness": "opencode", "models": [model], "executable": executable}


def _deepwiki_server() -> HTTPServer:
    return HTTPServer(name="deepwiki", url=_DEEPWIKI_URL, trust=TrustLevel.PUBLIC)


def _codex_acp(codex_acp: str, codex: str, codex_home: Path) -> dict[str, object]:
    model = os.environ.get("M3_CODEX_MODEL")
    runtime_paths = {str(Path(codex_acp).parent), str(Path(codex).parent)}
    if node := shutil.which("node"):
        # The npm-distributed codex-acp executable has an env/node shebang.
        runtime_paths.add(str(Path(node).parent))
    environment = {
        # codex-acp is the ACP bridge; CODEX_PATH makes it use the Codex binary
        # under test instead of the version bundled with the bridge.
        "CODEX_PATH": codex,
        "CODEX_HOME": str(codex_home),
        "NO_BROWSER": "1",
        "PATH": os.pathsep.join((*sorted(runtime_paths), os.defpath)),
    }
    for name in ("CODEX_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(name):
            environment[name] = f"${{{name}}}"
    return {
        "harness": "acp",
        "models": [model or "codex-default"],
        "manifest": {
            "command": codex_acp,
            "protocol": "acp",
            "protocol_version": 1,
            "env": environment,
        },
        "session_config": {"model": model} if model else {},
    }


def _require_codex_acp() -> tuple[str, str, Path]:
    codex_acp = shutil.which(os.environ.get("M3_CODEX_ACP_EXECUTABLE", "codex-acp"))
    if codex_acp is None:
        pytest.skip("codex-acp is not installed")
    codex = shutil.which(os.environ.get("M3_CODEX_EXECUTABLE", "codex"))
    if codex is None:
        pytest.skip("Codex is not installed")
    codex_home = Path(
        os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    if (
        not any(os.environ.get(name) for name in ("CODEX_API_KEY", "OPENAI_API_KEY"))
        and not (codex_home / "auth.json").is_file()
    ):
        pytest.skip("Codex is not logged in and no API key is available")
    return codex_acp, codex, codex_home


def _require_opencode() -> tuple[str, str]:
    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")
    if not os.environ.get("OPENCODE_API_KEY"):
        pytest.skip("OPENCODE_API_KEY is not available")
    return executable, os.environ.get("M3_OPENCODE_MODEL", "opencode/big-pickle")


def test_opencode_selects_read_wiki_structure() -> None:
    executable, model = _require_opencode()
    server = _deepwiki_server()
    with MCPTestKit(env={}) as kit:
        agent = kit.agents([_opencode_entry(executable, model)])[0]
        with agent.session(server=server) as session:
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


def test_codex_acp_selects_read_wiki_structure() -> None:
    codex_acp, codex, codex_home = _require_codex_acp()
    server = _deepwiki_server()
    with MCPTestKit(env={}) as kit:
        agent = kit.agents([_codex_acp(codex_acp, codex, codex_home)])[0]
        with agent.session(server=server) as session:
            turn = session.send(
                "Use only the deepwiki read_wiki_structure MCP tool to retrieve "
                "the documentation topic hierarchy for "
                "modelcontextprotocol/python-sdk. Report its top-level sections "
                "without using shell commands or web search.",
                timeout=120,
            )

        assert turn.snapshot.outcome is TurnOutcome.COMPLETED, (
            turn.error.model_dump(mode="json") if turn.error is not None else None
        )
        expect(session.result).to_have_tool_call(
            "read_wiki_structure",
            turn=turn,
            server="deepwiki",
            arguments={"repoName": "modelcontextprotocol/python-sdk"},
            status="success",
            count=1,
        )
        called = {
            call.tool.value
            for call in session.result.trace_view.tool_calls
            if call.turn_id == turn.snapshot.turn_id
        }
        assert called.isdisjoint(
            set(_DOCUMENTED_DEEPWIKI_TOOLS) - {"read_wiki_structure"}
        )


def test_opencode_agent_calls_read_wiki_structure() -> None:
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
    with MCPTestKit(env={}) as kit:
        agent = kit.agents([_opencode_entry(executable, model)])[0]
        result = agent.run(
            "For modelcontextprotocol/python-sdk, inspect the wiki structure and "
            "report its top-level documentation sections.",
            server=server,
            timeout=120,
        )

    assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
    expect(result).to_have_tool_call(
        "read_wiki_structure",
        server="deepwiki",
        arguments={"repoName": "modelcontextprotocol/python-sdk"},
        status="success",
        count=1,
    )
