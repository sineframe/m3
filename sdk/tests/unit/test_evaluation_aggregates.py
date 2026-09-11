from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import ClassVar

from mcp_pal import (
    EvaluationDecision,
    EvaluationId,
    EvaluationQuery,
    EvaluationRecord,
    EvaluationResult,
    EvaluationSource,
    EvaluationStatus,
    ExecutionId,
    ExecutionState,
    RunId,
    StdioServer,
    TurnId,
)
from mcp_pal.aggregations import aggregate_evaluations
from mcp_pal.evaluations import EvaluationRunner
from mcp_pal.matrix import ServerCase, ToolCase, ToolMatrix
from mcp_pal.storage import InMemoryExecutionStore
from mcp_pal.storage.sqlite import SQLiteExecutionStore


def _snapshot(name: str, created_at: datetime) -> ExecutionState:
    return ExecutionState(
        execution_id=ExecutionId(name), run_id=RunId("run-1"), created_at=created_at
    )


def test_memory_aggregate_uses_execution_time_and_deduplicates_latest_result() -> None:
    store = InMemoryExecutionStore()
    when = datetime(2026, 1, 2, tzinfo=timezone.utc)
    store.create(_snapshot("execution-1", when))
    runner = EvaluationRunner(durable_store=store)
    status = [EvaluationStatus.PASSED]
    runner.register(
        "quality.v1",
        lambda _context: EvaluationDecision(
            status=status[0], score=0.8 if status[0] is EvaluationStatus.PASSED else 0.2
        ),
    )
    runner.evaluate(
        {},
        "quality.v1",
        execution_id="execution-1",
        case_id="case-1",
        evaluation_id="old",
    )
    status[0] = EvaluationStatus.FAILED
    runner.evaluate(
        {},
        "quality.v1",
        execution_id="execution-1",
        case_id="case-1",
        evaluation_id="new",
    )

    query = EvaluationQuery(
        from_=when - timedelta(seconds=1),
        to=when + timedelta(seconds=1),
        group_by=("case_id", "evaluator"),
        filters={"evaluator": "quality.v1"},
    )
    values = store.aggregate_evaluations(query).groups[0].values
    assert values.trial_count == 1
    assert values.evaluation_count == 1
    assert values.status_counts["failed"] == 1
    assert values.pass_rate == 0
    passed_only = store.aggregate_evaluations(
        EvaluationQuery(
            group_by=("evaluator",),
            filters={"evaluator": "quality.v1", "evaluation_status": "passed"},
        )
    )
    assert passed_only.total_groups == 0


def test_sqlite_aggregate_survives_reopen_and_uses_run_filter(tmp_path) -> None:
    database = tmp_path / "aggregate.sqlite"
    store = SQLiteExecutionStore(database)
    created = datetime(2026, 2, 1, tzinfo=timezone.utc)
    store.create(_snapshot("execution-1", created))
    runner = EvaluationRunner(durable_store=store)
    runner.register("quality.v1", lambda _context: EvaluationStatus.PASSED)
    runner.evaluate({}, "quality.v1", execution_id="execution-1", case_id="case-1")
    store.close()

    reopened = SQLiteExecutionStore(database)
    trace_calls = []
    reopened.get_trace_view = lambda execution_id: (
        trace_calls.append(str(execution_id)),
        None,
    )[1]  # type: ignore[method-assign]
    report = reopened.aggregate_evaluations(
        EvaluationQuery(
            filters={"evaluator": "quality.v1", "run_id": "run-1"},
            group_by=("time.day", "evaluator"),
        )
    )
    assert report.total_groups == 1
    assert report.groups[0].key["time.day"] == "2026-02-01"
    assert report.groups[0].values.pass_rate == 1
    assert trace_calls == ["execution-1"]
    memory = InMemoryExecutionStore()
    memory.create(_snapshot("execution-1", created))
    _save(
        memory,
        "execution-1",
        "memory-evaluation",
        "quality.v1",
        EvaluationStatus.PASSED,
        case_id="case-1",
    )
    memory_report = memory.aggregate_evaluations(
        EvaluationQuery(
            filters={"evaluator": "quality.v1", "run_id": "run-1"},
            group_by=("time.day", "evaluator"),
        )
    )
    assert memory_report.totals.model_dump(mode="json") == report.totals.model_dump(
        mode="json"
    )
    reopened.close()


