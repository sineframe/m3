"""Focused tests for saved evaluations and stable IDs."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from m3 import EvaluationQuery, MCPTestKit
from m3.evaluations import EvaluationRunner, RequiredEvaluationError
from m3.pytest_plugin import _required_evaluation_issues
from m3.storage.sqlite import SQLiteExecutionStore
from m3.types import (
    EvaluationContext,
    EvaluationDecision,
    EvaluationId,
    EvaluationResult,
    EvaluationStatus,
    ExecutionId,
    ExecutionOutcome,
    ExecutionState,
    ExecutionStatus,
)


def _snapshot(identifier: str = "execution-v2") -> ExecutionState:
    return ExecutionState(
        execution_id=ExecutionId(identifier),
        created_at=datetime.now(timezone.utc),
    )


def test_structured_decision_is_compact_and_typed_after_sqlite_reopen(tmp_path) -> None:
    database = tmp_path / "evaluations.sqlite"
    store = SQLiteExecutionStore(database)
    store.create(_snapshot(), run_id="run-v2")
    assert store.list_executions(run_id="run-v2").total == 1
    runner = EvaluationRunner(durable_store=store)
    runner.register(
        "quality.v1",
        lambda context: EvaluationDecision(
            status=EvaluationStatus.PASSED,
            score=0.75,
            rationale="safe rationale",
            metrics={"quality": 0.75},
        ),
    )
    result = runner.evaluate(
        {"secret": "should-not-be-duplicated"},
        "quality.v1",
        execution_id="execution-v2",
        goal="evaluate output",
    )
    assert result.score == 0.75
    record = store.evaluations("execution-v2")[0]
    assert record.execution_id == ExecutionId("execution-v2")
    assert record.subject_kind == "json"
    assert record.subject_digest is not None and len(record.subject_digest) == 64
    raw = store.evaluation_json("execution-v2")[0]
    assert "should-not-be-duplicated" not in repr(raw)
    store.close()
    reopened = SQLiteExecutionStore(database)
    assert reopened.evaluations("execution-v2")[0].evaluation_id == result.evaluation_id
    reopened.close()


def test_reopened_runner_preserves_dynamic_required_lineage(tmp_path) -> None:
    database = tmp_path / "required-lineage.sqlite"
    store = SQLiteExecutionStore(database)
    store.create(_snapshot("execution-required-lineage"), run_id="run-lineage")

    first = EvaluationRunner(durable_store=store)
    first.register("quality.v1", lambda _context: False)
    with pytest.raises(RequiredEvaluationError):
        first.evaluate(
            {"answer": "safe"},
            "quality.v1",
            required=True,
            execution_id="execution-required-lineage",
        )
    required_record = store.evaluations("execution-required-lineage")[0]
    assert required_record.required is True
    assert required_record.subject_digest is not None
    store.close()

    reopened = SQLiteExecutionStore(database)
    second = EvaluationRunner(durable_store=reopened)
    second.register("quality.v1", lambda _context: True)
    rerun = second.evaluate(
        {"answer": "safe"},
        "quality.v1",
        execution_id="execution-required-lineage",
    )
    assert rerun.required is True
    attempt = (
        {
            "node_id": "test.py::lineage",
            "outcome": "passed",
            "execution_ids": ["execution-required-lineage"],
        },
    )
    assert _required_evaluation_issues(reopened, "run-lineage", attempt) == ()

    third = EvaluationRunner(durable_store=reopened)
    third.register("quality.v1", lambda _context: False)
    with pytest.raises(RequiredEvaluationError):
        third.evaluate(
            {"answer": "safe"},
            "quality.v1",
            execution_id="execution-required-lineage",
        )
    assert _required_evaluation_issues(reopened, "run-lineage", attempt) == (
        "required evaluation did not pass execution-required-lineage:quality.v1",
    )
    reopened.close()


def test_existing_evaluation_schema_is_migrated_before_indexes(tmp_path) -> None:
    database = tmp_path / "old.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        "CREATE TABLE v2_executions (id TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL, "
        "specification_json TEXT, provenance_json TEXT, parent_execution_id TEXT, "
        "created_at TEXT NOT NULL, deleted_at TEXT);"
        "CREATE TABLE v2_evaluations (id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, "
        "turn_id TEXT, result_json TEXT NOT NULL, created_at TEXT NOT NULL);"
    )
    connection.commit()
    connection.close()
    store = SQLiteExecutionStore(database)
    store.close()
    connection = sqlite3.connect(database)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(v2_evaluations)")
    }
    connection.close()
    assert {"evaluator_name", "status", "score", "run_id"} <= columns
    connection = sqlite3.connect(database)
    execution_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(v2_executions)")
    }
    connection.close()
    assert "deleted_at" in execution_columns


def test_legacy_evaluation_row_is_backfilled_and_malformed_row_is_skipped(
    tmp_path,
) -> None:
    database = tmp_path / "legacy-row.sqlite"
    store = SQLiteExecutionStore(database)
    execution_id = ExecutionId("legacy-execution")
    store.create(_snapshot("legacy-execution"), run_id="legacy-run")
    legacy = EvaluationResult(
        evaluation_id=EvaluationId("legacy-evaluation"),
        name="legacy.v1",
        status=EvaluationStatus.PASSED,
        context=EvaluationContext(execution_id=execution_id, subject={"answer": "ok"}),
    )
    store.close()
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO v2_evaluations(id,execution_id,turn_id,result_json,created_at,evaluator_name,status,score,run_id) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "legacy-evaluation",
            "legacy-execution",
            None,
            json.dumps(legacy.model_dump(mode="json")),
            datetime.now(timezone.utc).isoformat(),
            None,
            None,
            None,
            None,
        ),
    )
    connection.execute(
        "INSERT INTO v2_evaluations(id,execution_id,turn_id,result_json,created_at,evaluator_name,status,score,run_id) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "malformed-evaluation",
            "legacy-execution",
            None,
            "not-json",
            datetime.now(timezone.utc).isoformat(),
            None,
            None,
            None,
            None,
        ),
    )
    connection.commit()
    connection.close()
    reopened = SQLiteExecutionStore(database)
    try:
        records = reopened.evaluations(execution_id)
        assert len(records) == 1
        assert records[0].run_id.root == "legacy-run"
        assert records[0].subject_digest is not None
        assert "answer" not in repr(reopened.evaluation_json(execution_id)[0])
        aggregate = reopened.aggregate_evaluations(
            EvaluationQuery(group_by=("evaluator",), filters={"evaluator": "legacy.v1"})
        )
        assert aggregate.totals.trial_count == 1
    finally:
        reopened.close()


def test_kit_run_id_is_stable_and_explicit_store_is_independent() -> None:
    with (
        MCPTestKit(env={}, cwd="/tmp/m3-no-project") as first,
        MCPTestKit(env={}, cwd="/tmp/m3-no-project") as second,
    ):
        assert first.run_id != second.run_id
        from m3.types import RunId

        explicit = _snapshot_spec().model_copy(update={"run_id": RunId("caller-run")})
        handle = first.submit(explicit)
        assert handle.snapshot().run_id == RunId("caller-run")
        handle.cancel()


def test_recorder_propagates_spec_run_id_into_snapshot(tmp_path) -> None:
    from m3.execution_trace import ExecutionTraceRecorder
    from m3.storage.ephemeral import InMemoryExecutionStore

    store = InMemoryExecutionStore()
    recorder = ExecutionTraceRecorder(
        store,
        "execution-run-id",
        specification=_snapshot_spec()
        .model_copy(update={"run_id": "spec-run"})
        .model_dump(mode="json"),
    )
    assert store.get_snapshot(recorder.execution_id).run_id.root == "spec-run"


def _snapshot_spec():
    from m3.types import DirectSpec, Ping, ServerBinding, StdioServer

    return DirectSpec(
        servers=(ServerBinding(server=StdioServer(name="server", command="server")),),
        operation=Ping(server="server"),
    )


def test_builtins_use_redacted_mapping_view() -> None:
    runner = EvaluationRunner()
    completed = _snapshot("completed").model_copy(
        update={
            "lifecycle": ExecutionStatus.FINISHED,
            "outcome": ExecutionOutcome.COMPLETED,
            "finished_at": datetime.now(timezone.utc),
        }
    )
    from m3.types import ExecutionResult

    result = ExecutionResult(
        snapshot=completed, direct_result={"kind": "call_tool", "is_error": False}
    )
    assert (
        runner.evaluate(result, "m3.execution.completed.v1").status
        is EvaluationStatus.PASSED
    )
    assert (
        runner.evaluate(result.direct_result, "m3.tool_call.succeeded.v1").status
        is EvaluationStatus.PASSED
    )


def test_async_user_llm_evaluator_uses_structured_decision_without_network() -> None:
    async def evaluate_with_llm(context):
        assert context.subject == {"answer": "correct"}
        return EvaluationDecision(
            status=EvaluationStatus.PASSED,
            score=0.91,
            rationale="matches the rubric",
            metrics={"correctness": 0.91},
        )

    async def run():
        runner = EvaluationRunner()
        runner.register("project.user-llm-evaluator.v1", evaluate_with_llm)
        return await runner.evaluate_async(
            {"answer": "correct"}, "project.user-llm-evaluator.v1"
        )

    result = asyncio.run(run())
    assert result.status is EvaluationStatus.PASSED
    assert result.score == 0.91


def test_tool_matrix_trials_have_stable_ids_and_reserved_metadata() -> None:
    from m3.matrix import ServerCase, ToolCase, ToolMatrix
    from m3.types import StdioServer

    server = StdioServer(name="server", command="server")
    matrix = ToolMatrix(
        id="quality-matrix",
        servers=(
            ServerCase(
                name="server",
                server=server,
                tools=(ToolCase(name="echo", arguments={}),),
            ),
        ),
        trials=3,
    )
    cases = matrix.cases()
    assert [case.id for case in cases] == [
        "server/echo/trial-1",
        "server/echo/trial-2",
        "server/echo/trial-3",
    ]
    assert {case.matrix_id for case in cases} == {"quality-matrix"}
    assert [case.trial for case in cases] == [1, 2, 3]
    assert cases[0]._metadata(None)["m3.matrix.cell_id"] == "server/echo"


def test_direct_trace_bridge_persists_one_terminal_event_and_binding(tmp_path) -> None:
    from m3.direct_trace import DirectTraceBridge
    from m3.types import TransportKind

    store = SQLiteExecutionStore(tmp_path / "direct.sqlite")
    bridge = DirectTraceBridge(
        store=store,
        server_binding="configured-server",
        run_id="run-direct",
        server_bindings=(
            {
                "alias": "configured-server",
                "server": {"kind": "stdio", "name": "server", "command": "server"},
            },
        ),
    )
    bridge.record_transport_connected(TransportKind.STDIO)
    bridge.finalize(ExecutionOutcome.COMPLETED)
    events = store.events(bridge.execution_id)
    assert sum(event.kind.value == "execution.finished" for event in events) == 1
    assert store.get_snapshot(bridge.execution_id).run_id.root == "run-direct"
    assert events[1].server_binding == "configured-server"
    with store._connect() as connection:
        row = connection.execute(
            "SELECT binding_json FROM v2_execution_server_bindings WHERE execution_id=?",
            (bridge.execution_id.root,),
        ).fetchone()
    assert row is not None and "configured-server" in row[0]
    store.close()
