from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp_pal.execution_trace import ExecutionTraceRecorder
from mcp_pal.storage import InMemoryExecutionStore, SQLiteExecutionStore
from mcp_pal.types import (
    CallTool,
    DirectSpec,
    EventKind,
    ExecutionId,
    ExecutionState,
    ProjectId,
    ServerBinding,
    StdioServer,
)

PROJECT = ProjectId("11111111-1111-4111-8111-111111111111")


def test_trace_recorder_preserves_project_through_events_and_sqlite_filter(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "project.sqlite")
    store.ensure_project(PROJECT.root, "Orders")
    recorder = ExecutionTraceRecorder(
        store,
        ExecutionId("execution-project"),
        specification={"project_id": PROJECT.root},
    )
    assert store.get_snapshot("execution-project").project_id == PROJECT
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "queued"})
    assert store.get_snapshot("execution-project").project_id == PROJECT
    assert store.list_executions(project_id=PROJECT.root).total == 1
    store.close()


def test_in_memory_event_rebuild_preserves_project() -> None:
    store = InMemoryExecutionStore()
    store.create(
        ExecutionState(execution_id=ExecutionId("memory-project"), project_id=PROJECT)
    )
    recorder = ExecutionTraceRecorder(
        store,
        ExecutionId("memory-project"),
    )
    recorder.emit(EventKind.EXECUTION_STATE_CHANGED, payload={"lifecycle": "queued"})
    assert store.get_snapshot("memory-project").project_id == PROJECT


def test_in_memory_aggregate_uses_registered_project_name() -> None:
    store = InMemoryExecutionStore()
    store.ensure_project(PROJECT.root, "Orders")
    store.create(
        ExecutionState(
            execution_id=ExecutionId("aggregate-project"), project_id=PROJECT
        )
    )
    store.save_evaluation(
        "aggregate-project",
        {
            "evaluation_id": "evaluation-project",
            "name": "quality",
            "status": "passed",
            "score": 1.0,
        },
    )
    report = store.aggregate_evaluations(
        {"filters": {"evaluator": ("quality",)}, "group_by": ("project_name",)}
    )
    assert report.groups[0].key["project_name"] == "Orders"
    filtered = store.aggregate_evaluations(
        {
            "filters": {"evaluator": ("quality",), "project_name": ("Orders",)},
            "group_by": ("project_name",),
        }
    )
    assert filtered.total_groups == 1


def test_kit_run_persists_project_column_and_filter(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "run-project.sqlite")
    store.ensure_project(PROJECT.root, "Orders")
    # A valid submission shape is enough to exercise the persisted project path;
    # the missing command produces a terminal failed execution deterministically.
    spec = DirectSpec(
        project_id=PROJECT,
        suite_name="orders",
        servers=(
            ServerBinding(
                server=StdioServer(name="missing", command="mcp-pal-no-such-server"),
                alias="missing",
            ),
        ),
        operation=CallTool(server="missing", name="echo", arguments={}),
        timeout_seconds=0.1,
    )
    from mcp_pal import MCPTestKit

    with MCPTestKit(store=store) as kit:
        result = kit.run(spec)
    assert result.snapshot.project_id == PROJECT
    assert store.list_executions(project_id=PROJECT.root).total == 1
    with store._connect() as connection:
        assert (
            connection.execute(
                "select project_id from v2_executions where id=?",
                (result.snapshot.execution_id.root,),
            ).fetchone()[0]
            == PROJECT.root
        )
        assert (
            connection.execute(
                "select project_id from v2_suites where suite_name=?",
                ("orders",),
            ).fetchone()[0]
            == PROJECT.root
        )
    store.close()


def test_kit_run_registers_project_on_fresh_store(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "fresh-project.sqlite")
    spec = DirectSpec(
        project_id=PROJECT,
        servers=(
            ServerBinding(
                server=StdioServer(name="missing", command="mcp-pal-no-such-server"),
                alias="missing",
            ),
        ),
        operation=CallTool(server="missing", name="echo", arguments={}),
        timeout_seconds=0.1,
    )
    from mcp_pal import MCPTestKit

    with MCPTestKit(store=store) as kit:
        result = kit.run(spec)
    assert store.get_project(PROJECT.root) == (PROJECT.root, PROJECT.root)
    assert (
        store.list_executions(project_id=PROJECT.root).items[0].execution_id
        == result.snapshot.execution_id
    )
    store.close()


def test_sync_and_async_kits_preserve_existing_project_name(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "existing-project.sqlite")
    store.ensure_project(PROJECT.root, "Orders")
    from mcp_pal import MCPTestKit
    from mcp_pal.async_api import AsyncMCPTestKit

    with MCPTestKit(store=store, project_id=PROJECT):
        assert store.get_project(PROJECT.root) == (PROJECT.root, "Orders")

    async def check_async() -> None:
        async with AsyncMCPTestKit(store=store, project_id=PROJECT):
            assert store.get_project(PROJECT.root) == (PROJECT.root, "Orders")

    asyncio.run(check_async())
    store.close()


def test_kit_direct_trace_retains_project(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "direct-project.sqlite")
    store.ensure_project(PROJECT.root, "Orders")
    from mcp_pal import MCPTestKit

    with MCPTestKit(store=store, project_id=PROJECT) as kit:
        client = kit.direct(
            StdioServer(
                name="echo",
                command=sys.executable,
                args=("-m", "mcp_pal.fixtures.echo_server"),
            )
        )
        with client:
            client.initialize()
            client.list_tools()
    snapshots = store.list_executions(project_id=PROJECT.root)
    assert snapshots.total == 1
    assert snapshots.items[0].project_id == PROJECT
    store.close()