def _save(
    store,
    execution: str,
    evaluation: str,
    name: str,
    status: EvaluationStatus,
    *,
    case_id: str = "case",
    subject=None,
    metadata=None,
    score: float | None = None,
) -> None:
    store.save_evaluation(
        execution,
        EvaluationResult(
            evaluation_id=EvaluationId(evaluation),
            name=name,
            status=status,
            score=score,
            context={
                "execution_id": execution,
                "case_id": case_id,
                "subject": subject,
                "metadata": metadata or {},
            },
        ),
    )


def test_status_denominator_and_no_measured_results() -> None:
    store = InMemoryExecutionStore()
    created = datetime(2026, 3, 1, tzinfo=timezone.utc)
    statuses = (
        EvaluationStatus.PASSED,
        EvaluationStatus.FAILED,
        EvaluationStatus.INCONCLUSIVE,
        EvaluationStatus.ERROR,
        EvaluationStatus.NOT_RUN,
    )
    for index, status in enumerate(statuses):
        execution = f"execution-{index}"
        store.create(_snapshot(execution, created))
        _save(store, execution, f"evaluation-{index}", "quality.v1", status)
    query = EvaluationQuery(
        group_by=("evaluator",), filters={"evaluator": "quality.v1"}
    )
    values = store.aggregate_evaluations(query).totals
    assert values.measured_count == 2
    assert values.pass_rate == 0.5
    assert values.status_counts["inconclusive"] == 1

    empty = InMemoryExecutionStore()
    assert empty.aggregate_evaluations(query).totals.pass_rate is None


def test_multi_evaluator_totals_are_not_a_single_rate_and_empty_labels_are_valid() -> (
    None
):
    store = InMemoryExecutionStore()
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    store.create(_snapshot("execution-a", created))
    _save(store, "execution-a", "evaluation-a", "quality.v1", EvaluationStatus.PASSED)
    _save(store, "execution-a", "evaluation-b", "judge.v1", EvaluationStatus.FAILED)
    report = store.aggregate_evaluations(EvaluationQuery(group_by=("evaluator",)))
    assert report.totals.pass_rate is None
    assert report.totals.average_score is None
    assert report.total_groups == 2
    empty = store.aggregate_evaluations(
        EvaluationQuery(
            group_by=("transport", "evaluator"), filters={"evaluator": "missing"}
        )
    )
    assert empty.total_groups == 0


def test_metadata_filter_is_available_as_a_query_label() -> None:
    store = InMemoryExecutionStore()
    created = datetime(2026, 4, 2, tzinfo=timezone.utc)
    store.create(_snapshot("metadata-execution", created))
    _save(
        store,
        "metadata-execution",
        "metadata-evaluation",
        "quality.v1",
        EvaluationStatus.PASSED,
        metadata={"environment": "staging"},
    )
    report = store.aggregate_evaluations(
        EvaluationQuery(
            group_by=("metadata.environment", "evaluator"),
            filters={"evaluator": "quality.v1", "metadata.environment": "staging"},
        )
    )
    assert report.total_groups == 1


def test_query_rejects_duplicate_groups_empty_filters_and_multiple_time_buckets() -> (
    None
):
    for payload in (
        {"group_by": ("evaluator", "evaluator")},
        {"filters": {"evaluator": []}},
        {"group_by": ("time.day", "time.week")},
        {"group_by": ("evaluator",), "filters": {"time.day": "2026-01-01"}},
    ):
        try:
            EvaluationQuery.model_validate(payload)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid aggregate query was accepted")


