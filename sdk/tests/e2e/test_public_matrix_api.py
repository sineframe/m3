"""Public matrix API workflows against real stdio MCP and ACP processes."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from m3 import MCPTestKit, expect
from m3.matrix import HarnessCase, HarnessMatrix, ServerCase, ToolCase, ToolMatrix
from m3.storage import SQLiteExecutionStore
from m3.types import (
    ACPAgent,
    CallToolResult,
    ExecutionOutcome,
    RestrictiveToolPolicy,
    StdioServer,
    TextContent,
    TurnOutcome,
)

pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]

_SDK_ROOT = Path(__file__).parents[2]
_REPOSITORY_ROOT = _SDK_ROOT.parent
_FIXTURES = _SDK_ROOT / "tests" / "fixtures"
_MCP_SERVER = _FIXTURES / "matrix_stdio_server.py"
_ACP_HARNESS = _FIXTURES / "matrix_acp_harness.py"


def _server(name: str, marker: Path) -> StdioServer:
    return StdioServer(
        name=name,
        command=sys.executable,
        args=(str(_MCP_SERVER),),
        cwd=str(_REPOSITORY_ROOT),
        environment={"M3_E2E_MCP_MARKER": str(marker)},
    )


def _harness() -> HarnessCase:
    return HarnessCase(
        name="acp",
        harness=ACPAgent(
            model="matrix-fixture",
            manifest={
                "schema_version": "m3.harness.v1",
                "protocol": "acp",
                "protocol_version": 1,
                "command": sys.executable,
                "args": [str(_ACP_HARNESS)],
                "env": {},
            },
        ),
    )


def _server_case(name: str, marker: Path, tools: tuple[ToolCase, ...]) -> ServerCase:
    return ServerCase(name=name, server=_server(name, marker), tools=tools)


def _calls(marker: Path) -> list[dict[str, Any]]:
    assert marker.exists()
    return [
        value
        for line in marker.read_text(encoding="utf-8").splitlines()
        if isinstance(value := json.loads(line), dict)
        and value.get("method") == "tools/call"
    ]


def _assert_processes_exited(marker: Path) -> None:
    pids = {
        int(call["pid"]) for call in _calls(marker) if isinstance(call.get("pid"), int)
    }
    deadline = time.monotonic() + 2
    while pids and time.monotonic() < deadline:
        alive: set[int] = set()
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            alive.add(pid)
        pids = alive
        if pids:
            time.sleep(0.01)
    assert not pids, f"MCP fixture processes remained alive: {sorted(pids)}"


def test_tool_matrix_calls_owned_tools_and_persists_each_cell(tmp_path: Path) -> None:
    markers = {name: tmp_path / f"{name}.jsonl" for name in ("catalog", "warehouse")}
    servers = (
        _server_case(
            "catalog",
            markers["catalog"],
            (ToolCase(name="echo", arguments={"text": "catalog"}),),
        ),
        _server_case(
            "warehouse", markers["warehouse"], (ToolCase(name="failure", arguments={}),)
        ),
    )
    matrix = ToolMatrix(servers=servers)
    store = SQLiteExecutionStore(tmp_path / "matrix.sqlite")
    results = []
    try:
        with MCPTestKit(store=store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
            for case in matrix.cases():
                result = case.run(kit=kit)
                results.append(result)
                assert result.trace is not None
                assert result.trace_view.tool_calls
                persisted = store.get_execution_spec(result.snapshot.execution_id)
                assert persisted is not None
                report = store.get_report(result.snapshot.execution_id)
                assert report is not None
                assert persisted.metadata["m3.matrix.case_id"] == case.id
                assert persisted.metadata["m3.matrix.servers"] == case.server.name
        assert [case.id for case in matrix.cases()] == [
            "catalog/echo",
            "warehouse/failure",
        ]
        assert isinstance(results[0].direct_result, CallToolResult)
        assert results[0].direct_result.is_error is False
        assert results[0].direct_result.content[0]["text"] == "catalog"
        assert isinstance(results[1].direct_result, CallToolResult)
        assert results[1].direct_result.is_error is True
        assert results[1].direct_result.content[0]["text"] == "expected failure"
        assert len(_calls(markers["catalog"])) == 1
        assert len(_calls(markers["warehouse"])) == 1
        _assert_processes_exited(markers["catalog"])
        _assert_processes_exited(markers["warehouse"])
    finally:
        store.close()


def test_harness_matrix_each_server_and_each_tool_use_real_acp_decisions(
    tmp_path: Path,
) -> None:
    markers = {name: tmp_path / f"{name}.jsonl" for name in ("catalog", "warehouse")}
    servers = (
        _server_case(
            "catalog",
            markers["catalog"],
            (ToolCase(id="search", name="echo"), ToolCase(id="get", name="failure")),
        ),
        _server_case(
            "warehouse", markers["warehouse"], (ToolCase(id="search", name="echo"),)
        ),
    )
    prompt = json.dumps({"query": "find the matrix value"})
    matrix = HarnessMatrix.each_server(
        servers=servers, harnesses=(_harness(),), trials=2
    )
    store = SQLiteExecutionStore(tmp_path / "harness.sqlite")
    try:
        with MCPTestKit(store=store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
            for case in matrix.cases():
                result = case.run(prompt, kit=kit, timeout=20)
                assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
                assert result.trace_view.tool_calls
                expect(result).to_have_tool_call(
                    "echo", server=case.server.name, status="success"
                )
                call = result.trace_view.tool_calls[0]
                tool_result = call.result.value
                assert tool_result is not None and tool_result.content
                assert isinstance(tool_result.content[0], TextContent)
                assert tool_result.content[0].text == "find the matrix value"
                spec = store.get_execution_spec(result.snapshot.execution_id)
                assert spec is not None
                assert isinstance(spec.tool_policy, RestrictiveToolPolicy)
                assert spec.tool_policy.allowed_tools == tuple(
                    f"{case.server.name}:{tool.name}" for tool in case.server.tools
                )
        _assert_processes_exited(markers["catalog"])
        _assert_processes_exited(markers["warehouse"])
    finally:
        store.close()
    assert len(_calls(markers["catalog"])) == 2
    assert len(_calls(markers["warehouse"])) == 2

    tool_matrix = HarnessMatrix.each_tool(servers=servers, harnesses=(_harness(),))
    tool_store = SQLiteExecutionStore(tmp_path / "harness-tools.sqlite")
    try:
        with MCPTestKit(store=tool_store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
            for case in tool_matrix.cases():
                result = case.run(
                    json.dumps(
                        {
                            "server": case.server.name,
                            "tool": case.tool.name,
                            "arguments": {"text": case.id},
                        }
                    ),
                    kit=kit,
                    timeout=20,
                )
                assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
                assert result.trace_view.tool_calls
                assert result.snapshot.execution_id
                spec = tool_store.get_execution_spec(result.snapshot.execution_id)
                assert spec is not None
                assert isinstance(spec.tool_policy, RestrictiveToolPolicy)
                assert spec.tool_policy.allowed_tools == (
                    f"{case.server.name}:{case.tool.name}",
                )
        _assert_processes_exited(markers["catalog"])
        _assert_processes_exited(markers["warehouse"])
    finally:
        tool_store.close()


def test_harness_matrix_all_servers_chains_real_turns_and_persists_one_execution(
    tmp_path: Path,
) -> None:
    markers = {name: tmp_path / f"{name}.jsonl" for name in ("catalog", "warehouse")}
    servers = (
        _server_case("catalog", markers["catalog"], (ToolCase(name="echo"),)),
        _server_case("warehouse", markers["warehouse"], (ToolCase(name="echo"),)),
    )
    store = SQLiteExecutionStore(tmp_path / "session.sqlite")
    matrix = HarnessMatrix.all_servers(servers=servers, harnesses=(_harness(),))
    try:
        with MCPTestKit(store=store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
            case = matrix.cases()[0]
            with case.session(kit=kit) as session:
                first = session.send(
                    json.dumps(
                        {
                            "server": "catalog",
                            "tool": "echo",
                            "arguments": {"text": "item-42"},
                        }
                    ),
                    timeout=20,
                )
                assert first.response is not None
                chained = first.response.text
                assert chained == "item-42"
                second = session.send(
                    json.dumps(
                        {
                            "server": "warehouse",
                            "tool": "echo",
                            "arguments": {"text": chained},
                        }
                    ),
                    timeout=20,
                )
            result = session.result
        assert first.snapshot.outcome is TurnOutcome.COMPLETED
        assert second.snapshot.outcome is TurnOutcome.COMPLETED
        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert len(result.turns) == 2
        expect(result).to_have_tool_call(
            "echo", turn=first, server="catalog", arguments={"text": "item-42"}
        )
        expect(result).to_have_tool_call(
            "echo", turn=second, server="warehouse", arguments={"text": chained}
        )
        assert any(
            call.get("arguments") == {"text": chained}
            for call in _calls(markers["warehouse"])
        )
        report = store.get_report(result.snapshot.execution_id)
        assert report is not None
        assert sum(event.kind.value == "turn.created" for event in report.events) == 2
        spec = store.get_execution_spec(result.snapshot.execution_id)
        assert spec is not None
        assert spec.metadata["m3.matrix.mode"] == "all_servers"
        assert spec.metadata["m3.matrix.servers"] == "catalog,warehouse"
        _assert_processes_exited(markers["catalog"])
        _assert_processes_exited(markers["warehouse"])
    finally:
        store.close()


@pytest.mark.asyncio
async def test_tool_matrix_async_run_helpers_use_real_processes_and_are_independent(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "async.jsonl"
    server = _server_case(
        "catalog", marker, (ToolCase(name="echo", arguments={"text": "async"}),)
    )
    matrix = ToolMatrix(servers=(server,))
    store = SQLiteExecutionStore(tmp_path / "async.sqlite")
    try:
        from m3.async_api import AsyncMCPTestKit

        async with AsyncMCPTestKit(
            store=store, env={}, cwd=str(_REPOSITORY_ROOT)
        ) as kit:
            first = await matrix.cases()[0].run_async(kit=kit)
            second = await matrix.cases()[0].run_async(kit=kit)
        assert first.snapshot.execution_id != second.snapshot.execution_id
        assert first.trace_view.tool_calls and second.trace_view.tool_calls
        assert len(_calls(marker)) == 2
        _assert_processes_exited(marker)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_harness_matrix_async_run_and_session_use_real_processes(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "async-harness.jsonl"
    server = _server_case("catalog", marker, (ToolCase(name="echo"),))
    case = HarnessMatrix.each_server(
        servers=(server,), harnesses=(_harness(),)
    ).cases()[0]
    store = SQLiteExecutionStore(tmp_path / "async-harness.sqlite")
    try:
        from m3.async_api import AsyncMCPTestKit

        async with AsyncMCPTestKit(
            store=store, env={}, cwd=str(_REPOSITORY_ROOT)
        ) as kit:
            run_result = await case.run_async(
                json.dumps(
                    {
                        "server": "catalog",
                        "tool": "echo",
                        "arguments": {"text": "async-run"},
                    }
                ),
                kit=kit,
                timeout=20,
            )
            assert run_result.snapshot.outcome is ExecutionOutcome.COMPLETED
            assert run_result.trace_view.tool_calls
            expect(run_result).to_have_tool_call(
                "echo",
                server="catalog",
                arguments={"text": "async-run"},
                status="success",
            )
            async with case.async_session(kit=kit) as session:
                turn = await session.send(
                    json.dumps(
                        {
                            "server": "catalog",
                            "tool": "echo",
                            "arguments": {"text": "async-session"},
                        }
                    ),
                    timeout=20,
                )
            session_result = session.result
        assert turn.snapshot.outcome is TurnOutcome.COMPLETED
        assert session_result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert session_result.trace_view.tool_calls
        expect(session_result).to_have_tool_call(
            "echo",
            turn=turn,
            server="catalog",
            arguments={"text": "async-session"},
            status="success",
        )
        spec = store.get_execution_spec(session_result.snapshot.execution_id)
        assert spec is not None
        assert spec.metadata["m3.matrix.mode"] == "each_server"
        assert spec.metadata["m3.matrix.case_id"] == case.id
        assert len(_calls(marker)) == 2
        _assert_processes_exited(marker)
    finally:
        store.close()
