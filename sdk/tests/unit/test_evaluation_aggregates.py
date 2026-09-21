from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import ClassVar

from m3 import (
    CallTool,
    DirectSpec,
    EvaluationDecision,
    EvaluationId,
    EvaluationQuery,
    EvaluationRecord,
    EvaluationRegistration,
    EvaluationResult,
    EvaluationSource,
    EvaluationStatus,
    ExecutionId,
    ExecutionOutcome,
    ExecutionState,
    ExecutionStatus,
    ProjectId,
    RunId,
    ServerBinding,
    StdioServer,
    TurnId,
)
from m3.aggregations import aggregate_evaluations
from m3.evaluations import EvaluationRunner
from m3.matrix import ServerCase, ToolCase, ToolMatrix
from m3.storage import InMemoryExecutionStore
from m3.storage.sqlite import SQLiteExecutionStore


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


def test_status_denominator_and_no_expected_results() -> None:
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
    assert values.expected_count == 5
    assert "measured_count" not in values.model_dump()
    assert values.pass_rate == 0.2
    assert values.status_counts["inconclusive"] == 1

    empty = InMemoryExecutionStore()
    assert empty.aggregate_evaluations(query).totals.pass_rate is None


def test_pagination_keeps_full_population_totals_and_non_additive_metrics() -> None:
    store = InMemoryExecutionStore()
    created = datetime(2026, 3, 1, tzinfo=timezone.utc)
    for execution in ("execution-1", "execution-2"):
        store.create(_snapshot(execution, created))
    _save(
        store,
        "execution-1",
        "quality-1",
        "quality.v1",
        EvaluationStatus.PASSED,
        score=0.0,
    )
    _save(
        store,
        "execution-2",
        "quality-2",
        "quality.v1",
        EvaluationStatus.PASSED,
        score=0.5,
    )
    _save(
        store,
        "execution-1",
        "quality-3",
        "quality.v2",
        EvaluationStatus.PASSED,
        score=1.0,
    )
    traces = {
        "execution-1": SimpleNamespace(
            summary=SimpleNamespace(
                timing=SimpleNamespace(duration_ms=100.0),
                successful_tool_call_count=1,
                failed_tool_call_count=0,
                protocol_error_count=0,
            ),
            tool_calls=(
                SimpleNamespace(server_latency_ms=SimpleNamespace(value=10.0)),
            ),
        ),
        "execution-2": SimpleNamespace(
            summary=SimpleNamespace(
                timing=SimpleNamespace(duration_ms=300.0),
                successful_tool_call_count=1,
                failed_tool_call_count=0,
                protocol_error_count=0,
            ),
            tool_calls=(
                SimpleNamespace(server_latency_ms=SimpleNamespace(value=30.0)),
            ),
        ),
    }

    query = EvaluationQuery(group_by=("evaluator",), limit=1, offset=1)
    report = aggregate_evaluations(
        query,
        tuple(
            record
            for execution in ("execution-1", "execution-2")
            for record in store.evaluations(execution)
        ),
        snapshots={
            execution: store.get_snapshot(execution)
            for execution in ("execution-1", "execution-2")
        },
        traces=traces,
    )

    assert report.total_groups == 2
    assert len(report.groups) == 1
    assert report.groups[0].key == {"evaluator": "quality.v2"}
    assert report.totals.trial_count == 2
    assert report.totals.evaluation_count == 3
    assert report.totals.average_score == 0.5
    assert report.totals.health.execution_duration_ms.p50 == 200.0
    assert report.totals.health.execution_duration_ms.p95 == 290.0
    assert report.totals.health.server_latency_ms.p50 == 20.0
    assert report.totals.health.server_latency_ms.p95 == 29.0
    assert report.groups[0].values.average_score == 1.0
    assert report.groups[0].values.health.execution_duration_ms.p50 == 100.0
    assert report.groups[0].values.health.execution_duration_ms.p95 == 100.0
    assert report.groups[0].values.health.server_latency_ms.p50 == 10.0
    assert report.groups[0].values.health.server_latency_ms.p95 == 10.0


