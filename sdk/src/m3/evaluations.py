"""Evaluator callback registration and persistence contracts."""

from __future__ import annotations

import inspect as _inspect
from collections.abc import (
    Callable as _Callable,
)
from collections.abc import (
    Mapping as _Mapping,
)
from collections.abc import (
    Sequence as _Sequence,
)
from dataclasses import dataclass as _dataclass
from threading import RLock as _RLock
from typing import Any as _Any
from typing import Protocol as _Protocol
from typing import TypeAlias as _TypeAlias
from uuid import uuid4 as _uuid4

from .errors import ModelValidationError as _ModelValidationError
from .errors import UnsupportedFeature as _UnsupportedFeature
from .trace.redaction import (
    RedactionConfig as _RedactionConfig,
)
from .trace.redaction import (
    redact_for_api as _redact_for_api,
)
from .types import (
    ArtifactRef as _ArtifactRef,
)
from .types import (
    EvaluationContext as _EvaluationContext,
)
from .types import (
    EvaluationDecision as _EvaluationDecision,
)
from .types import (
    EvaluationId as _EvaluationId,
)
from .types import (
    EvaluationResult as _EvaluationResult,
)
from .types import (
    EvaluationStatus as _EvaluationStatus,
)
from .types import (
    ExecutionId as _ExecutionId,
)
from .types import (
    TraceResult as _TraceResult,
)
from .types import (
    TurnId as _TurnId,
)

EvaluationVerdict: _TypeAlias = _EvaluationStatus | bool | str | _EvaluationDecision
EvaluationDecision = _EvaluationDecision


class RequiredEvaluationError(AssertionError):
    """A required failed/error evaluation after its result was persisted."""

    def __init__(self, result: _EvaluationResult) -> None:
        self.result = result.model_copy()
        super().__init__(f"required evaluation {result.status.value}")


class Evaluator(_Protocol):
    """An evaluator callback over an immutable context."""

    def __call__(self, context: _EvaluationContext) -> EvaluationVerdict: ...


class AsyncEvaluator(_Protocol):
    async def __call__(self, context: _EvaluationContext) -> EvaluationVerdict: ...


EvaluatorCallable: _TypeAlias = _Callable[[_EvaluationContext], EvaluationVerdict]


@_dataclass(frozen=True, slots=True)
class EvaluatorRegistration:
    """Runtime-only registration; only its stable name is serializable."""

    name: str
    evaluator: EvaluatorCallable | AsyncEvaluator

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("evaluator name must not be empty")
        if not callable(self.evaluator):
            raise TypeError("evaluator must be callable")


class EvaluatorRegistry:
    """Explicit runtime registry for evaluator callables."""

    def __init__(self) -> None:
        self._evaluators: dict[str, _Any] = {}
        self._lock = _RLock()

    def register(self, name: str, evaluator: _Any) -> EvaluatorRegistration:
        registration = EvaluatorRegistration(name=name, evaluator=evaluator)
        with self._lock:
            if registration.name in self._evaluators:
                raise ValueError(f"evaluator already registered: {registration.name}")
            self._evaluators[registration.name] = registration.evaluator
        return registration

    def get(self, name: str) -> _Any:
        with self._lock:
            try:
                return self._evaluators[name]
            except KeyError:
                raise _UnsupportedFeature("evaluator is not registered") from None

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._evaluators))


def _built_in_completed(context: _EvaluationContext) -> _EvaluationDecision:
    def get(value: _Any, key: str, default: _Any = None) -> _Any:
        return (
            value.get(key, default)
            if isinstance(value, _Mapping)
            else getattr(value, key, default)
        )

    snapshot = get(context.subject, "snapshot")
    outcome = get(snapshot, "outcome")
    if outcome is None:
        return _EvaluationDecision(status=_EvaluationStatus.INCONCLUSIVE)
    return _EvaluationDecision(
        status=_EvaluationStatus.PASSED
        if str(getattr(outcome, "value", outcome)) == "completed"
        else _EvaluationStatus.FAILED
    )


def _built_in_tool_succeeded(context: _EvaluationContext) -> _EvaluationDecision:
    subject = context.subject
    is_error = (
        subject.get("is_error")
        if isinstance(subject, _Mapping)
        else getattr(subject, "is_error", None)
    )
    if is_error is None:
        return _EvaluationDecision(status=_EvaluationStatus.NOT_RUN)
    return _EvaluationDecision(
        status=_EvaluationStatus.FAILED if bool(is_error) else _EvaluationStatus.PASSED
    )