def test_utc_hour_and_week_buckets_use_execution_time() -> None:
    store = InMemoryExecutionStore()
    for index, created in enumerate(
        (
            datetime(2026, 1, 4, 23, 30, tzinfo=timezone.utc),
            datetime(2026, 1, 5, 0, 30, tzinfo=timezone.utc),
        )
    ):
        execution = f"bucket-{index}"
        store.create(_snapshot(execution, created))
        _save(
            store,
            execution,
            f"bucket-evaluation-{index}",
            "quality.v1",
            EvaluationStatus.PASSED,
        )
    report = store.aggregate_evaluations(
        EvaluationQuery(
            group_by=("time.hour", "evaluator"), filters={"evaluator": "quality.v1"}
        )
    )
    assert {group.key["time.hour"] for group in report.groups} == {
        "2026-01-04T23:00:00Z",
        "2026-01-05T00:00:00Z",
    }
    weeks = store.aggregate_evaluations(
        EvaluationQuery(
            group_by=("time.week", "evaluator"), filters={"evaluator": "quality.v1"}
        )
    )
    assert {group.key["time.week"] for group in weeks.groups} == {
        "2025-12-29",
        "2026-01-05",
    }


def test_distinct_subjects_are_not_collapsed_and_spec_case_hash_is_stable() -> None:
    store = InMemoryExecutionStore()
    created = datetime(2026, 7, 1, tzinfo=timezone.utc)
    store.create(_snapshot("subject-execution", created))
    _save(
        store,
        "subject-execution",
        "subject-a",
        "quality.v1",
        EvaluationStatus.PASSED,
        case_id="explicit-case",
        subject={"answer": "a"},
    )
    _save(
        store,
        "subject-execution",
        "subject-b",
        "quality.v1",
        EvaluationStatus.FAILED,
        case_id="explicit-case",
        subject={"answer": "b"},
    )
    report = store.aggregate_evaluations(
        EvaluationQuery(
            group_by=("case_id", "evaluator"), filters={"evaluator": "quality.v1"}
        )
    )
    assert report.totals.evaluation_count == 2
    assert report.totals.trial_count == 1


def test_same_execution_subject_and_evaluator_stay_separate_by_turn() -> None:
    created = datetime(2026, 7, 1, tzinfo=timezone.utc)
    snapshots = {"turn-execution": SimpleNamespace(created_at=created, run_id="run")}
    records = tuple(
        EvaluationRecord(
            evaluation_id=f"turn-evaluation-{number}",
            execution_id="turn-execution",
            turn_id=TurnId(f"turn-{number}"),
            name="quality.v1",
            status=EvaluationStatus.PASSED,
            subject_kind="json",
            subject_digest="a" * 64,
        )
        for number in (1, 2)
    )
    report = aggregate_evaluations(
        EvaluationQuery(group_by=("evaluator",), filters={"evaluator": "quality.v1"}),
        records,
        snapshots=snapshots,
    )
    assert report.totals.evaluation_count == 2


def test_spec_and_trace_case_fallbacks_are_stable_for_chained_and_empty_tool_shapes() -> (
    None
):
    created = datetime(2026, 7, 2, tzinfo=timezone.utc)
    spec_record = EvaluationRecord(
        evaluation_id="spec-evaluation",
        execution_id="spec-execution",
        name="quality.v1",
        status=EvaluationStatus.PASSED,
    )
    trace_record = EvaluationRecord(
        evaluation_id="trace-evaluation",
        execution_id="trace-execution",
        name="quality.v1",
        status=EvaluationStatus.PASSED,
    )
    snapshots = {
        "spec-execution": SimpleNamespace(created_at=created, run_id="run"),
        "trace-execution": SimpleNamespace(created_at=created, run_id="run"),
    }

    class SavedSpec:
        metadata: ClassVar[dict[str, str]] = {}
        kind = "direct"
        case_id = None
        servers = ()
        operation = None
        harness = None

        def model_dump(self, **_kwargs):
            return {"kind": "direct", "stable": "chain"}

    trace = SimpleNamespace(
        runtime=SimpleNamespace(
            kind="direct",
            transport=SimpleNamespace(state="observed", value="streamable_http"),
            model_id=SimpleNamespace(state="observed", value="model-a"),
        ),
        timeline=(SimpleNamespace(server_binding="deepwiki"),),
        tool_calls=(),
    )
    query = EvaluationQuery(
        group_by=("case_id", "evaluator"), filters={"evaluator": "quality.v1"}
    )
    spec_report = aggregate_evaluations(
        query,
        (spec_record,),
        snapshots=snapshots,
        specifications={"spec-execution": SavedSpec()},
    )
    trace_report = aggregate_evaluations(
        query, (trace_record,), snapshots=snapshots, traces={"trace-execution": trace}
    )
    assert spec_report.groups[0].key["case_id"].startswith("spec:")
    assert len(spec_report.groups[0].key["case_id"]) == 69
    assert trace_report.groups[0].key["case_id"].startswith("trace:")
    filtered = aggregate_evaluations(
        EvaluationQuery(
            group_by=("transport", "model", "evaluator"),
            filters={
                "evaluator": "quality.v1",
                "transport": "streamable_http",
                "model": "model-a",
            },
        ),
        (trace_record,),
        snapshots=snapshots,
        traces={"trace-execution": trace},
    )
    assert filtered.total_groups == 1