def test_required_expectations_are_pending_live_and_missing_after_terminal() -> None:
    created = datetime(2026, 3, 2, tzinfo=timezone.utc)
    spec = SimpleNamespace(
        evaluations=(SimpleNamespace(name="quality.v1", required=True),),
        metadata={},
        case_id="case-required",
        suite_name=None,
        kind="direct",
        servers=(),
        operation=None,
        harness=None,
        model_dump=lambda **_kwargs: {"kind": "direct", "case_id": "case-required"},
    )
    query = EvaluationQuery(
        group_by=("time.day", "harness", "evaluator"),
        filters={"evaluator": "quality.v1"},
    )
    running = SimpleNamespace(
        created_at=created, lifecycle=SimpleNamespace(value="running")
    )
    report = aggregate_evaluations(
        query,
        (),
        snapshots={"required-execution": running},
        specifications={"required-execution": spec},
    )
    values = report.groups[0].values
    assert values.trial_count == 1
    assert values.expected_count == 0
    assert values.pending_required_count == 1
    assert values.missing_required_count == 0
    assert values.pass_rate is None
    assert report.groups[0].key["time.day"] == "2026-03-02"

    finished = SimpleNamespace(
        created_at=created, lifecycle=SimpleNamespace(value="finished")
    )
    report = aggregate_evaluations(
        query,
        (),
        snapshots={"required-execution": finished},
        specifications={"required-execution": spec},
    )
    values = report.groups[0].values
    assert values.trial_count == 1
    assert values.expected_count == 1
    assert values.pending_required_count == 0
    assert values.missing_required_count == 1
    assert values.pass_rate == 0

    still_running = aggregate_evaluations(
        query,
        (),
        snapshots={"required-execution": finished},
        specifications={"required-execution": spec},
        attempt_states={"required-execution": "running"},
    )
    assert still_running.totals.expected_count == 0
    assert still_running.totals.pending_required_count == 1

    terminal_attempt_running_execution = aggregate_evaluations(
        query,
        (),
        snapshots={"required-execution": running},
        specifications={"required-execution": spec},
        attempt_states={"required-execution": "passed"},
    )
    assert terminal_attempt_running_execution.totals.expected_count == 0
    assert terminal_attempt_running_execution.totals.pending_required_count == 1


def test_required_expectations_time_bounds_match_memory_and_sqlite(tmp_path) -> None:
    spec = DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(name="echo", command="echo"),
            ),
        ),
        operation=CallTool(name="ping", server="echo"),
        evaluations=(EvaluationRegistration(name="quality.v1", required=True),),
    )
    before = datetime(2026, 3, 1, tzinfo=timezone.utc)
    inside = datetime(2026, 3, 2, tzinfo=timezone.utc)
    after = datetime(2026, 3, 3, tzinfo=timezone.utc)
    query = EvaluationQuery(
        from_=inside,
        to=after,
        group_by=("evaluator",),
        filters={"evaluator": "quality.v1"},
    )

    def state(name: str, created_at: datetime, lifecycle: ExecutionStatus):
        finished = lifecycle is ExecutionStatus.FINISHED
        outcome = ExecutionOutcome.COMPLETED if finished else None
        return _snapshot(name, created_at).model_copy(
            update={
                "lifecycle": lifecycle,
                "outcome": outcome,
                "finished_at": created_at if finished else None,
            }
        )

    executions = (
        state("outside-missing", before, ExecutionStatus.FINISHED),
        state("inside-missing", inside, ExecutionStatus.FINISHED),
        state("outside-pending", before, ExecutionStatus.RUNNING_TURN),
        state("inside-pending", inside, ExecutionStatus.RUNNING_TURN),
    )
    stores = (
        InMemoryExecutionStore(),
        SQLiteExecutionStore(tmp_path / "aggregate-time-bounds.sqlite"),
    )
    reports = []
    for store in stores:
        try:
            for execution in executions:
                store.create(execution, specification=spec.model_dump(mode="json"))
            reports.append(store.aggregate_evaluations(query))
        finally:
            store.close()

    for report in reports:
        assert report.totals.trial_count == 2
        assert report.totals.expected_count == 1
        assert report.totals.missing_required_count == 1
        assert report.totals.pending_required_count == 1
        assert report.totals.pass_rate == 0
    assert reports[0].totals.model_dump(mode="json") == reports[1].totals.model_dump(
        mode="json"
    )


def test_store_attempt_state_keeps_finished_execution_requirement_pending(
    tmp_path,
) -> None:
    created = datetime(2026, 3, 2, tzinfo=timezone.utc)
    spec = DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(name="echo", command="echo"),
            ),
        ),
        operation=CallTool(name="ping", server="echo"),
        evaluations=(EvaluationRegistration(name="quality.v1", required=True),),
    )
    query = EvaluationQuery(
        group_by=("evaluator",), filters={"evaluator": "quality.v1"}
    )
    finished = _snapshot("attempt-linked-execution", created).model_copy(
        update={
            "lifecycle": ExecutionStatus.FINISHED,
            "outcome": ExecutionOutcome.COMPLETED,
            "finished_at": created,
        }
    )
    stores = (
        InMemoryExecutionStore(),
        SQLiteExecutionStore(tmp_path / "attempt-state.sqlite"),
    )
    for store in stores:
        store.create(
            finished,
            specification=spec.model_dump(mode="json"),
        )
        store.save_test_result(
            "run-1",
            "attempt-1",
            {"outcome": "running", "execution_ids": ["attempt-linked-execution"]},
        )
        store.save_test_result(
            "run-1",
            "attempt-2",
            {"outcome": "passed", "execution_ids": ["attempt-linked-execution"]},
        )
        values = store.aggregate_evaluations(query).totals
        assert values.pending_required_count == 1
        store.close()


