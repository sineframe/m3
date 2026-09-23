from __future__ import annotations

from datetime import datetime as _datetime

from pydantic import Field as _Field
from pydantic import model_validator as _model_validator

from ..errors import (
    InvalidTransitionError as _InvalidTransitionError,
)
from ..errors import (
    ModelValidationError as _ModelValidationError,
)
from .agent_identity import AgentIdentity
from .base import (
    ExecutionId,
    ExecutionOutcome,
    ExecutionStatus,
    FrozenModel,
    ProjectId,
    RunId,
    SessionId,
    SuiteId,
    TurnId,
    TurnOutcome,
    TurnStatus,
    _utc_now,
)
from .specs import SessionSource


class ExecutionState(FrozenModel):
    execution_id: ExecutionId
    project_id: ProjectId | None = None
    run_id: RunId | None = None
    suite_id: SuiteId | None = None
    suite_name: str | None = _Field(default=None, min_length=1, max_length=256)
    lifecycle: ExecutionStatus = ExecutionStatus.CREATED
    outcome: ExecutionOutcome | None = None
    sequence: int = _Field(default=0, ge=0)
    tool_call_count: int = _Field(default=0, ge=0)
    created_at: _datetime = _Field(default_factory=_utc_now)
    finished_at: _datetime | None = None
    provenance: SessionSource | None = None
    agent: AgentIdentity | None = None

    @_model_validator(mode="after")
    def _terminal_consistency(self) -> ExecutionState:
        if self.lifecycle is ExecutionStatus.FINISHED and self.outcome is None:
            raise ValueError("finished execution requires an outcome")
        if self.lifecycle is ExecutionStatus.FINISHED and self.finished_at is None:
            raise ValueError("finished execution requires finished_at")
        if self.lifecycle is not ExecutionStatus.FINISHED and self.outcome is not None:
            raise ValueError("non-finished execution cannot have an outcome")
        if (
            self.lifecycle is not ExecutionStatus.FINISHED
            and self.finished_at is not None
        ):
            raise ValueError("non-finished execution cannot have finished_at")
        return self

    def transition(
        self, lifecycle: ExecutionStatus, outcome: ExecutionOutcome | None = None
    ) -> ExecutionState:
        lifecycle = ExecutionStatus(lifecycle)
        outcome = ExecutionOutcome(outcome) if outcome is not None else None
        transitions = {
            ExecutionStatus.CREATED: {
                ExecutionStatus.QUEUED,
                ExecutionStatus.STARTING,
                ExecutionStatus.FINISHED,
            },
            ExecutionStatus.QUEUED: {
                ExecutionStatus.STARTING,
                ExecutionStatus.FINISHED,
            },
            ExecutionStatus.STARTING: {
                ExecutionStatus.IDLE,
                ExecutionStatus.RUNNING_TURN,
                ExecutionStatus.CLOSING,
                ExecutionStatus.FINISHED,
            },
            ExecutionStatus.IDLE: {
                ExecutionStatus.RUNNING_TURN,
                ExecutionStatus.CLOSING,
                ExecutionStatus.FINISHED,
            },
            ExecutionStatus.RUNNING_TURN: {
                ExecutionStatus.IDLE,
                ExecutionStatus.WAITING_FOR_INPUT,
                ExecutionStatus.CLOSING,
                ExecutionStatus.FINISHED,
            },
            ExecutionStatus.WAITING_FOR_INPUT: {
                ExecutionStatus.RUNNING_TURN,
                ExecutionStatus.CLOSING,
                ExecutionStatus.FINISHED,
            },
            ExecutionStatus.CLOSING: {ExecutionStatus.FINISHED},
            ExecutionStatus.FINISHED: set(),
        }
        if lifecycle not in transitions[self.lifecycle]:
            raise _InvalidTransitionError(
                f"execution cannot transition {self.lifecycle.value} → {lifecycle.value}"
            )
        if lifecycle is not ExecutionStatus.FINISHED and outcome is not None:
            raise _ModelValidationError("non-finished execution cannot have an outcome")
        if lifecycle is ExecutionStatus.FINISHED and outcome is None:
            raise _ModelValidationError("finished execution requires an outcome")
        values = self.model_dump(mode="python")
        values.update(
            lifecycle=lifecycle,
            outcome=outcome,
            sequence=self.sequence + 1,
            finished_at=_utc_now()
            if lifecycle is ExecutionStatus.FINISHED
            else self.finished_at,
        )
        return type(self).model_validate(values)


class ExecutionPage(FrozenModel):
    """Bounded, stable page of persisted execution snapshots."""

    items: tuple[ExecutionState, ...] = ()
    limit: int = _Field(default=50, ge=1, le=100)
    offset: int = _Field(default=0, ge=0)
    total: int = _Field(default=0, ge=0)


class TurnState(FrozenModel):
    turn_id: TurnId
    session_id: SessionId
    number: int = _Field(ge=1)
    lifecycle: TurnStatus = TurnStatus.QUEUED
    outcome: TurnOutcome | None = None
    created_at: _datetime = _Field(default_factory=_utc_now)
    finished_at: _datetime | None = None

    @_model_validator(mode="after")
    def _terminal_consistency(self) -> TurnState:
        if self.lifecycle is TurnStatus.FINISHED and self.outcome is None:
            raise ValueError("finished turn requires an outcome")
        if self.lifecycle is TurnStatus.FINISHED and self.finished_at is None:
            raise ValueError("finished turn requires finished_at")
        if self.lifecycle is not TurnStatus.FINISHED and self.outcome is not None:
            raise ValueError("non-finished turn cannot have an outcome")
        if self.lifecycle is not TurnStatus.FINISHED and self.finished_at is not None:
            raise ValueError("non-finished turn cannot have finished_at")
        return self

    def transition(
        self, lifecycle: TurnStatus, outcome: TurnOutcome | None = None
    ) -> TurnState:
        lifecycle = TurnStatus(lifecycle)
        outcome = TurnOutcome(outcome) if outcome is not None else None
        transitions = {
            TurnStatus.QUEUED: {TurnStatus.RUNNING, TurnStatus.FINISHED},
            TurnStatus.RUNNING: {TurnStatus.FINISHED},
            TurnStatus.FINISHED: set(),
        }
        if lifecycle not in transitions[self.lifecycle]:
            raise _InvalidTransitionError(
                f"turn cannot transition {self.lifecycle.value} → {lifecycle.value}"
            )
        if lifecycle is not TurnStatus.FINISHED and outcome is not None:
            raise _ModelValidationError("non-finished turn cannot have an outcome")
        if lifecycle is TurnStatus.FINISHED and outcome is None:
            raise _ModelValidationError("finished turn requires an outcome")
        values = self.model_dump(mode="python")
        values.update(
            lifecycle=lifecycle,
            outcome=outcome,
            finished_at=_utc_now()
            if lifecycle is TurnStatus.FINISHED
            else self.finished_at,
        )
        return type(self).model_validate(values)


__all__ = ["ExecutionPage", "ExecutionState", "TurnState"]
