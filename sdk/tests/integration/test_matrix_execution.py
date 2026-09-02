"""Real execution coverage for matrix helpers."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from mcp_pal import MCPTestKit
from mcp_pal.matrix import HarnessCase, HarnessMatrix, ServerCase, ToolCase, ToolMatrix
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal.types import (
    ACPAgent,
    EventKind,
    ExecutionOutcome,
    StdioServer,
    TurnOutcome,
)


_SDK_ROOT = Path(__file__).parents[2]
_REPOSITORY_ROOT = _SDK_ROOT.parent
_FIXTURES = _SDK_ROOT / "tests" / "fixtures"
_MCP_SERVER = _FIXTURES / "matrix_stdio_server.py"
_ACP_HARNESS = _FIXTURES / "matrix_acp_harness.py"


def _server(name: str) -> StdioServer:
    return StdioServer(
        name=name,
        command=sys.executable,
        args=(str(_MCP_SERVER),),
        cwd=str(_REPOSITORY_ROOT),
    )


def _harness() -> ACPAgent:
    return ACPAgent(
        model="matrix-fixture",
        manifest={
            "schema_version": "mcp-pal.harness.v1",
            "protocol": "acp",
            "protocol_version": 1,
            "command": sys.executable,
            "args": [str(_ACP_HARNESS)],
            "env": {},
        },
    )


def _turn_numbers(store: SQLiteExecutionStore, execution_id: object) -> list[int]:
    return [
        int(event.payload["number"])
        for event in store.iter_events(execution_id)
        if event.kind is EventKind.TURN_CREATED
    ]


def test_tool_matrix_case_run_persists_one_normal_execution(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "tool-matrix.sqlite")
    try:
        matrix = ToolMatrix(
            servers=(
                ServerCase(
                    name="catalog",
                    server=_server("catalog"),
                    tools=(ToolCase(name="echo", arguments={"text": "matrix"}),),
                ),
            )
        )
        with MCPTestKit(store=store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
            result = matrix.cases()[0].run(kit=kit, validate_schemas=True)

        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert result.trace is not None
        report = store.get_report(result.snapshot.execution_id)
        assert report is not None
        assert report.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert report.evidence is not None
        assert sum(event.kind is EventKind.EXECUTION_CREATED for event in report.events) == 1
        assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in report.events) == 1
        spec = store.get_execution_spec(result.snapshot.execution_id)
        assert spec is not None
        assert spec.metadata["mcp_pal.matrix.case_id"] == "catalog/echo"
        assert spec.metadata["mcp_pal.matrix.kind"] == "tool"
        assert spec.metadata["mcp_pal.matrix.mode"] == "tool"
        assert spec.metadata["mcp_pal.matrix.servers"] == "catalog"
        assert spec.metadata["mcp_pal.matrix.tool"] == "echo"
    finally:
        store.close()


def test_harness_matrix_case_run_persists_one_normal_execution(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "harness-matrix.sqlite")
    try:
        matrix = HarnessMatrix.each_server(
            servers=(
                ServerCase(
                    name="catalog",
                    server=_server("catalog"),
                    tools=(ToolCase(name="echo"),),
                ),
            ),
            harnesses=(_harness_case(),),
        )
        with MCPTestKit(store=store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
            result = matrix.cases()[0].run(
                json.dumps({"server": "catalog", "tool": "echo", "arguments": {"text": "matrix"}}),
                kit=kit,
            )

        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert result.trace is not None
        report = store.get_report(result.snapshot.execution_id)
        assert report is not None
        assert sum(event.kind is EventKind.EXECUTION_CREATED for event in report.events) == 1
        assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in report.events) == 1
        spec = store.get_execution_spec(result.snapshot.execution_id)
        assert spec is not None
        assert spec.metadata["mcp_pal.matrix.case_id"] == "catalog/acp"
        assert spec.metadata["mcp_pal.matrix.kind"] == "harness"
        assert spec.metadata["mcp_pal.matrix.mode"] == "each_server"
        assert spec.metadata["mcp_pal.matrix.harness"] == "acp"
        assert spec.metadata["mcp_pal.matrix.trial"] == 1
    finally:
        store.close()


def _harness_case() -> HarnessCase:
    return HarnessCase(name="acp", harness=_harness())


def test_harness_matrix_session_persists_two_ordered_turns(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "harness-session.sqlite")
    try:
        servers = tuple(
            ServerCase(name=name, server=_server(name), tools=(ToolCase(name="echo"),))
            for name in ("catalog", "warehouse")
        )
        matrix = HarnessMatrix.all_servers(
            servers=servers,
            harnesses=(_harness_case(),),
        )
        with MCPTestKit(store=store, env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
            case = matrix.cases()[0]
            with case.session(kit=kit) as session:
                first = session.send(
                    json.dumps({"server": "catalog", "tool": "echo", "arguments": {"text": "first"}}),
                    timeout=20,
                )
                second = session.send(
                    json.dumps({"server": "warehouse", "tool": "echo", "arguments": {"text": "second"}}),
                    timeout=20,
                )
            result = session.result

        assert first.snapshot.outcome is TurnOutcome.COMPLETED
        assert second.snapshot.outcome is TurnOutcome.COMPLETED
        assert result.snapshot.outcome is ExecutionOutcome.COMPLETED
        assert _turn_numbers(store, result.snapshot.execution_id) == [1, 2]
        report = store.get_report(result.snapshot.execution_id)
        assert report is not None
        assert sum(event.kind is EventKind.EXECUTION_CREATED for event in report.events) == 1
        assert sum(event.kind is EventKind.EXECUTION_FINISHED for event in report.events) == 1
        spec = store.get_execution_spec(result.snapshot.execution_id)
        assert spec is not None
        assert spec.metadata["mcp_pal.matrix.mode"] == "all_servers"
        assert spec.metadata["mcp_pal.matrix.servers"] == "catalog,warehouse"
    finally:
        store.close()