def test_evaluation_status_grouping_excludes_synthetic_requirements() -> None:
    created = datetime(2026, 3, 3, tzinfo=timezone.utc)
    snapshot = SimpleNamespace(
        created_at=created, lifecycle=SimpleNamespace(value="finished")
    )
    spec = SimpleNamespace(
        evaluations=(SimpleNamespace(name="quality.v1", required=True),),
        metadata={},
        case_id="case-required",
        suite_name=None,
        kind="direct",
        servers=(),
        operation=None,
        harness=None,
        model_dump=lambda **_kwargs: {"kind": "direct"},
    )
    report = aggregate_evaluations(
        EvaluationQuery(
            group_by=("evaluation_status", "evaluator"),
            filters={"evaluator": "quality.v1"},
        ),
        (),
        snapshots={"required-execution": snapshot},
        specifications={"required-execution": spec},
    )
    assert report.total_groups == 0
    assert report.totals.expected_count == 0
    assert report.totals.missing_required_count == 0


def test_mapping_spec_fields_are_used_for_evaluation_labels() -> None:
    created = datetime(2026, 3, 3, tzinfo=timezone.utc)
    spec = {
        "kind": "agent",
        "case_id": "mapping-case",
        "suite_name": "mapping-suite",
        "metadata": {"owner": "mapping-owner"},
        "servers": (
            {
                "alias": "catalog",
                "server": {"name": "catalog", "kind": "stdio"},
            },
        ),
        "operation": {"name": "lookup"},
        "harness": {
            "name": "mapping-harness",
            "kind": "agent",
            "model": "mapping-model",
        },
    }
    record = EvaluationRecord(
        evaluation_id="mapping-evaluation",
        execution_id="mapping-execution",
        name="quality.v1",
        status=EvaluationStatus.PASSED,
        created_at=created,
    )
    report = aggregate_evaluations(
        EvaluationQuery(
            group_by=(
                "case_id",
                "suite_name",
                "execution_kind",
                "server",
                "tool",
                "transport",
                "harness",
                "model",
                "metadata.owner",
                "evaluator",
            ),
            filters={"evaluator": "quality.v1"},
        ),
        (record,),
        snapshots={
            "mapping-execution": SimpleNamespace(
                created_at=created, lifecycle=SimpleNamespace(value="finished")
            )
        },
        specifications={"mapping-execution": spec},
    )

    assert report.total_groups == 1
    assert report.groups[0].key == {
        "case_id": "mapping-case",
        "suite_name": "mapping-suite",
        "execution_kind": "agent",
        "server": "catalog",
        "tool": "lookup",
        "transport": "stdio",
        "harness": "mapping-harness",
        "model": "mapping-model",
        "metadata.owner": "mapping-owner",
        "evaluator": "quality.v1",
    }


def test_mapping_spec_required_expectation_preserves_labels_and_metadata() -> None:
    created = datetime(2026, 3, 3, tzinfo=timezone.utc)
    report = aggregate_evaluations(
        EvaluationQuery(
            group_by=("case_id", "suite_name", "metadata.owner", "evaluator"),
            filters={"evaluator": "quality.v1"},
        ),
        (),
        snapshots={
            "mapping-execution": SimpleNamespace(
                created_at=created, lifecycle=SimpleNamespace(value="finished")
            )
        },
        specifications={
            "mapping-execution": {
                "case_id": "mapping-case",
                "suite_name": "mapping-suite",
                "metadata": {"owner": "mapping-owner"},
                "evaluations": ({"name": "quality.v1", "required": True},),
            }
        },
    )

    assert report.total_groups == 1
    assert report.groups[0].key == {
        "case_id": "mapping-case",
        "suite_name": "mapping-suite",
        "metadata.owner": "mapping-owner",
        "evaluator": "quality.v1",
    }
    assert report.groups[0].values.missing_required_count == 1