def _built_in_has_text(context: _EvaluationContext) -> _EvaluationDecision:
    subject = context.subject

    def get(value: _Any, key: str, default: _Any = None) -> _Any:
        return (
            value.get(key, default)
            if isinstance(value, _Mapping)
            else getattr(value, key, default)
        )

    candidates: list[_Any] = [get(subject, "text")]
    response = get(subject, "response")
    candidates.append(get(response, "text"))
    direct = get(subject, "direct_result")
    candidates.extend([get(direct, "text"), get(get(direct, "response"), "text")])
    turns = get(subject, "turns", ()) or ()
    candidates.extend(get(get(turn, "response"), "text") for turn in turns)
    # MCP content blocks often carry text under a nested ``text`` key.
    for value in (get(subject, "content", ()), get(direct, "content", ())):
        if isinstance(value, (list, tuple)):
            candidates.extend(get(item, "text") for item in value)
    if isinstance(subject, str):
        candidates.append(subject)
    applicable = any(value is not None for value in candidates)
    if not applicable:
        return _EvaluationDecision(status=_EvaluationStatus.NOT_RUN)
    return _EvaluationDecision(
        status=_EvaluationStatus.PASSED
        if any(isinstance(value, str) and value.strip() for value in candidates)
        else _EvaluationStatus.FAILED
    )


def register_builtin_evaluators(registry: EvaluatorRegistry) -> None:
    """Install the reserved deterministic evaluators on a runtime registry."""
    for name, callback in (
        ("m3.execution.completed.v1", _built_in_completed),
        ("m3.tool_call.succeeded.v1", _built_in_tool_succeeded),
        ("m3.output.has_text.v1", _built_in_has_text),
    ):
        if name not in registry.names():
            registry.register(name, callback)


class EvaluationStore(_Protocol):
    def save(self, result: _EvaluationResult) -> None: ...

    def get(self, evaluation_id: _EvaluationId | str) -> _EvaluationResult | None: ...

    def all(self) -> tuple[_EvaluationResult, ...]: ...


class InMemoryEvaluationStore:
    """Small independent store; evaluations never rewrite execution state."""

    def __init__(self) -> None:
        self._results: dict[str, _EvaluationResult] = {}
        self._lock = _RLock()

    def save(self, result: _EvaluationResult) -> None:
        key = result.evaluation_id.root
        with self._lock:
            if key in self._results:
                raise ValueError("evaluation already exists")
            self._results[key] = result.model_copy()

    def get(self, evaluation_id: _EvaluationId | str) -> _EvaluationResult | None:
        key = (
            evaluation_id.root
            if isinstance(evaluation_id, _EvaluationId)
            else evaluation_id
        )
        with self._lock:
            result = self._results.get(key)
        return result.model_copy() if result is not None else None

    def all(self) -> tuple[_EvaluationResult, ...]:
        with self._lock:
            return tuple(
                self._results[key].model_copy() for key in sorted(self._results)
            )


def _status(value: EvaluationVerdict) -> _EvaluationStatus:
    if isinstance(value, _EvaluationDecision):
        return value.status
    if isinstance(value, bool):
        return _EvaluationStatus.PASSED if value else _EvaluationStatus.FAILED
    if isinstance(value, _EvaluationStatus):
        return value
    if isinstance(value, str):
        try:
            return _EvaluationStatus(value)
        except ValueError:
            pass
    raise ValueError("evaluator must return a valid evaluation status or bool")


def _raise_for_required(result: _EvaluationResult) -> None:
    if result.required and result.status in {
        _EvaluationStatus.FAILED,
        _EvaluationStatus.ERROR,
    }:
        raise RequiredEvaluationError(result)


def _consistent_execution_id(
    subject: _Any,
    trace: _TraceResult | None,
    artifacts: _Sequence[_ArtifactRef],
    explicit: _ExecutionId | str | None,
) -> _ExecutionId | None:
    """Resolve execution identity and reject conflicting evidence safely."""

    def coerce(value: _Any) -> _ExecutionId | None:
        if isinstance(value, _ExecutionId):
            return value
        if isinstance(value, str):
            return _ExecutionId(value)
        return None

    try:
        candidates: list[_ExecutionId] = []
        explicit_id = coerce(explicit)
        if explicit_id is not None:
            candidates.append(explicit_id)
        if trace is not None:
            candidates.append(trace.execution_id)
        subject_trace = getattr(subject, "trace", None)
        if subject_trace is not None:
            subject_trace_id = coerce(getattr(subject_trace, "execution_id", None))
            if subject_trace_id is not None:
                candidates.append(subject_trace_id)
        snapshot = getattr(subject, "snapshot", None)
        snapshot_id = coerce(getattr(snapshot, "execution_id", None))
        if snapshot_id is not None:
            candidates.append(snapshot_id)
        subject_id = coerce(getattr(subject, "execution_id", None))
        if subject_id is not None:
            candidates.append(subject_id)
        for artifact in artifacts:
            artifact_id = coerce(artifact.execution_id)
            if artifact_id is not None:
                candidates.append(artifact_id)
    except BaseException:
        raise _ModelValidationError(
            "evaluation execution identity is invalid",
            details={"operation": "evaluation"},
        ) from None
    unique = {item.root for item in candidates}
    if len(unique) > 1:
        raise _ModelValidationError(
            "evaluation execution IDs do not match",
            details={"operation": "evaluation"},
        )
    return candidates[0] if candidates else None


