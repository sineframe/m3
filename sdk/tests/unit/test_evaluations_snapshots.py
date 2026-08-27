"""Phase 7 contracts for canonical snapshots and deterministic evaluations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from mcp_pal import MCPTestKit, canonical_snapshot, normalize_snapshot
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.errors import ModelValidationError, UnsupportedFeature
from mcp_pal.evaluations import EvaluationRunner, InMemoryEvaluationStore, RequiredEvaluationError
from mcp_pal.snapshots import SnapshotOptions
from mcp_pal.trace.redaction import RedactionConfig, REDACTED
from mcp_pal.types import ArtifactId, ArtifactRef, EvaluationStatus, ExecutionId, ExecutionOutcome, ExecutionResult, ExecutionSnapshot, LifecycleState, TraceId, TraceResult


class _SnapshotModel(BaseModel):
    z: int
    timestamp: str
    nested: dict[str, object]


def test_canonical_snapshot_is_json_compatible_sorted_redacted_and_stable() -> None:
    value = {
        "z": 2,
        "a": 1,
        "run_id": "run-unstable",
        "timestamp": "2026-01-01T00:00:00Z",
        "nested": {"trace_id": "trace-unstable", "message": "api-secret"},
    }
    projected = canonical_snapshot(
        value,
        config=RedactionConfig(secrets=frozenset({"api-secret"}), include_environment=False),
    )
    assert isinstance(projected, dict)
    assert list(projected) == ["a", "nested", "z"]
    assert projected["nested"] == {"message": REDACTED}
    assert "run_id" not in repr(projected)
    assert normalize_snapshot(value, config=RedactionConfig(secrets=frozenset({"api-secret"}), include_environment=False)) == projected


def test_snapshot_field_opt_in_restores_only_selected_unstable_fields() -> None:
    value = {"duration_ms": 4, "nested": {"trace_id": "trace-1", "duration_ms": 9}}
    projected = canonical_snapshot(
        value,
        options=SnapshotOptions(include_fields=frozenset({"duration_ms", "$.nested.trace_id"})),
        config=RedactionConfig(include_environment=False),
    )
    assert projected == {"duration_ms": 4, "nested": {"duration_ms": 9, "trace_id": "trace-1"}}


def test_snapshot_accepts_pydantic_models_without_leaking_omitted_fields() -> None:
    projected = canonical_snapshot(
        _SnapshotModel(z=2, timestamp="unstable", nested={"path": "/private", "value": 1}),
        config=RedactionConfig(include_environment=False),
    )
    assert projected == {"nested": {"value": 1}, "z": 2}
    assert isinstance(projected, dict)


def test_snapshot_removes_url_ports_by_default_and_restores_explicit_opt_in() -> None:
    value = {"endpoint": "https://example.test:8443/mcp", "port": 8443}
    config = RedactionConfig(include_environment=False)
    assert canonical_snapshot(value, config=config) == {"endpoint": "https://example.test/mcp"}
    assert canonical_snapshot(value, include_fields=("port",), config=config) == value


def test_snapshot_cycles_and_opaque_values_fail_closed() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(Exception) as cycle_error:
        canonical_snapshot(cyclic, config=RedactionConfig(include_environment=False))
    assert "self" not in str(cycle_error.value)
    with pytest.raises(Exception):
        canonical_snapshot({"stable": object()}, config=RedactionConfig(include_environment=False))


def test_evaluation_runner_persists_statuses_and_sanitizes_failures() -> None:
    store = InMemoryEvaluationStore()
    runner = EvaluationRunner(store=store)
    seen: list[object] = []

    def passing(context: object) -> bool:
        seen.append(context)
        return True

    runner.register("passing", passing)
    passed = runner.evaluate({"value": 1}, "passing", required=True)
    assert passed.status is EvaluationStatus.PASSED
    assert passed.required is True
    assert store.get(passed.evaluation_id) == passed
    assert seen

    def failing(_context: object) -> bool:
        raise RuntimeError("EVALUATOR_SECRET")

    runner.register("failing", failing)
    failed = runner.evaluate({"value": 1}, "failing", required=False)
    assert failed.status is EvaluationStatus.ERROR
    assert failed.message == "evaluator failed"
    assert "EVALUATOR_SECRET" not in repr(failed)


def test_evaluation_infers_execution_id_from_execution_result_snapshot() -> None:
    execution_id = ExecutionId("execution-evaluation-link")
    trace = TraceResult(trace_id=TraceId("trace-evaluation-link"), execution_id=execution_id)
    result = ExecutionResult(
        snapshot=ExecutionSnapshot(
            execution_id=execution_id,
            created_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            lifecycle=LifecycleState.FINISHED,
            outcome=ExecutionOutcome.COMPLETED,
        ),
        trace=trace,
    )
    artifact = ArtifactRef(
        artifact_id=ArtifactId("artifact-evaluation-link"),
        execution_id=execution_id,
        name="result.json",
        size_bytes=0,
        sha256="0" * 64,
    )
    runner = EvaluationRunner()
    runner.register("same-execution", lambda context: context.execution_id == execution_id)
    evaluation = runner.evaluate(
        result,
        "same-execution",
        trace=trace,
        artifacts=(artifact,),
        evaluation_id="evaluation-distinct-id",
    )
    assert evaluation.evaluation_id.root == "evaluation-distinct-id"
    assert evaluation.context is not None
    assert evaluation.context.execution_id == execution_id
    assert evaluation.context.trace == trace
    assert evaluation.status is EvaluationStatus.PASSED


def test_evaluation_rejects_conflicting_execution_result_and_artifact_ids() -> None:
    result = ExecutionResult(
        snapshot=ExecutionSnapshot(
            execution_id=ExecutionId("execution-result"),
            created_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            lifecycle=LifecycleState.FINISHED,
            outcome=ExecutionOutcome.COMPLETED,
        ),
    )
    artifact = ArtifactRef(
        artifact_id=ArtifactId("artifact-conflicting-id"),
        execution_id=ExecutionId("execution-artifact"),
        name="result.json",
        size_bytes=0,
        sha256="0" * 64,
    )
    runner = EvaluationRunner()
    runner.register("always", lambda _context: True)
    with pytest.raises(ModelValidationError) as error:
        runner.evaluate(result, "always", artifacts=(artifact,))
    assert str(error.value) == "evaluation execution IDs do not match"
    assert "execution-result" not in str(error.value)
    assert "execution-artifact" not in str(error.value)


def test_evaluation_rejects_conflicting_explicit_and_trace_ids() -> None:
    execution_id = ExecutionId("execution-trace")
    runner = EvaluationRunner()
    runner.register("always", lambda _context: True)
    with pytest.raises(ModelValidationError) as error:
        runner.evaluate(
            {},
            "always",
            trace=TraceResult(trace_id=TraceId("trace-conflict"), execution_id=execution_id),
            execution_id="execution-explicit",
        )
    assert str(error.value) == "evaluation execution IDs do not match"
    assert "execution-trace" not in str(error.value)
    assert "execution-explicit" not in str(error.value)


def test_evaluation_rejects_conflicting_subject_and_artifact_ids() -> None:
    runner = EvaluationRunner()
    runner.register("always", lambda _context: True)
    subject = SimpleNamespace(execution_id="execution-subject")
    artifact = ArtifactRef(
        artifact_id=ArtifactId("artifact-subject-conflict"),
        execution_id=ExecutionId("execution-artifact"),
        name="result.json",
        size_bytes=0,
        sha256="0" * 64,
    )
    with pytest.raises(ModelValidationError) as error:
        runner.evaluate(subject, "always", artifacts=(artifact,))
    assert str(error.value) == "evaluation execution IDs do not match"
    assert "execution-subject" not in str(error.value)
    assert "execution-artifact" not in str(error.value)


def test_async_evaluation_rejects_conflicting_result_and_artifact_ids() -> None:
    async def run() -> None:
        result = ExecutionResult(
            snapshot=ExecutionSnapshot(
                execution_id=ExecutionId("execution-subject"),
                created_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                lifecycle=LifecycleState.FINISHED,
                outcome=ExecutionOutcome.COMPLETED,
            ),
        )
        artifact = ArtifactRef(
            artifact_id=ArtifactId("artifact-async-conflict"),
            execution_id=ExecutionId("execution-other"),
            name="result.json",
            size_bytes=0,
            sha256="0" * 64,
        )
        runner = EvaluationRunner()
        runner.register("always", lambda _context: True)
        with pytest.raises(ModelValidationError) as error:
            await runner.evaluate_async(result, "always", artifacts=(artifact,))
        assert str(error.value) == "evaluation execution IDs do not match"
        assert "execution-subject" not in str(error.value)
        assert "execution-other" not in str(error.value)

    asyncio.run(run())


def test_evaluator_receives_immutable_redacted_context_and_unregistered_callable_is_rejected() -> None:
    runner = EvaluationRunner(
        redaction_config=RedactionConfig(secrets=frozenset({"raw-secret"}), include_environment=False)
    )
    captured: list[object] = []

    def evaluator(context: object) -> bool:
        captured.append(context)
        return True

    with pytest.raises(UnsupportedFeature):
        runner.evaluate({"token": "raw-secret"}, evaluator)

    runner.register("immutable", evaluator)
    result = runner.evaluate({"token": "raw-secret", "stable": 1}, "immutable")
    assert result.context is not None
    assert result.context.subject["token"] == REDACTED
    with pytest.raises(TypeError):
        result.context.subject["stable"] = 2


def test_required_failed_or_error_persists_before_outer_failure() -> None:
    store = InMemoryEvaluationStore()
    runner = EvaluationRunner(store=store)
    runner.register("failed", lambda _context: False)
    with pytest.raises(RequiredEvaluationError) as failure:
        runner.evaluate({}, "failed", required=True, evaluation_id="evaluation-required-failed")
    assert failure.value.result.status is EvaluationStatus.FAILED
    assert store.get("evaluation-required-failed") is not None

    def broken(_context: object) -> bool:
        raise RuntimeError("evaluator-secret")

    runner.register("broken", broken)
    with pytest.raises(RequiredEvaluationError) as error:
        runner.evaluate({}, "broken", required=True, evaluation_id="evaluation-required-error")
    assert error.value.result.status is EvaluationStatus.ERROR
    assert "evaluator-secret" not in str(error.value)
    assert store.get("evaluation-required-error") is not None

    runner.register("not-run", lambda _context: EvaluationStatus.NOT_RUN)
    not_run = runner.evaluate({}, "not-run", required=True, evaluation_id="evaluation-required-not-run")
    assert not_run.status is EvaluationStatus.NOT_RUN


def test_sync_and_async_kits_expose_separate_persisted_evaluation_results() -> None:
    with MCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
        kit.register_evaluator("sync", lambda context: True)
        result = kit.evaluate({"answer": "ok"}, "sync")
        assert kit.evaluation_results() == (result,)

    async def run() -> None:
        async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
            kit.register_evaluator("async", lambda context: EvaluationStatus.INCONCLUSIVE)
            result = await kit.evaluate({"answer": "ok"}, "async", required=True)
            assert result.status is EvaluationStatus.INCONCLUSIVE
            assert kit.evaluation_results() == (result,)

            kit.register_evaluator("required-failure", lambda context: False)
            with pytest.raises(RequiredEvaluationError):
                await kit.evaluate({"answer": "bad"}, "required-failure", required=True)
            assert any(item.status is EvaluationStatus.FAILED for item in kit.evaluation_results())

    asyncio.run(run())


def test_async_evaluator_is_awaited_and_persisted() -> None:
    async def run() -> None:
        async with AsyncMCPTestKit(env={}, cwd="/tmp/mcp-pal-no-project") as kit:
            async def evaluate(context: object) -> bool:
                return True

            kit.register_evaluator("async-function", evaluate)
            result = await kit.evaluate({"answer": "ok"}, "async-function")
            assert result.status is EvaluationStatus.PASSED

    asyncio.run(run())