def test_sqlite_pending_group_uses_project_and_trace_labels_and_health(
    tmp_path,
) -> None:
    created = datetime(2026, 3, 3, tzinfo=timezone.utc)
    project_id = ProjectId("11111111-1111-4111-8111-111111111111")
    spec = DirectSpec(
        project_id=project_id,
        servers=(
            ServerBinding(
                server=StdioServer(name="echo", command="echo"),
            ),
        ),
        operation=CallTool(name="ping", server="echo"),
        evaluations=(EvaluationRegistration(name="quality.v1", required=True),),
    )
    store = SQLiteExecutionStore(tmp_path / "pending-trace.sqlite")
    store.ensure_project(project_id.root, "Acme")
    store.create(
        _snapshot("pending-trace", created).model_copy(
            update={"project_id": project_id}
        ),
        specification=spec.model_dump(mode="json"),
    )
    store.get_trace_view = lambda _execution_id: SimpleNamespace(  # type: ignore[method-assign]
        runtime=SimpleNamespace(kind="agent"),
        summary=SimpleNamespace(
            timing=SimpleNamespace(duration_ms=12),
            successful_tool_call_count=1,
            failed_tool_call_count=0,
            protocol_error_count=0,
        ),
        tool_calls=(SimpleNamespace(server_latency_ms=SimpleNamespace(value=3)),),
    )
    report = store.aggregate_evaluations(
        EvaluationQuery(
            group_by=("project_name", "harness", "evaluator"),
            filters={"evaluator": "quality.v1"},
        )
    )
    assert report.total_groups == 1
    group = report.groups[0]
    assert group.key["project_name"] == "Acme"
    assert group.key["harness"] == "agent"
    assert group.values.pending_required_count == 1
    assert group.values.health.execution_count == 1
    assert group.values.health.tool_calls.total == 1
    store.close()


def test_in_memory_pending_group_uses_trace_labels_and_health() -> None:
    created = datetime(2026, 3, 3, tzinfo=timezone.utc)
    spec = DirectSpec(
        servers=(
            ServerBinding(
                server=StdioServer(name="echo", command="echo"),
            ),
        ),
        operation=CallTool(name="ping", server="echo"),
        evaluations=(EvaluationRegistration(name="quality.v1", required=True),),
    )
    store = InMemoryExecutionStore()
    store.create(
        _snapshot("in-memory-pending-trace", created),
        specification=spec.model_dump(mode="json"),
    )
    store.get_trace_view = lambda _execution_id: SimpleNamespace(  # type: ignore[method-assign]
        runtime=SimpleNamespace(kind="agent"),
        summary=SimpleNamespace(
            timing=SimpleNamespace(duration_ms=12),
            successful_tool_call_count=1,
            failed_tool_call_count=0,
            protocol_error_count=0,
        ),
        tool_calls=(SimpleNamespace(server_latency_ms=SimpleNamespace(value=3)),),
    )
    report = store.aggregate_evaluations(
        EvaluationQuery(
            group_by=("harness", "evaluator"),
            filters={"evaluator": "quality.v1"},
        )
    )
    assert report.total_groups == 1
    group = report.groups[0]
    assert group.key["harness"] == "agent"
    assert group.values.pending_required_count == 1
    assert group.values.health.execution_count == 1
    assert group.values.health.tool_calls.total == 1


def test_required_history_does_not_change_latest_identity_or_pass_numerator() -> None:
    created = datetime(2026, 3, 4, tzinfo=timezone.utc)
    records = (
        EvaluationRecord(
            evaluation_id="required-old",
            execution_id="required-lineage",
            name="quality.v1",
            status=EvaluationStatus.FAILED,
            required=True,
            subject_kind="json",
            subject_digest="a" * 64,
            created_at=created,
        ),
        EvaluationRecord(
            evaluation_id="advisory-new",
            execution_id="required-lineage",
            name="quality.v1",
            status=EvaluationStatus.PASSED,
            required=False,
            subject_kind="json",
            subject_digest="a" * 64,
            created_at=created + timedelta(seconds=1),
        ),
    )
    report = aggregate_evaluations(
        EvaluationQuery(group_by=("evaluator",), filters={"evaluator": "quality.v1"}),
        records,
        snapshots={"required-lineage": SimpleNamespace(created_at=created)},
    )
    assert report.totals.evaluation_count == 1
    assert report.totals.expected_count == 1
    assert report.totals.status_counts["passed"] == 1
    assert report.totals.pass_rate == 1


def test_multi_evaluator_totals_use_the_full_expected_denominator_and_empty_labels_are_valid() -> (
    None
):
    store = InMemoryExecutionStore()
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    store.create(_snapshot("execution-a", created))
    _save(store, "execution-a", "evaluation-a", "quality.v1", EvaluationStatus.PASSED)
    _save(store, "execution-a", "evaluation-b", "judge.v1", EvaluationStatus.FAILED)
    report = store.aggregate_evaluations(EvaluationQuery(group_by=("evaluator",)))
    assert report.totals.pass_rate == 0.5
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
                    "m3.matrix.matrix_id": "matrix-1",
                    "m3.matrix.cell_id": "server/tool",
                    "m3.matrix.case_id": "server/tool/trial-1",
                    "m3.matrix.trial": 1,
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