def _subject_context(
    subject: _Any,
    *,
    goal: str | None,
    trace: _TraceResult | None,
    artifacts: _Sequence[_ArtifactRef],
    metadata: _Mapping[str, str | int | float | bool | None],
    config: _RedactionConfig,
    execution_id: _ExecutionId | str | None,
    turn_id: _TurnId | str | None,
    case_id: str | None,
) -> _EvaluationContext:
    declared_kind = getattr(subject, "kind", None)
    if not isinstance(declared_kind, str) or not declared_kind:
        declared_kind = type(subject).__name__ if subject is not None else "unknown"
        if declared_kind in {"dict", "list", "tuple"}:
            declared_kind = "json"
    inferred_turn = getattr(subject, "turn_id", None)
    if inferred_turn is None:
        snapshot = getattr(subject, "snapshot", None)
        inferred_turn = getattr(snapshot, "turn_id", None)
    if turn_id is not None and inferred_turn is not None:
        explicit_turn = turn_id if isinstance(turn_id, _TurnId) else _TurnId(turn_id)
        if explicit_turn != inferred_turn:
            raise _ModelValidationError(
                "evaluation turn IDs do not match", details={"operation": "evaluation"}
            )
    resolved_turn = (
        turn_id
        if isinstance(turn_id, _TurnId)
        else _TurnId(turn_id)
        if turn_id is not None
        else inferred_turn
    )
    resolved_execution_id = _consistent_execution_id(
        subject, trace, artifacts, execution_id
    )
    # EvaluationContext is a frozen model and recursively freezes its mapping
    # inputs.  Redacting first also prevents evaluator inputs from retaining a
    # secret-bearing representation supplied by a hostile subject model.
    projected = _redact_for_api(
        {
            "subject": subject,
            "subject_kind": declared_kind,
            "execution_id": resolved_execution_id,
            "case_id": case_id,
            "turn_id": resolved_turn,
            "goal": goal,
            "trace": trace,
            "artifacts": tuple(artifacts),
            "metadata": metadata,
        },
        path="$.evaluation",
        config=config,
    )
    return _EvaluationContext.model_validate(projected)