def test_matrix_repeated_trials_share_case_id() -> None:
    matrix = ToolMatrix(
        servers=(
            ServerCase(
                name="catalog",
                server=StdioServer(name="catalog", command="echo"),
                tools=(ToolCase(name="read", arguments={}),),
            ),
        ),
        trials=2,
    )
    cases = matrix.cases()
    first = cases[0]._spec(timeout=None, validate_schemas=False, metadata=None)
    second = cases[1]._spec(timeout=None, validate_schemas=False, metadata=None)
    assert first.case_id == second.case_id == f"{matrix.matrix_id}:catalog/read"


def test_health_is_counted_once_per_execution_for_each_group() -> None:
    store = InMemoryExecutionStore()
    created = datetime(2026, 5, 1, tzinfo=timezone.utc)
    store.create(_snapshot("execution-health", created))
    _save(
        store, "execution-health", "evaluation-a", "quality.v1", EvaluationStatus.PASSED
    )
    _save(
        store, "execution-health", "evaluation-b", "judge.v1", EvaluationStatus.PASSED
    )
    trace = SimpleNamespace(
        summary=SimpleNamespace(
            timing=SimpleNamespace(duration_ms=100.0),
            successful_tool_call_count=1,
            failed_tool_call_count=0,
            protocol_error_count=0,
        ),
        tool_calls=(SimpleNamespace(server_latency_ms=SimpleNamespace(value=20.0)),),
    )
    store.get_trace_view = lambda _execution_id: trace  # type: ignore[method-assign]
    report = store.aggregate_evaluations(EvaluationQuery(group_by=("evaluator",)))
    for group in report.groups:
        assert group.values.health.execution_count == 1
        assert group.values.health.tool_calls.total == 1
        assert group.values.health.execution_duration_ms.count == 1
        assert group.values.health.server_latency_ms.count == 1


def test_matrix_case_and_judge_labels_are_stable() -> None:
    store = InMemoryExecutionStore()
    created = datetime(2026, 6, 1, tzinfo=timezone.utc)
    store.create(_snapshot("matrix-trial-1", created))
    store.save_evaluation(
        "matrix-trial-1",
        EvaluationResult(
            evaluation_id=EvaluationId("matrix-evaluation"),
            name="judge.v1",
            status=EvaluationStatus.PASSED,
            provenance=EvaluationSource(
                kind="llm_judge",
                provider="provider",
                model="judge-model",
                rubric_id="rubric",
            ),
            context={
                "execution_id": "matrix-trial-1",
                "metadata": {
                    "mcp_pal.matrix.matrix_id": "matrix-1",
                    "mcp_pal.matrix.cell_id": "server/tool",
                    "mcp_pal.matrix.case_id": "server/tool/trial-1",
                    "mcp_pal.matrix.trial": 1,
                },
            },
        ),
    )
    group = store.aggregate_evaluations(
        EvaluationQuery(
            group_by=(
                "case_id",
                "matrix.id",
                "matrix.cell",
                "trial.number",
                "judge_provider",
                "judge_model",
                "rubric_id",
            ),
            filters={"evaluator": "judge.v1"},
        )
    ).groups[0]
    assert group.key["case_id"] == "matrix-1:server/tool"
    assert group.key["judge_provider"] == "provider"
    assert group.key["judge_model"] == "judge-model"
