from __future__ import annotations

from collections.abc import Mapping as _Mapping
from datetime import datetime as _datetime
from math import isfinite as _isfinite
from typing import Any as _Any

from pydantic import Field as _Field
from pydantic import field_validator as _field_validator

from .base import (
    EvaluationId,
    EvaluationStatus,
    ExecutionId,
    FrozenModel,
    RunId,
    SuiteId,
    TurnId,
    _utc_now,
)
from .events import ArtifactRef, TraceResult


class EvaluationContext(FrozenModel):
    subject: _Any = None
    subject_kind: str = "unknown"
    execution_id: ExecutionId | None = None
    suite_id: SuiteId | None = None
    suite_name: str | None = None
    case_id: str | None = _Field(default=None, min_length=1, max_length=256)
    turn_id: TurnId | None = None
    goal: str | None = None
    trace: TraceResult | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    metadata: _Mapping[str, str | int | float | bool | None] = _Field(
        default_factory=dict
    )


class EvaluationSource(FrozenModel):
    """Optional, redaction-safe provenance for a structured judgment."""

    kind: str = _Field(min_length=1, max_length=128)
    provider: str | None = _Field(default=None, max_length=256)
    model: str | None = _Field(default=None, max_length=256)
    rubric_id: str | None = _Field(default=None, max_length=256)
    rubric_version: str | None = _Field(default=None, max_length=128)
    config_digest: str | None = _Field(default=None, max_length=256)


class EvaluationDecision(FrozenModel):
    """Structured evaluator output, compatible with scalar verdicts."""

    status: EvaluationStatus
    score: float | None = None
    rationale: str | None = None
    metrics: _Mapping[str, float] = _Field(default_factory=dict)
    provenance: EvaluationSource | None = None

    @_field_validator("score", mode="before")
    @classmethod
    def _finite_score(cls, value: _Any) -> float | None:
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not _isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise ValueError("score must be finite and between 0 and 1")
        return float(value) if value is not None else None

    @_field_validator("metrics", mode="before")
    @classmethod
    def _finite_metrics(cls, value: _Any) -> _Mapping[str, float]:
        if not isinstance(value, _Mapping):
            raise ValueError("metrics must be a mapping")
        if any(not key.strip() for key in value):
            raise ValueError("metric names must not be empty")
        if any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not _isfinite(float(item))
            for item in value.values()
        ):
            raise ValueError("metric values must be finite")
        return {key: float(item) for key, item in value.items()}


class EvaluationResult(FrozenModel):
    evaluation_id: EvaluationId
    name: str
    status: EvaluationStatus
    required: bool = False
    message: str | None = None
    context: EvaluationContext | None = None
    score: float | None = None
    rationale: str | None = None
    metrics: _Mapping[str, float] = _Field(default_factory=dict)
    provenance: EvaluationSource | None = None
    # Structured, redaction-safe details emitted by M3 matchers or a
    # user evaluator.  The mapping is intentionally open so old stores and
    # framework-specific checks remain forward compatible.
    details: _Mapping[str, _Any] = _Field(default_factory=dict)

    @_field_validator("score", mode="before")
    @classmethod
    def _strict_result_score(cls, value: _Any) -> float | None:
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not _isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise ValueError("score must be finite and between 0 and 1")
        return float(value)


class EvaluationRecord(FrozenModel):
    """Compact durable evaluation row linked to an execution report."""

    evaluation_id: EvaluationId
    execution_id: ExecutionId
    suite_id: SuiteId | None = None
    suite_name: str | None = None
    case_id: str | None = _Field(default=None, min_length=1, max_length=256)
    turn_id: TurnId | None = None
    name: str
    status: EvaluationStatus
    required: bool = False
    message: str | None = None
    score: float | None = None
    rationale: str | None = None
    metrics: _Mapping[str, float] = _Field(default_factory=dict)
    provenance: EvaluationSource | None = None
    details: _Mapping[str, _Any] = _Field(default_factory=dict)
    goal: str | None = None
    metadata: _Mapping[str, str | int | float | bool | None] = _Field(
        default_factory=dict
    )
    subject_kind: str = "unknown"
    subject_digest: str | None = _Field(default=None, pattern=r"^[0-9a-f]{64}$")
    run_id: RunId | None = None
    created_at: _datetime = _Field(default_factory=_utc_now)


__all__ = [
    "EvaluationContext",
    "EvaluationDecision",
    "EvaluationRecord",
    "EvaluationResult",
    "EvaluationSource",
]