class EvaluationRunner:
    """Run and persist deterministic evaluations in a separate store."""

    def __init__(
        self,
        *,
        registry: EvaluatorRegistry | None = None,
        store: EvaluationStore | None = None,
        durable_store: _Any | None = None,
        redaction_config: _RedactionConfig | None = None,
    ) -> None:
        self.registry = registry or EvaluatorRegistry()
        register_builtin_evaluators(self.registry)
        self.store = store or InMemoryEvaluationStore()
        self.durable_store = durable_store
        self.redaction_config = redaction_config or _RedactionConfig.from_environment()

    def register(
        self, name: str, evaluator: EvaluatorCallable | AsyncEvaluator
    ) -> EvaluatorRegistration:
        return self.registry.register(name, evaluator)

    def evaluate(
        self,
        subject: _Any,
        evaluator: str | EvaluatorCallable,
        *,
        required: bool = False,
        goal: str | None = None,
        trace: _TraceResult | None = None,
        artifacts: _Sequence[_ArtifactRef] = (),
        metadata: _Mapping[str, str | int | float | bool | None] | None = None,
        evaluation_id: _EvaluationId | str | None = None,
        execution_id: _ExecutionId | str | None = None,
        turn_id: _TurnId | str | None = None,
        case_id: str | None = None,
    ) -> _EvaluationResult:
        if isinstance(evaluator, str):
            name = evaluator
            callback = self.registry.get(evaluator)
        else:
            registered = next(
                (
                    name
                    for name in self.registry.names()
                    if self.registry.get(name) is evaluator
                ),
                None,
            )
            if registered is None:
                raise _UnsupportedFeature(
                    "evaluator callables must be registered before evaluation"
                )
            name = registered
            callback = evaluator
        context = _subject_context(
            subject,
            goal=goal,
            trace=trace,
            artifacts=artifacts,
            metadata=metadata or {},
            config=self.redaction_config,
            execution_id=execution_id,
            turn_id=turn_id,
            case_id=case_id,
        )
        identifier = (
            evaluation_id
            if isinstance(evaluation_id, _EvaluationId)
            else _EvaluationId(evaluation_id or f"evaluation-{_uuid4().hex}")
        )
        message: str | None = None
        score = None
        rationale = None
        metrics: dict[str, float] = {}
        provenance = None
        try:
            raw = callback(context)
        except Exception:
            status = _EvaluationStatus.ERROR
            message = "evaluator failed"
        else:
            if _inspect.isawaitable(raw):
                close = getattr(raw, "close", None)
                if callable(close):
                    close()
                status = _EvaluationStatus.ERROR
                message = "async evaluator requires the async evaluation API"
            else:
                try:
                    status = _status(raw)
                    if isinstance(raw, _EvaluationDecision):
                        score, rationale, metrics, provenance = (
                            raw.score,
                            raw.rationale,
                            dict(raw.metrics),
                            raw.provenance,
                        )
                except Exception:
                    status = _EvaluationStatus.ERROR
                    message = "evaluator failed"
        result = _EvaluationResult(
            evaluation_id=identifier,
            name=name,
            status=status,
            required=required,
            message=message,
            context=context,
            score=score,
            rationale=rationale,
            metrics=metrics,
            provenance=provenance,
        )
        self._persist(result)
        _raise_for_required(result)
        return result.model_copy()

    async def evaluate_async(
        self,
        subject: _Any,
        evaluator: str | _Any,
        *,
        required: bool = False,
        goal: str | None = None,
        trace: _TraceResult | None = None,
        artifacts: _Sequence[_ArtifactRef] = (),
        metadata: _Mapping[str, str | int | float | bool | None] | None = None,
        evaluation_id: _EvaluationId | str | None = None,
        execution_id: _ExecutionId | str | None = None,
        turn_id: _TurnId | str | None = None,
        case_id: str | None = None,
    ) -> _EvaluationResult:
        if isinstance(evaluator, str):
            name = evaluator
            callback = self.registry.get(evaluator)
        else:
            registered = next(
                (
                    name
                    for name in self.registry.names()
                    if self.registry.get(name) is evaluator
                ),
                None,
            )
            if registered is None:
                raise _UnsupportedFeature(
                    "evaluator callables must be registered before evaluation"
                )
            name = registered
            callback = evaluator
        context = _subject_context(
            subject,
            goal=goal,
            trace=trace,
            artifacts=artifacts,
            metadata=metadata or {},
            config=self.redaction_config,
            execution_id=execution_id,
            turn_id=turn_id,
            case_id=case_id,
        )
        identifier = (
            evaluation_id
            if isinstance(evaluation_id, _EvaluationId)
            else _EvaluationId(evaluation_id or f"evaluation-{_uuid4().hex}")
        )
        message: str | None = None
        score: float | None = None
        rationale: str | None = None
        metrics: dict[str, float] = {}
        provenance: _Any = None
        try:
            raw = callback(context)
            if _inspect.isawaitable(raw):
                raw = await raw
            status = _status(raw)
            score = raw.score if isinstance(raw, _EvaluationDecision) else None
            rationale = raw.rationale if isinstance(raw, _EvaluationDecision) else None
            metrics = dict(raw.metrics) if isinstance(raw, _EvaluationDecision) else {}
            provenance = (
                raw.provenance if isinstance(raw, _EvaluationDecision) else None
            )
        except Exception:
            status = _EvaluationStatus.ERROR
            message = "evaluator failed"
            score = rationale = provenance = None
            metrics = {}
        result = _EvaluationResult(
            evaluation_id=identifier,
            name=name,
            status=status,
            required=required,
            message=message,
            context=context,
            score=score,
            rationale=rationale,
            metrics=metrics,
            provenance=provenance,
        )
        self._persist(result)
        _raise_for_required(result)
        return result.model_copy()

    def results(self) -> tuple[_EvaluationResult, ...]:
        return self.store.all()

    def _persist(self, result: _EvaluationResult) -> None:
        execution_id = (
            result.context.execution_id if result.context is not None else None
        )
        self.store.save(result)
        save_evaluation = getattr(self.durable_store, "save_evaluation", None)
        if callable(save_evaluation) and execution_id is not None:
            save_evaluation(
                execution_id,
                result,
                turn_id=result.context.turn_id if result.context else None,
            )


__all__ = [  # noqa: RUF022 - public API order is compatibility-checked
    "AsyncEvaluator",
    "EvaluationDecision",
    "EvaluationRunner",
    "EvaluationStore",
    "EvaluationVerdict",
    "Evaluator",
    "EvaluatorCallable",
    "EvaluatorRegistry",
    "EvaluatorRegistration",
    "InMemoryEvaluationStore",
    "RequiredEvaluationError",
    "register_builtin_evaluators",
]
